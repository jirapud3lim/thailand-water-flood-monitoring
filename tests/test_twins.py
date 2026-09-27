from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.main import app
from backend.app.services.cache import cache
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.twins import link_river_twins


client = TestClient(app)


def _hours_ago(hours: float) -> str:
    return (datetime.now(BANGKOK_TZ) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")


def _dpm(name, lon, lat, severity="normal", observed_at=None, point_type="river_gauge"):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
        "id": name, "name": name, "point_type": point_type, "severity": severity,
        "observed_at": observed_at or _hours_ago(16), "water_level_msl": 10.0,
    }}


def _tw(tw_id, name, lon, lat, severity="moderate", observed_at=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
        "id": tw_id, "name": name, "point_type": "river_gauge", "severity": severity,
        "observed_at": observed_at or _hours_ago(1), "water_level_msl": 10.4, "storage_percent": 88.0,
    }}


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


def test_same_name_same_point_links_and_adopts_newer_reading() -> None:
    [linked] = link_river_twins([_dpm("บ้านกง (Y.15)", 99.9, 17.1)], [_tw("wl-1", "บ้านกง", 99.9, 17.1)])
    p = linked["properties"]
    assert p["twin_id"] == "wl-1" and p["twin"]["distance_m"] == 0
    assert (p["severity"], p["severity_source"], p["dpm_severity"]) == ("moderate", "thaiwater", "normal")
    assert p["observed_at"] == p["twin"]["observed_at"]


def test_code_match_links_despite_different_name_and_distance() -> None:
    # "P.7A" vs "P7A", ~130 m apart.
    [linked] = link_river_twins([_dpm("ต.ในเมือง (P.7A)", 99.5200, 16.4800)],
                                [_tw("wl-2", "เมืองกำแพงเพชร (P7A)", 99.5210, 16.4808)])
    assert linked["properties"]["twin_id"] == "wl-2"


def test_co_located_links_whatever_the_names() -> None:
    [linked] = link_river_twins([_dpm("บ้านวัดพระรูป (T.10)", 100.1, 14.47)], [_tw("wl-3", "เมืองสุพรรณบุรี", 100.10005, 14.47)])
    assert linked["properties"]["twin_id"] == "wl-3"


def test_different_station_nearby_is_not_linked() -> None:
    # 400 m apart, different names, different codes.
    [linked] = link_river_twins([_dpm("ตลาดนาทวี (X.282)", 100.70, 6.73)], [_tw("wl-4", "นาทวี", 100.7036, 6.73)])
    assert "twin_id" not in linked["properties"]


def test_older_or_unknown_twin_keeps_dpm_severity() -> None:
    dpm = _dpm("บ้านกง (Y.15)", 99.9, 17.1, severity="high", observed_at=_hours_ago(1))
    [older] = link_river_twins([dpm], [_tw("wl-1", "บ้านกง", 99.9, 17.1, observed_at=_hours_ago(5))])
    [unknown] = link_river_twins([dpm], [_tw("wl-1", "บ้านกง", 99.9, 17.1, severity="unknown")])
    for linked in (older, unknown):
        assert linked["properties"]["severity"] == "high" and linked["properties"]["severity_source"] == "dpm"
        assert linked["properties"]["twin_id"] == "wl-1"


def test_road_points_and_inputs_are_untouched() -> None:
    road = _dpm("ถ.พัฒนาการ", 100.6, 13.7, point_type="road_flood")
    river = _dpm("บ้านกง (Y.15)", 99.9, 17.1)
    out = link_river_twins([road, river], [_tw("wl-1", "บ้านกง", 99.9, 17.1), _tw("wl-9", "x", 100.6, 13.7)])
    assert out[0] is road
    assert "twin_id" not in river["properties"]  # cached input not mutated


def test_route_links_twins_before_alert_filter(monkeypatch) -> None:
    async def fake_points(settings):
        return {"type": "FeatureCollection", "features": [_dpm("บ้านกง (Y.15)", 99.9, 17.1), _dpm("บ้านสบสอย (P.73)", 99.5, 17.5)]}

    async def fake_tw(layer, settings):
        assert layer == "water-level"
        return {"type": "FeatureCollection", "features": [_tw("wl-1", "บ้านกง", 99.9, 17.1)]}

    monkeypatch.setattr(routes, "fetch_flood_points", fake_points)
    monkeypatch.setattr(routes, "fetch_thaiwater_layer", fake_tw)
    body = client.get("/api/v1/flood-points", params={"alerts_only": "true"}).json()
    assert [f["properties"]["twin_id"] for f in body["data"]["features"]] == ["wl-1"]
    assert body["meta"]["twins_linked"] == 1


def test_route_serves_unlinked_when_thaiwater_is_down(monkeypatch) -> None:
    async def fake_points(settings):
        return {"type": "FeatureCollection", "features": [_dpm("บ้านกง (Y.15)", 99.9, 17.1)]}

    monkeypatch.setattr(routes, "fetch_flood_points", fake_points)
    body = client.get("/api/v1/flood-points").json()
    assert body["meta"]["status"] == "live" and body["meta"]["twins_linked"] == 0
    assert "twin_id" not in body["data"]["features"][0]["properties"]
