from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.upstreams import normalize_dams, normalize_flood_points


client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_flood_returns_empty_geojson_when_not_configured() -> None:
    response = client.get("/api/v1/flood/current")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["type"] == "FeatureCollection"
    assert payload["features"] == []
    assert payload["source_status"] == "not_configured"


def test_flood_rejects_invalid_bbox() -> None:
    response = client.get(
        "/api/v1/flood/current",
        params={"min_lon": 105, "max_lon": 100},
    )
    assert response.status_code == 422


def test_normalize_dams_merges_live_values_with_coordinates() -> None:
    rid = {
        "date": "2026-09-27",
        "data": [{
            "region": "ภาคเหนือ",
            "dam": [{"id": "1", "name": "เขื่อนทดสอบ", "percent_storage": 91, "volume": 10}],
        }],
    }
    locations = {
        "features": [{
            "geometry": {"type": "Point", "coordinates": [100, 15]},
            "properties": {"name": "เขื่อนทดสอบ", "PROV_NAM_T": "ทดสอบ"},
        }]
    }
    result = normalize_dams(rid, locations)
    props = result["features"][0]["properties"]
    assert props["storage_status"] == "high"
    assert "level" not in props  # storage must not reuse flood-severity wording
    assert result["features"][0]["geometry"]["coordinates"] == [100, 15]


def test_normalize_flood_points_assigns_severity() -> None:
    rivers = {
        "features": [{
            "geometry": {"type": "Point", "coordinates": [100, 15]},
            "properties": {
                "STATION_DISPLAY_NAME": "P.1",
                "WATER_LEVEL_MSL": 10.2,
                "BANK_LEVEL": 10.0,
            },
        }]
    }
    roads = {
        "features": [{
            "geometry": {"type": "MultiPoint", "coordinates": [[100.5, 13.7]]},
            "properties": {"STATION_NAME_TH": "ถนนทดสอบ", "WATER_LEVEL_CM": 22},
        }]
    }
    result = normalize_flood_points(rivers, roads)
    assert result["features"][0]["properties"]["severity"] == "critical"
    assert result["features"][1]["properties"]["severity"] == "high"
    assert result["features"][1]["geometry"]["type"] == "Point"
