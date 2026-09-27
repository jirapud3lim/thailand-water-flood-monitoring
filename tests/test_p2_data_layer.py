"""Phase 2: TTL config, LRU cache, national flood-points, alerts_only, ETag, resilience."""

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.config import DEFAULT_REFRESH_TTL, Settings
from backend.app.main import app
from backend.app.services import http as http_mod
from backend.app.services import upstreams
from backend.app.services.cache import AsyncTTLCache, cache, payload_version
from backend.app.services.freshness import BANGKOK_TZ

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def _point(lon, lat, severity, **props):
    now = datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M")
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"severity": severity, "observed_at": now, **props}}


# ---------------------------------------------------------------- 2.2 TTL config

def test_ttl_defaults_overrides_and_floor(monkeypatch) -> None:
    monkeypatch.setenv("REFRESH_TTL", '{"thaiwater-canal": 60, "radar": 5}')
    settings = Settings(_env_file=None)
    assert settings.ttl("thaiwater-canal") == 60                       # override
    assert settings.ttl("dams") == DEFAULT_REFRESH_TTL["dams"]          # untouched default kept
    assert settings.ttl("radar") == 30                                  # floor guards typos
    assert settings.ttl("unknown-source") == settings.cache_ttl_seconds


# ---------------------------------------------------------------- 2.1 cache

def test_cache_lru_eviction_and_injected_clock() -> None:
    now = [1000.0]
    local = AsyncTTLCache(max_entries=2, clock=lambda: now[0])

    async def value(v):
        return v

    async def run():
        await local.load("a", lambda: value(1), 60)
        await local.load("b", lambda: value(2), 60)
        await local.load("a", lambda: value(1), 60)       # touch a -> b is least recent
        await local.load("c", lambda: value(3), 60)       # evicts b
        assert set(local._entries) == {"a", "c"}
        now[0] += 61                                        # expire without sleeping
        result = await local.load("a", lambda: value(9), 60)
        assert result.status == "live" and result.value == 9

    asyncio.run(run())


def test_payload_version_tracks_content_only() -> None:
    assert payload_version({"a": 1, "b": [1, 2]}) == payload_version({"b": [1, 2], "a": 1})
    assert payload_version({"a": 1}) != payload_version({"a": 2})


# ---------------------------------------------------------------- flood-points national + alerts_only

