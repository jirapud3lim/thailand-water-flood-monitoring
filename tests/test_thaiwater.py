from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.main import app
from backend.app.services.cache import cache
from backend.app.services.thaiwater import (
    BANGKOK_TZ,
    canal_severity,
    drop_stale,
    filter_bbox,
    normalize_canals,
    normalize_rain,
    normalize_water_levels,
    normalize_watergates,
    rain_severity,
    water_level_severity,
)


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=BANGKOK_TZ)
AGENCY = {"agency_name": {"th": "กรมชลประทาน ", "en": "Royal Irrigation Department"}}
GEOCODE = {
    "amphoe_name": {"th": "บ้านโพธิ์"},
    "province_name": {"th": "ฉะเชิงเทรา", "en": "Chachoengsao"},
}

# Shapes mirror live api-v3.thaiwater.net responses (captured 2026-09-27).
WATERLEVEL_PAYLOAD = {
    "waterlevel_data": {
        "result": "OK",
        "data": [{
            "id": 1313523834,
            "waterlevel_datetime": "2026-09-27 01:00",
            "waterlevel_msl": "352.21",
            "waterlevel_msl_previous": "352.18",
            "storage_percent": "190.46",
            "situation_level": 5,
            "diff_wl_bank": 3.81,
            "river_name": "คลองลำตะคอง",
            "agency": AGENCY,
            "basin": {"basin_name": {"th": "ลุ่มน้ำมูล"}},
            "geocode": GEOCODE,
            "station": {
                "id": 1394808,
                "tele_station_name": {"en": "Lam Takhong Nam Bridge", "th": "สะพานน้ำลำตะคอง"},
                "tele_station_lat": 14.7,
                "tele_station_long": 101.4,
            },
        }],
    }
}

RAIN_PAYLOAD = {
    "result": "OK",
    "data": [
        {
            "id": 312092450,
            "rain_24h": 324,
            "rain_1h": 3.5,
            "rainfall_datetime": "2026-09-26 13:00",
            "agency": AGENCY,
            "geocode": GEOCODE,
            "station": {
                "id": 1316655,
                "tele_station_name": {"th": "บ้านโพธิ์"},
                "tele_station_lat": 13.58335,
                "tele_station_long": 101.070917,
            },
        },
        {  # missing coordinates -> skipped
            "id": 2, "rain_24h": 5, "rainfall_datetime": "2026-09-27 10:00",
            "station": {"id": 2, "tele_station_lat": None, "tele_station_long": None},
        },
    ],
}

CANAL_PAYLOAD = {
    "result": "OK",
    "data": [
        {  # dead station, last reading 2021 -> dropped as stale
            "canal_datetime": "2021-01-22 12:05",
            "canal_value": 0.12,
            "station": {"id": 153, "canal_name": {"th": "ค.ภาษีเจริญ"}, "canal_lat": 13.68, "canal_long": 100.35},
        },
        {
            "canal_datetime": "2026-09-27 14:30",
            "canal_value": 1.3,
            "agency": {"agency_name": {"th": "สำนักการระบายน้ำ กรุงเทพมหานคร"}},
            "station": {
                "id": 200, "canal_name": {"th": "ค.แสนแสบ"}, "canal_lat": 13.75, "canal_long": 100.6,
                "warning_level": 1.2, "critical_level": 1.5, "bank": None,
            },
        },
    ],
}

WATERGATE_PAYLOAD = {
    "watergate_data": {
        "result": "OK",
        "data": [
            {
                "watergate_datetime_in": "2026-09-27 14:20",
                "watergate_in": 0.47,
                "watergate_out": 0.72,
                "station": {
                    "id": 575571,
                    "tele_station_name": {"en": "Khlong Phae Gate", "th": "ปตร. คลองแพ"},
                    "tele_station_lat": 13.694974,
                    "tele_station_long": 100.58231,
                },
            },
            {  # placeholder row seen in live data -> skipped
                "watergate_datetime_in": "0001-01-01 00:00",
                "watergate_in": None, "watergate_out": None,
                "station": {"id": 9, "tele_station_lat": 13.0, "tele_station_long": 100.0},
            },
        ],
    }
}


def test_water_level_normalizer_reads_nested_wrapper() -> None:
    [feature] = normalize_water_levels(WATERLEVEL_PAYLOAD)
    p = feature["properties"]
    assert feature["geometry"]["coordinates"] == [101.4, 14.7]
    assert p["name"] == "สะพานน้ำลำตะคอง"
    assert p["severity"] == "critical"
    assert p["storage_percent"] == 190.46
    assert p["trend_m"] == 0.03
    assert p["source"] == "กรมชลประทาน"
    assert p["province"] == "ฉะเชิงเทรา"


