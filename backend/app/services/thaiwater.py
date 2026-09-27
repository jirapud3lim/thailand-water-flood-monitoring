"""ThaiWater (HII / สสน.) public API adapter.

Each upstream dataset is national in size (tens to low thousands of stations),
so we fetch it once, cache it, and filter by viewport in-process instead of
calling upstream per bounding box.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from backend.app.config import Settings
from backend.app.services.geo import Bbox, filter_bbox  # noqa: F401  (re-exported for callers)
from backend.app.services.http import get_json


THAIWATER_BASE_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"
THAIWATER_SOURCE = "คลังข้อมูลน้ำแห่งชาติ (สสน.)"
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


# --------------------------------------------------------------------------- helpers

def _records(payload: Any) -> list[dict[str, Any]]:
    """Return the record list from either {data: [...]} or {<x>_data: {data: [...]}}."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("data"), list):
        return payload["data"]
    for value in payload.values():
        if isinstance(value, dict) and isinstance(value.get("data"), list):
            return value["data"]
    return []


def _th(value: Any) -> str | None:
    """Pick the Thai label from a {th, en} dict, falling back to English."""
    if isinstance(value, dict):
        return (value.get("th") or value.get("en") or "").strip() or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    """ThaiWater timestamps are local Bangkok time, e.g. '2026-09-27 14:20'."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=BANGKOK_TZ)
        except ValueError:
            continue
    return None


def _point(lat: Any, lon: Any) -> dict[str, Any] | None:
    lat_f, lon_f = _float(lat), _float(lon)
    if lat_f is None or lon_f is None or (lat_f == 0 and lon_f == 0):
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
        return None
    return {"type": "Point", "coordinates": [lon_f, lat_f]}


def _common(record: dict[str, Any]) -> dict[str, Any]:
    geocode = record.get("geocode") or {}
    agency = record.get("agency") or {}
    basin = record.get("basin") or {}
    return {
        "province": _th(geocode.get("province_name")),
        "district": _th(geocode.get("amphoe_name")),
        "basin": _th(basin.get("basin_name")),
        "source": _th(agency.get("agency_name")) or THAIWATER_SOURCE,
        "provider": THAIWATER_SOURCE,
    }


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


# --------------------------------------------------------------------------- severity

def water_level_severity(storage_percent: float | None, situation_level: Any) -> str:
    """ThaiWater bands: >100% ล้นตลิ่ง, 70-100% น้ำมาก, 30-70% ปกติ, <30% น้อย."""
    if storage_percent is not None:
        if storage_percent > 100:
            return "critical"
        if storage_percent >= 90:
            return "high"
        if storage_percent >= 70:
            return "moderate"
        return "normal"
    level = {5: "critical", 4: "moderate", 3: "normal", 2: "normal", 1: "normal"}
    return level.get(situation_level, "unknown")


def rain_severity(rain_mm: float | None) -> str:
    """TMD 24-hour rainfall classes: light ≤10, moderate ≤35, heavy ≤90, very heavy >90."""
    if rain_mm is None:
        return "unknown"
    if rain_mm > 90:
        return "critical"
    if rain_mm > 35:
        return "high"
    if rain_mm > 10:
        return "moderate"
    if rain_mm > 0:
        return "low"
    return "normal"


def canal_severity(
    level: float | None,
    warning: float | None,
    critical: float | None,
    bank: float | None,
) -> str:
    if level is None:
        return "unknown"
    critical = critical if critical is not None else bank
    if critical is not None and level >= critical:
        return "critical"
    if warning is not None and level >= warning:
        return "high"
    if warning is None and critical is None:
        return "unknown"
    return "normal"


# --------------------------------------------------------------------------- normalizers

def normalize_water_levels(payload: Any) -> list[dict[str, Any]]:
    features = []
    for record in _records(payload):
        station = record.get("station") or {}
        geometry = _point(
            station.get("tele_station_lat", record.get("tele_station_lat")),
            station.get("tele_station_long", record.get("tele_station_long")),
        )
        if not geometry:
            continue
        storage = _float(record.get("storage_percent"))
        level = _float(record.get("waterlevel_msl"))
        previous = _float(record.get("waterlevel_msl_previous"))
        features.append(_feature(geometry, {
            **_common(record),
            "id": f"wl-{station.get('id') or record.get('id')}",
            "name": _th(station.get("tele_station_name")) or "สถานีระดับน้ำ",
            "point_type": "river_gauge",
            "severity": water_level_severity(storage, record.get("situation_level")),
            "river": record.get("river_name"),
            "water_level_msl": level,
            "trend_m": round(level - previous, 2) if level is not None and previous is not None else None,
            "storage_percent": storage,
            "bank_diff_m": _float(record.get("diff_wl_bank")),
            "bank_diff_text": record.get("diff_wl_bank_text"),
            "discharge_cms": _float(record.get("discharge")),
            "observed_at": record.get("waterlevel_datetime"),
        }))
    return features


def normalize_rain(payload: Any) -> list[dict[str, Any]]:
    features = []
    for record in _records(payload):
        station = record.get("station") or {}
        geometry = _point(station.get("tele_station_lat"), station.get("tele_station_long"))
        if not geometry:
            continue
        rain_24h = _float(record.get("rain_24h"))
        features.append(_feature(geometry, {
            **_common(record),
            "id": f"rain-{station.get('id') or record.get('id')}",
            "name": _th(station.get("tele_station_name")) or "สถานีวัดฝน",
            "point_type": "rain_gauge",
            "severity": rain_severity(rain_24h),
            "rain_24h_mm": rain_24h,
            "rain_1h_mm": _float(record.get("rain_1h")),
            "observed_at": record.get("rainfall_datetime"),
        }))
    return features


def normalize_canals(payload: Any) -> list[dict[str, Any]]:
    features = []
    for record in _records(payload):
        station = record.get("station") or {}
        geometry = _point(station.get("canal_lat"), station.get("canal_long"))
        if not geometry:
            continue
        level = _float(record.get("canal_value"))
        warning = _float(station.get("warning_level"))
        critical = _float(station.get("critical_level"))
        bank = _float(station.get("bank"))
        features.append(_feature(geometry, {
            **_common(record),
            "id": f"canal-{station.get('id') or station.get('canal_oldcode')}",
            "name": _th(station.get("canal_name")) or "สถานีวัดระดับน้ำคลอง",
            "point_type": "canal_gauge",
            "severity": canal_severity(level, warning, critical, bank),
            "water_level_m": level,
            "warning_level_m": warning,
            "critical_level_m": critical,
            "bank_level_m": bank,
            "observed_at": record.get("canal_datetime"),
        }))
    return features


def normalize_watergates(payload: Any) -> list[dict[str, Any]]:
    features = []
    for record in _records(payload):
        station = record.get("station") or {}
        geometry = _point(station.get("tele_station_lat"), station.get("tele_station_long"))
        if not geometry:
            continue
        upstream = _float(record.get("watergate_in"))
        downstream = _float(record.get("watergate_out"))
        if upstream is None and downstream is None:
            continue
        observed = record.get("watergate_datetime_in") or record.get("watergate_datetime_out")
        features.append(_feature(geometry, {
            **_common(record),
            "id": f"gate-{station.get('id')}",
            "name": _th(station.get("tele_station_name")) or "ประตูระบายน้ำ",
            "point_type": "watergate",
            "severity": "unknown",
            "upstream_level_m": upstream,
            "downstream_level_m": downstream,
            "head_diff_m": round(upstream - downstream, 2)
            if upstream is not None and downstream is not None else None,
            "pump_on": record.get("pump_on"),
            "floodgate_open": record.get("floodgate_open"),
            "observed_at": observed,
        }))
    return features


# --------------------------------------------------------------------------- layers

@dataclass(frozen=True)
class ThaiWaterLayer:
    path: str
    normalizer: Callable[[Any], list[dict[str, Any]]]
    max_age_hours: int  # drop dead stations whose last reading is older than this
    # Cache TTL lives in Settings.ttl(f"thaiwater-{layer}") so it has a single source of truth.


LAYERS: dict[str, ThaiWaterLayer] = {
    "water-level": ThaiWaterLayer("waterlevel_load", normalize_water_levels, 24),
    "rain": ThaiWaterLayer("rain_24h", normalize_rain, 26),
    "canal": ThaiWaterLayer("canal_waterlevel", normalize_canals, 6),
    "watergate": ThaiWaterLayer("watergate_load", normalize_watergates, 6),
}


def drop_stale(
    features: list[dict[str, Any]],
    max_age_hours: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(BANGKOK_TZ)
    cutoff = now - timedelta(hours=max_age_hours)
    fresh = []
    for feature in features:
        observed = _parse_time(feature["properties"].get("observed_at"))
        if observed is not None and observed >= cutoff:
            fresh.append(feature)
    return fresh


async def fetch_thaiwater_layer(layer: str, settings: Settings) -> dict[str, Any]:
    """Fetch and normalize one national ThaiWater dataset (cache this, not per bbox)."""
    spec = LAYERS[layer]
    payload = await get_json(f"{THAIWATER_BASE_URL}/{spec.path}", settings)
    features = drop_stale(spec.normalizer(payload), spec.max_age_hours)
    return {
        "type": "FeatureCollection",
        "features": features,
        "layer": layer,
        "provider": THAIWATER_SOURCE,
        "fetched_at": datetime.now(BANGKOK_TZ).isoformat(timespec="seconds"),
    }
