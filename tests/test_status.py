import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.main import app
from backend.app.services.cache import AsyncTTLCache, cache
from backend.app.services.freshness import max_observed, parse_observed
from backend.app.services.upstreams import dam_storage_status


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_cache():
    cache._entries.clear()
    cache._health.clear()
    yield
    cache._entries.clear()
    cache._health.clear()


# ---------------------------------------------------------------- cache statuses

@pytest.mark.anyio
async def test_cache_reports_live_cached_then_stale() -> None:
    local = AsyncTTLCache()
    calls = {"n": 0}

    async def ok():
        calls["n"] += 1
        return {"v": calls["n"]}

    async def boom():
        raise RuntimeError("upstream down")

    first = await local.load("k", ok, ttl_seconds=60)
    second = await local.load("k", ok, ttl_seconds=60)
    assert (first.status, second.status, calls["n"]) == ("live", "cached", 1)

    local._entries["k"].expires_at = 0  # force expiry
    stale = await local.load("k", boom, ttl_seconds=60)
    assert stale.status == "stale" and stale.value == {"v": 1}
    health = local.health()["k"]
    assert health["failing"] and "upstream down" in health["last_error"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------- route meta / errors

def test_response_has_meta_and_legacy_shape(monkeypatch) -> None:
    async def fake_radar(settings):
        return {"host": "h", "radar": {"past": [{"time": 1790488800, "path": "/p"}]}}

    monkeypatch.setattr(routes, "fetch_radar", fake_radar)
    first = client.get("/api/v1/radar/latest").json()
    second = client.get("/api/v1/radar/latest").json()
    assert first["stale"] is False and first["data"]["host"] == "h"
    assert first["meta"]["status"] == "live"
    assert second["meta"]["status"] == "cached"
    assert first["meta"]["fetched_at"].endswith("+07:00")


def test_upstream_failure_without_cache_is_503_with_reason(monkeypatch) -> None:
    async def failing(settings):
        raise RuntimeError("connect refused")

    monkeypatch.setattr(routes, "fetch_dams", failing)
    response = client.get("/api/v1/dams")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["status"] == "error" and detail["source"] == "dams"


def test_gistda_not_configured_is_explicit() -> None:
    body = client.get("/api/v1/flood/current").json()
    assert body["meta"]["status"] == "not_configured"
    assert body["data"]["features"] == []


def test_sources_status_does_not_call_upstreams(monkeypatch) -> None:
    async def must_not_run(*args, **kwargs):
        raise AssertionError("status endpoint must not hit upstream")

    for name in ("fetch_radar", "fetch_dams", "fetch_flood_points", "fetch_weather", "fetch_river"):
        monkeypatch.setattr(routes, name, must_not_run)

    async def failing(settings):
        raise RuntimeError("timeout")

    monkeypatch.setattr(routes, "fetch_dams", failing)
    client.get("/api/v1/dams")  # records an error for "dams"
    monkeypatch.setattr(routes, "fetch_dams", must_not_run)

    sources = {s["id"]: s for s in client.get("/api/v1/sources/status").json()["sources"]}
    assert sources["gistda"]["status"] == "not_configured"
    assert sources["radar"]["status"] == "idle"
    assert sources["dams"]["status"] == "error"
    assert "timeout" in sources["dams"]["last_error"]


# ---------------------------------------------------------------- freshness parsing

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-27 14:20", "2026-09-27T14:20:00+07:00"),
        ("2026-09-27", "2026-09-27T00:00:00+07:00"),
        (1790488800, "2026-09-27T13:00:00+07:00"),        # epoch seconds (RainViewer)
        (1790488800000, "2026-09-27T13:00:00+07:00"),     # epoch ms (ArcGIS)
        ("2026-09-27T07:00:00+00:00", "2026-09-27T14:00:00+07:00"),
        ("0001-01-01 00:00", None),
        ("", None),
    ],
)
def test_parse_observed(value, expected) -> None:
    parsed = parse_observed(value)
    assert (parsed.isoformat() if parsed else None) == expected


def test_max_observed_prefers_latest_feature() -> None:
    data = {"features": [
        {"properties": {"observed_at": "2026-09-27 13:00"}},
        {"properties": {"observed_at": "2026-09-27 14:30"}},
        {"properties": {"observed_at": None}},
    ]}
    assert max_observed(data) == "2026-09-27T14:30+07:00"


# ---------------------------------------------------------------- dam storage bands

@pytest.mark.parametrize(
    ("percent", "status"),
    [(None, "unknown"), (12, "low"), (55, "normal"), (80, "high"), (100, "high"), (104.2, "very_high")],
)
def test_dam_storage_status(percent, status) -> None:
    assert dam_storage_status(percent)[0] == status
