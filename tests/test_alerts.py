from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.services import gistda
from backend.app.services.cache import cache
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.provinces import normalize_province, provinces


client = TestClient(app)


def _recent(hours: float = 1) -> str:
    return (datetime.now(BANGKOK_TZ) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")


def _point(name, lon, lat, severity, province, point_type="river_gauge", **extra):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
        "id": extra.pop("id", name), "name": name, "point_type": point_type, "severity": severity,
        "province": province, "observed_at": extra.pop("observed_at", _recent(16)), **extra,
    }}


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def upstreams(monkeypatch):
    async def fake_points(settings):
        return {"type": "FeatureCollection", "features": [
            _point("บ้านกง (Y.15)", 99.9, 17.1, "normal", "สุโขทัย"),                 # twin, TW says moderate
            _point("ถ.พัฒนาการ", 100.64, 13.74, "critical", "กรุงเทพมหานคร", point_type="road_flood"),
            _point("ถ.ปกติ", 100.60, 13.75, "normal", "กรุงเทพมหานคร", point_type="road_flood"),
            _point("ถ.น้ำขังน้อย", 100.61, 13.76, "low", "กรุงเทพมหานคร", point_type="road_flood"),  # 1-9 cm: below the alert floor
        ]}

    async def fake_tw(layer, settings):
        return {"type": "FeatureCollection", "features": [
            _point("บ้านกง", 99.9, 17.1, "moderate", "สุโขทัย", id="wl-1", observed_at=_recent(1)),
            _point("กงไกรลาศ", 99.95, 16.95, "high", "สุโขทัย", id="wl-2", observed_at=_recent(1)),   # ThaiWater only
            _point("ฝาง", 99.2, 19.9, "normal", "เชียงใหม่", id="wl-3", observed_at=_recent(1)),
        ]}

    monkeypatch.setattr(routes, "fetch_flood_points", fake_points)
    monkeypatch.setattr(routes, "fetch_thaiwater_layer", fake_tw)


def test_province_table_is_complete() -> None:
    rows = provinces()
    assert len(rows) == 77 and len({r["code"] for r in rows}) == 77
    assert all(r["bbox"] and r["bbox"][0] < r["bbox"][2] and r["bbox"][1] < r["bbox"][3] for r in rows)
    assert {r["code"]: r["name"] for r in rows}[64] == "สุโขทัย"


@pytest.mark.parametrize("raw, expected", [
    ("จ.สุโขทัย", "สุโขทัย"), ("จังหวัดเชียงใหม่", "เชียงใหม่"), ("กรุงเทพฯ", "กรุงเทพมหานคร"),
    (" น่าน ", "น่าน"), ("สาธารณรัฐแห่งสหภาพเมียนมา", None), (None, None),
])
def test_normalize_province(raw, expected) -> None:
    assert normalize_province(raw) == expected


def test_alerts_by_province_one_entry_per_station(upstreams) -> None:
    body = client.get("/api/v1/alerts", params={"province": "จ.สุโขทัย"}).json()
    features = body["data"]["features"]
    assert body["data"]["scope"] == {"province": "สุโขทัย"}
    # ThaiWater-only station included; the DPM/ThaiWater twin appears once, with the fresher severity.
    assert [(f["properties"]["name"], f["properties"]["severity"]) for f in features] == [
        ("กงไกรลาศ", "high"), ("บ้านกง (Y.15)", "moderate"),
    ]
    assert [f["properties"]["tw_id"] for f in features] == ["wl-2", "wl-1"]
    assert features[0]["properties"]["origin"] == "thaiwater"


def test_alerts_by_province_counts_whole_country(upstreams) -> None:
    body = client.get("/api/v1/alerts", params={"province": "สุโขทัย"}).json()
    counts = {row["name"]: row for row in body["data"]["by_province"]}
    assert len(counts) == 77
    assert counts["สุโขทัย"] == {"name": "สุโขทัย", "alerts": 2, "critical": 0, "high": 1}
    assert counts["กรุงเทพมหานคร"]["alerts"] == 1 and counts["กรุงเทพมหานคร"]["critical"] == 1
    assert counts["เชียงใหม่"]["alerts"] == 0
    assert body["meta"]["total_alerts_national"] == 3


def test_alerts_by_bbox_when_no_province(upstreams) -> None:
    body = client.get("/api/v1/alerts", params={"min_lon": 100, "min_lat": 13.5, "max_lon": 101, "max_lat": 14}).json()
    # "ถ.น้ำขังน้อย" (severity "low") is in the same bbox but stays out: matches the map's default view.
    assert [f["properties"]["name"] for f in body["data"]["features"]] == ["ถ.พัฒนาการ"]


def test_alerts_excludes_low_severity(upstreams) -> None:
    body = client.get("/api/v1/alerts", params={"province": "กรุงเทพมหานคร"}).json()
    names = [f["properties"]["name"] for f in body["data"]["features"]]
    assert "ถ.น้ำขังน้อย" not in names
    counts = {row["name"]: row for row in body["data"]["by_province"]}
    assert counts["กรุงเทพมหานคร"]["alerts"] == 1  # only ถ.พัฒนาการ; the "low" point is not counted


def test_alerts_rejects_unknown_province(upstreams) -> None:
    assert client.get("/api/v1/alerts", params={"province": "แอตแลนติส"}).status_code == 422


def test_provinces_endpoint() -> None:
    assert len(client.get("/api/v1/provinces").json()["provinces"]) == 77


def test_gistda_province_scope_uses_pv_idn_and_areas_only(monkeypatch) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, gistda_api_key="k")
    calls = []

    async def fake_get_json(url, settings, *, params=None, headers=None):
        calls.append(params)
        ring = [[99.8, 17.0], [99.801, 17.0], [99.801, 17.001], [99.8, 17.0]]
        return {"numberMatched": 1, "features": [{"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
                "properties": {"tb_idn": 640302, "tb_tn": "ต.ทุ่งหลวง", "ap_tn": "อ.คีรีมาศ", "pv_tn": "จ.สุโขทัย",
                               "f_area": 1600, "population": 3, "file_name": "rd2_20260926_0613"}}]}

    monkeypatch.setattr(gistda, "get_json", fake_get_json)
    body = client.get("/api/v1/flood/current", params={"province": "สุโขทัย", "detail": True, "areas_only": True}).json()
    assert calls[0]["pv_idn"] == 64 and "bbox" not in calls[0]
    assert body["data"]["features"] == [] and body["data"]["areas"][0]["subdistrict"] == "ต.ทุ่งหลวง"
    assert client.get("/api/v1/flood/current", params={"province": "ไม่มี"}).status_code == 422