def test_flood_points_fetch_once_filter_per_viewport(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake(settings):
        calls["n"] += 1
        return {"type": "FeatureCollection", "features": [
            _point(100.5, 13.7, "normal"), _point(100.6, 13.8, "high"), _point(99.0, 18.8, "low"),
        ]}

    monkeypatch.setattr(routes, "fetch_flood_points", fake)
    bkk = {"min_lon": 100, "min_lat": 13, "max_lon": 101, "max_lat": 14}
    all_bkk = client.get("/api/v1/flood-points", params=bkk).json()
    alerts_bkk = client.get("/api/v1/flood-points", params={**bkk, "alerts_only": "true"}).json()
    north = client.get("/api/v1/flood-points", params={"min_lon": 98, "min_lat": 18, "max_lon": 100, "max_lat": 20}).json()

    assert calls["n"] == 1
    assert all_bkk["meta"]["total_in_bbox"] == 2 and all_bkk["meta"]["total_national"] == 3
    assert [f["properties"]["severity"] for f in alerts_bkk["data"]["features"]] == ["high"]
    assert alerts_bkk["meta"]["returned_count"] == 1 and alerts_bkk["meta"]["total_in_bbox"] == 2
    assert len(north["data"]["features"]) == 1


def test_thaiwater_serve_time_age_check_drops_expired(monkeypatch) -> None:
    old = (datetime.now(BANGKOK_TZ) - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")

    async def fake(layer, settings):
        fresh = _point(100.5, 13.7, "high")
        expired = _point(100.6, 13.8, "critical")
        expired["properties"]["observed_at"] = old          # canal max age is 6 h
        return {"type": "FeatureCollection", "features": [fresh, expired]}

    monkeypatch.setattr(routes, "fetch_thaiwater_layer", fake)
    body = client.get("/api/v1/thaiwater/canal").json()
    assert body["meta"]["total_national"] == 1
    assert [f["properties"]["severity"] for f in body["data"]["features"]] == ["high"]


# ---------------------------------------------------------------- 2.4 ETag / 304

def test_etag_304_then_200_when_data_changes(monkeypatch) -> None:
    payload = {"host": "h", "radar": {"past": [{"time": 1, "path": "/a"}]}}

    async def fake(settings):
        return payload

    monkeypatch.setattr(routes, "fetch_radar", fake)
    first = client.get("/api/v1/radar/latest")
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "no-cache" and first.json()["meta"]["version"]

    again = client.get("/api/v1/radar/latest", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""

    cache.clear()
    payload["radar"]["past"].append({"time": 2, "path": "/b"})
    changed = client.get("/api/v1/radar/latest", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_etag_differs_per_viewport(monkeypatch) -> None:
    async def fake(settings):
        return {"type": "FeatureCollection", "features": [_point(100.5, 13.7, "high")]}

    monkeypatch.setattr(routes, "fetch_flood_points", fake)
    a = client.get("/api/v1/flood-points", params={"min_lon": 100, "min_lat": 13, "max_lon": 101, "max_lat": 14})
    b = client.get("/api/v1/flood-points", params={"min_lon": 98, "min_lat": 18, "max_lon": 99, "max_lat": 19},
                   headers={"If-None-Match": a.headers["etag"]})
    assert b.status_code == 200  # a different viewport must never be answered with 304


def test_large_responses_are_gzipped(monkeypatch) -> None:
    async def fake(layer, settings):
        return {"type": "FeatureCollection",
                "features": [_point(100 + i / 1000, 13.5, "high", name=f"s{i}") for i in range(300)]}

    monkeypatch.setattr(routes, "fetch_thaiwater_layer", fake)
    response = client.get("/api/v1/thaiwater/rain", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


# ---------------------------------------------------------------- 2.5 resilience

def test_arcgis_pagination_follows_exceeded_limit(monkeypatch) -> None:
    pages = [
        {"features": [{"id": i} for i in range(2000)], "properties": {"exceededTransferLimit": True}},
        {"features": [{"id": 2000 + i} for i in range(5)]},
    ]
    offsets = []

    async def fake_get(url, settings, *, params=None, headers=None):
        offsets.append(params["resultOffset"])
        return pages[len(offsets) - 1]

    monkeypatch.setattr(upstreams, "_get_json", fake_get)
    result = asyncio.run(upstreams._get_arcgis_all("u", Settings(_env_file=None), {}))
    assert offsets == [0, 2000] and len(result["features"]) == 2005 and not result["truncated"]


def test_arcgis_pagination_stops_if_server_ignores_offset(monkeypatch) -> None:
    page = {"features": [{"id": i} for i in range(2000)], "exceededTransferLimit": True}

    async def fake_get(url, settings, *, params=None, headers=None):
        return page

    monkeypatch.setattr(upstreams, "_get_json", fake_get)
    result = asyncio.run(upstreams._get_arcgis_all("u", Settings(_env_file=None), {}))
    assert len(result["features"]) == 2000  # no infinite loop, no duplicates


def test_flood_points_partial_when_one_feed_fails(monkeypatch) -> None:
    async def fake_all(url, settings, params):
        if url == upstreams.DPM_ROAD_FLOOD_URL:
            raise httpx.ConnectError("road feed down")
        return {"features": [{"geometry": {"type": "Point", "coordinates": [100, 15]},
                              "properties": {"STATION_DISPLAY_NAME": "P.1", "WATER_LEVEL_MSL": 1, "BANK_LEVEL": 3}}]}

    monkeypatch.setattr(upstreams, "_get_arcgis_all", fake_all)
    result = asyncio.run(upstreams.fetch_flood_points(Settings(_env_file=None)))
    assert result["partial"] and "road" in result["source_errors"]
    assert len(result["features"]) == 1

    monkeypatch.setattr(routes, "fetch_flood_points", upstreams.fetch_flood_points)
    assert client.get("/api/v1/flood-points").json()["meta"]["status"] == "partial"


def test_flood_points_raise_when_all_feeds_fail(monkeypatch) -> None:
    async def down(url, settings, params):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(upstreams, "_get_arcgis_all", down)
    with pytest.raises(RuntimeError):
        asyncio.run(upstreams.fetch_flood_points(Settings(_env_file=None)))


def test_dams_report_unmatched_instead_of_dropping_silently() -> None:
    rid = {"date": "2026-09-27", "data": [{"region": "เหนือ", "dam": [
        {"name": "เขื่อนมีพิกัด", "percent_storage": 50}, {"name": "เขื่อนไม่มีพิกัด", "percent_storage": 60},
    ]}]}
    geo = {"features": [{"geometry": {"type": "Point", "coordinates": [100, 15]}, "properties": {"name": "เขื่อนมีพิกัด"}}]}
    result = upstreams.normalize_dams(rid, geo)
    assert len(result["features"]) == 1 and result["unmatched"] == ["เขื่อนไม่มีพิกัด"]


@pytest.mark.parametrize(
    ("responses", "expect_ok", "expect_calls"),
    [
        ([503, 200], True, 2),      # transient 5xx retried
        ([429, 200], True, 2),      # rate limit retried
        ([404, 200], False, 1),     # client error never retried
        ([503, 503], False, 2),     # retries bounded (upstream_retries=1)
    ],
)
def test_http_retry_policy(monkeypatch, responses, expect_ok, expect_calls) -> None:
    calls = {"n": 0}

    def handler(request):
        status = responses[calls["n"]]
        calls["n"] += 1
        return httpx.Response(status, json={"ok": status == 200})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(http_mod.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(http_mod.asyncio, "sleep", no_sleep)
    settings = Settings(_env_file=None)
    if expect_ok:
        assert asyncio.run(http_mod.get_json("https://x.test/a", settings)) == {"ok": True}
    else:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(http_mod.get_json("https://x.test/a", settings))
    assert calls["n"] == expect_calls


def test_sources_status_reports_ttl_and_counters(monkeypatch) -> None:
    async def fake(settings):
        return {"host": "h", "radar": {"past": []}}

    monkeypatch.setattr(routes, "fetch_radar", fake)
    client.get("/api/v1/radar/latest")
    client.get("/api/v1/radar/latest")
    radar = next(s for s in client.get("/api/v1/sources/status").json()["sources"] if s["id"] == "radar")
    assert radar["ttl_seconds"] == DEFAULT_REFRESH_TTL["radar"]
    assert (radar["misses"], radar["hits"]) == (1, 1)
