from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.main import app
from backend.app.services.cache import cache
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.river_flow import build_river_flow, trend_state


def gauge(id_, lon, lat, msl, trend=0.0, river="แม่น้ำเจ้าพระยา", basin="ลุ่มน้ำเจ้าพระยา"):
    observed = (datetime.now(BANGKOK_TZ) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
        "id": id_, "name": id_, "province": "x", "river": river, "basin": basin,
        "water_level_msl": msl, "trend_m": trend, "severity": "normal", "observed_at": observed}}


def test_orders_gauges_downhill_and_carries_the_upstream_trend():
    flow = build_river_flow([
        gauge("ayutthaya", 100.57, 14.37, 4.5, trend=0.0),
        gauge("nakhon_sawan", 100.12, 15.67, 22.4, trend=0.12),   # rising
        gauge("chainat", 100.18, 15.25, 18.1, trend=-0.08),       # falling
    ])
    links = [(s["from"]["id"], s["to"]["id"], s["trend"]) for s in flow["segments"]]
    assert links == [("nakhon_sawan", "chainat", "rising"), ("chainat", "ayutthaya", "falling")]
    assert flow["segments"][0]["drop_m"] == 4.3


def test_tidal_reach_has_no_direction_claim():
    flow = build_river_flow([gauge("nonthaburi", 100.5, 13.95, 2.25, trend=0.3), gauge("bangkok", 100.51, 13.7, 0.58, trend=0.3)])
    [segment] = flow["segments"]
    assert segment["tidal"] is True and segment["trend"] == "unknown"


def test_colocated_gauges_merge_and_far_gauges_do_not_link():
    flow = build_river_flow([
        gauge("a", 104.16, 15.34, 114.63, trend=None, river="แม่น้ำมูล", basin="มูล"),
        gauge("b", 104.161, 15.341, 114.60, trend=0.01, river="แม่น้ำมูล", basin="มูล"),   # same spot as a
        gauge("c", 104.86, 15.23, 111.2, river="แม่น้ำมูล", basin="มูล"),
        gauge("far", 98.9, 18.8, 300.0, river="แม่น้ำมูล", basin="มูล"),                   # > 150 km away
    ])
    assert [(s["from"]["id"], s["to"]["id"]) for s in flow["segments"]] == [("b", "c")]


def test_same_river_name_in_another_basin_is_a_different_river():
    flow = build_river_flow([gauge("x", 100, 15, 20, basin="A"), gauge("y", 100, 14.8, 10, basin="B")])
    assert flow["segments"] == []


def test_trend_thresholds():
    assert [trend_state(v) for v in (None, 0.05, 0.04, -0.05)] == ["unknown", "rising", "steady", "falling"]


def test_endpoint_uses_the_water_level_cache_and_filters_by_view(monkeypatch):
    cache.clear()
    calls = []

    async def fake(layer, settings):
        calls.append(layer)
        return {"type": "FeatureCollection", "features": [
            gauge("up", 100.12, 15.67, 22.4, trend=0.1), gauge("down", 100.18, 15.25, 18.1)]}

    monkeypatch.setattr(routes, "fetch_thaiwater_layer", fake)
    client = TestClient(app)
    body = client.get("/api/v1/river-flow", params={"min_lon": 99.5, "min_lat": 15, "max_lon": 101, "max_lat": 16}).json()
    assert [s["trend"] for s in body["data"]["segments"]] == ["rising"]
    assert client.get("/api/v1/river-flow", params={"min_lon": 97, "min_lat": 5, "max_lon": 98, "max_lat": 6}).json()["data"]["segments"] == []
    client.get("/api/v1/thaiwater/water-level")
    assert calls == ["water-level"]   # one upstream fetch shared by both endpoints
    cache.clear()