def test_rain_normalizer_skips_missing_coordinates() -> None:
    features = normalize_rain(RAIN_PAYLOAD)
    assert len(features) == 1
    assert features[0]["properties"]["rain_24h_mm"] == 324
    assert features[0]["properties"]["severity"] == "critical"


def test_canal_stale_station_is_dropped_and_threshold_applied() -> None:
    features = drop_stale(normalize_canals(CANAL_PAYLOAD), max_age_hours=6, now=NOW)
    assert [f["properties"]["name"] for f in features] == ["ค.แสนแสบ"]
    # No bank reading for this station, and its level (1.3) is below critical_level (1.5): normal.
    assert features[0]["properties"]["severity"] == "normal"
    assert features[0]["properties"]["bank_clearance_m"] is None


def test_watergate_skips_placeholder_rows() -> None:
    [feature] = normalize_watergates(WATERGATE_PAYLOAD)
    assert feature["properties"]["head_diff_m"] == -0.25
    assert feature["properties"]["name"] == "ปตร. คลองแพ"


@pytest.mark.parametrize(
    ("storage", "situation", "expected"),
    [(101, None, "critical"), (95, None, "high"), (75, None, "moderate"),
     (40, None, "normal"), (None, 5, "critical"), (None, None, "unknown")],
)
def test_water_level_severity(storage, situation, expected) -> None:
    assert water_level_severity(storage, situation) == expected


@pytest.mark.parametrize(
    ("rain", "expected"),
    [(0, "normal"), (5, "low"), (20, "moderate"), (50, "high"), (120, "critical"), (None, "unknown")],
)
def test_rain_severity_follows_tmd_classes(rain, expected) -> None:
    assert rain_severity(rain) == expected


def test_canal_severity_measures_against_the_physical_bank() -> None:
    # At or over the bank: critical, regardless of ThaiWater's own warning/critical levels.
    assert canal_severity(1.0, 0.1, 0.2, 1.0) == "critical"
    # Within the 0.3 m margin of the bank: high.
    assert canal_severity(0.8, 0.4, 0.5, 1.0) == "high"
    # Over ThaiWater's "critical_level" but still far below the bank: not an alert (เฝ้าระวังต่ำ).
    assert canal_severity(0.5, 0.1, 0.2, 1.0) == "low"
    # No bank reading (or bank <= 0): can't judge against the bank, fall back to the low step.
    assert canal_severity(1.6, 1.2, 1.5, None) == "low"
    assert canal_severity(1.6, 1.2, 1.5, 0) == "low"
    # Below every threshold: normal.
    assert canal_severity(0.5, 1.2, 1.5, None) == "normal"
    assert canal_severity(0.5, 1.2, 1.5, 2.0) == "normal"
    # No water level reading, or no thresholds at all: unknown.
    assert canal_severity(None, 1.2, 1.5, 2.0) == "unknown"
    assert canal_severity(1.0, None, None, None) == "unknown"


def test_filter_bbox() -> None:
    features = normalize_rain(RAIN_PAYLOAD)
    assert filter_bbox(features, (100.0, 13.0, 102.0, 14.0)) == features
    assert filter_bbox(features, (98.0, 18.0, 99.0, 19.0)) == []


def test_thaiwater_route_filters_cached_dataset(monkeypatch) -> None:
    calls = 0

    async def fake_fetch(layer, settings):
        nonlocal calls
        calls += 1
        features = normalize_rain(RAIN_PAYLOAD)
        for f in features:  # fresh reading, so the serve-time age check keeps it
            f["properties"]["observed_at"] = datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M")
        return {"type": "FeatureCollection", "features": features, "layer": layer}

    monkeypatch.setattr(routes, "fetch_thaiwater_layer", fake_fetch)
    cache._entries.pop("thaiwater:rain", None)
    client = TestClient(app)

    inside = client.get("/api/v1/thaiwater/rain", params={
        "min_lon": 100, "min_lat": 13, "max_lon": 102, "max_lat": 14,
    }).json()
    outside = client.get("/api/v1/thaiwater/rain", params={
        "min_lon": 98, "min_lat": 18, "max_lon": 99, "max_lat": 19,
    }).json()

    assert len(inside["data"]["features"]) == 1
    assert outside["data"]["features"] == []
    assert outside["data"]["total_national"] == 1
    assert calls == 1  # second viewport served from the same national cache entry
    cache._entries.pop("thaiwater:rain", None)


def test_thaiwater_route_rejects_unknown_layer() -> None:
    response = TestClient(app).get("/api/v1/thaiwater/tsunami")
    assert response.status_code == 404
