import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.services import gistda
from backend.app.services.cache import cache


client = TestClient(app)
KEY = "gistda-secret"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def with_key():
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, gistda_api_key=KEY)
    cache.clear()
    yield
    cache.clear()


def _cell(i: int, *, sub: int = 640302, population: int = 0, hospital: int = 0, file_name: str = "rd2_20260926_0613"):
    lon, lat = 99.7 + i * 0.001, 16.9
    ring = [[lon, lat], [lon + 0.001, lat], [lon + 0.001, lat + 0.001], [lon, lat]]
    return {
        "type": "Feature",
        "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
        "properties": {
            "_createdAt": "2026-09-26T18:06:46.976Z", "h3_address": f"h3-{i}", "file_name": file_name,
            "pv_tn": "จ.สุโขทัย", "ap_tn": "อ.คีรีมาศ", "tb_tn": f"ต.{sub}", "tb_idn": sub,
            "f_area": 16000, "population": population, "building": 0, "hospital": hospital, "school": 0,
            "length_road": 1500, "rice_area": 3200,
        },
    }


def _fake_upstream(cells, calls):
    async def fake_get_json(url, settings, *, params=None, headers=None):
        calls.append({"url": url, "params": params, "headers": headers})
        offset, limit = params["offset"], params["limit"]
        return {
            "type": "FeatureCollection",
            "numberMatched": len(cells),
            "features": cells[offset:offset + limit],
            "links": [{"rel": "next", "href": f"https://api-gateway.gistda.or.th/x?api_key={KEY}"}],
        }
    return fake_get_json


def test_acquisitions_parse_thai_local_time() -> None:
    stamps = gistda.acquisitions("S1D_20260910_0551, rd2_20260926_0613, S1C_20260830_1812.shp")
    assert [s.isoformat() for s in stamps] == [
        "2026-09-10T05:51:00+07:00", "2026-09-26T06:13:00+07:00", "2026-08-30T18:12:00+07:00",
    ]
    assert gistda.acquisitions(None) == []


@pytest.mark.anyio
async def test_detail_paginates_slims_and_drops_links(monkeypatch) -> None:
    cells = [_cell(i) for i in range(2500)]
    calls = []
    monkeypatch.setattr(gistda, "get_json", _fake_upstream(cells, calls))
    data = await gistda.fetch_gistda_flood((99, 16, 100, 17), Settings(_env_file=None, gistda_api_key=KEY))

    assert sorted(c["params"]["offset"] for c in calls) == [0, 1000, 2000]
    assert all(c["headers"] == {"API-Key": KEY} for c in calls)
    assert all(c["params"]["bbox"] == "99.00000,16.00000,100.00000,17.00000" for c in calls)
    assert len(data["features"]) == 2500 and data["total_matched"] == 2500 and not data["truncated"]
    assert KEY not in json.dumps(data)  # upstream `links` echo the key
    p = data["features"][0]["properties"]
    assert p["flood_rai"] == 10.0 and p["rice_rai"] == 2.0 and p["road_km"] == 1.5
    assert p["observed_at"] == "2026-09-26T06:13+07:00"
    assert data["published_at"] == "2026-09-26T18:06:46.976Z"


@pytest.mark.anyio
async def test_detail_caps_cells_and_flags_truncation(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(gistda, "get_json", _fake_upstream([_cell(i) for i in range(5200)], calls))
    data = await gistda.fetch_gistda_flood((99, 16, 100, 17), Settings(_env_file=None, gistda_api_key=KEY))
    assert len(data["features"]) == gistda.MAX_CELLS
    assert data["truncated"] is True and data["total_matched"] == 5200


@pytest.mark.anyio
async def test_count_only_mode_fetches_one_cell(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(gistda, "get_json", _fake_upstream([_cell(i) for i in range(40)], calls))
    data = await gistda.fetch_gistda_flood((99, 16, 100, 17), Settings(_env_file=None, gistda_api_key=KEY), detail=False)
    assert [c["params"]["limit"] for c in calls] == [1]
    assert data["features"] == [] and data["areas"] == [] and data["total_matched"] == 40


def test_areas_rank_hospitals_then_people() -> None:
    features = [gistda._slim(c) for c in (
        _cell(0, sub=1, population=500), _cell(1, sub=1, population=500),
        _cell(2, sub=2, population=50, hospital=1),
        _cell(3, sub=3, population=0),
    )]
    areas = gistda.summarize_areas(features)
    assert [a["subdistrict"] for a in areas] == ["ต.2", "ต.1", "ต.3"]
    assert areas[1]["population"] == 1000 and areas[1]["cells"] == 2
    assert areas[1]["center"] is not None


def test_flood_current_not_configured_without_key() -> None:
    body = client.get("/api/v1/flood/current").json()
    assert body["meta"]["status"] == "not_configured"


def test_flood_current_rejects_unknown_window() -> None:
    assert client.get("/api/v1/flood/current", params={"window": "2days"}).status_code == 422


def test_flood_current_serves_detail(monkeypatch, with_key) -> None:
    calls = []
    monkeypatch.setattr(gistda, "get_json", _fake_upstream([_cell(i) for i in range(3)], calls))
    body = client.get("/api/v1/flood/current", params={
        "min_lon": 99, "min_lat": 16, "max_lon": 100, "max_lat": 17, "window": "7days", "detail": True,
    }).json()
    assert calls[0]["url"].endswith("/features/flood/7days")
    assert body["meta"]["status"] == "live"
    assert body["meta"]["observed_at_max"] == "2026-09-26T06:13+07:00"
    assert len(body["data"]["features"]) == 3 and body["data"]["areas"][0]["cells"] == 3


def test_tile_requires_key_and_known_layer(monkeypatch) -> None:
    async def must_not_run(*args, **kwargs):
        raise AssertionError("must not call GISTDA without a key")

    monkeypatch.setattr(routes, "fetch_gistda_tile", must_not_run)
    assert client.get("/api/v1/gistda/tiles/flood-3days/10/795/463.png").status_code == 404
    assert client.get("/api/v1/gistda/tiles/nope/10/795/463.png").status_code == 404


def test_tile_proxies_png(monkeypatch, with_key) -> None:
    seen = {}

    async def fake(layer, z, x, y, settings):
        seen.update(layer=layer, z=z, x=x, y=y)
        return b"\x89PNG"

    monkeypatch.setattr(routes, "fetch_gistda_tile", fake)
    response = client.get("/api/v1/gistda/tiles/flood-freq/10/795/463.png")
    assert response.status_code == 200 and response.content == b"\x89PNG"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert seen == {"layer": "flood-freq", "z": 10, "x": 795, "y": 463}
    assert client.get("/api/v1/gistda/tiles/flood-3days/2/4/0.png").status_code == 422


def test_tile_error_does_not_leak_key(monkeypatch, with_key) -> None:
    async def failing(layer, z, x, y, settings):
        request = httpx.Request("GET", f"https://api-gateway.gistda.or.th/tile?api_key={KEY}")
        raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    monkeypatch.setattr(routes, "fetch_gistda_tile", failing)
    response = client.get("/api/v1/gistda/tiles/flood-3days/10/795/463.png")
    assert response.status_code == 502 and KEY not in response.text and "401" in response.text


def test_tile_paths_cover_all_windows() -> None:
    assert set(gistda.TILE_PATHS) == {"flood-1day", "flood-3days", "flood-7days", "flood-30days", "flood-freq"}
