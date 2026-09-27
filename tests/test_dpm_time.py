"""DPM road-flood DATA_DT is a naive UTC string (layer metadata timeZone "UTC")."""

import pytest

from backend.app.services.freshness import max_observed
from backend.app.services.upstreams import dpm_utc_to_local, normalize_flood_points


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-27 08:30:00", "2026-09-27 15:30"),   # observed live on 2026-09-27
        ("2026-09-27 20:15:00", "2026-09-28 03:15"),   # crosses midnight in Bangkok
        ("2026-09-27 08:30", "2026-09-27 15:30"),
        ("not a date", "not a date"),                  # unparseable: kept, never dropped
        (None, None),
    ],
)
def test_dpm_utc_to_local(raw, expected) -> None:
    assert dpm_utc_to_local(raw) == expected


def test_road_flood_points_use_bangkok_time_and_keep_raw() -> None:
    roads = {"features": [{
        "geometry": {"type": "Point", "coordinates": [100.55, 13.75]},
        "properties": {"STATION_NAME_TH": "ถนนทดสอบ", "WATER_LEVEL_CM": 12, "DATA_DT": "2026-09-27 08:30:00"},
    }]}
    [feature] = normalize_flood_points({"features": []}, roads)["features"]
    props = feature["properties"]
    assert props["observed_at"] == "2026-09-27 15:30"
    assert props["observed_at_utc"] == "2026-09-27 08:30:00"
    assert max_observed({"features": [feature]}) == "2026-09-27T15:30+07:00"


def test_river_gauges_times_are_not_shifted() -> None:
    # DPM river layer publishes a daily date with no timezone evidence; leave it as is.
    rivers = {"features": [{
        "geometry": {"type": "Point", "coordinates": [100, 15]},
        "properties": {"STATION_DISPLAY_NAME": "P.1", "DATA_DT": "2026-09-27 00:00:00"},
    }]}
    [feature] = normalize_flood_points(rivers, {"features": []})["features"]
    assert feature["properties"]["observed_at"] == "2026-09-27 00:00:00"
