import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.app.config import Settings
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.geo import THAILAND_BBOX
from backend.app.services.http import get_json

log = logging.getLogger(__name__)
ARCGIS_PAGE_SIZE = 2000
ARCGIS_MAX_PAGES = 10


RAINVIEWER_METADATA_URL = "https://api.rainviewer.com/public/weather-maps.json"
OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
RID_DAM_URL = "https://app.rid.go.th/reservoir/api/dam/public"
DPM_DAM_GEO_URL = (
    "https://gis-portal.disaster.go.th/arcgis/rest/services/"
    "MapDX/DPM_TH_Hydrology/FeatureServer/1/query"
)
DPM_RIVER_STATION_URL = (
    "https://gis-portal.disaster.go.th/arcgis/rest/services/"
    "Map115_Dynamic/DPM_RUNOFF_STATION_RID_DSS/FeatureServer/0/query"
)
DPM_ROAD_FLOOD_URL = (
    "https://gis-portal.disaster.go.th/arcgis/rest/services/"
    "Map116/DPM_RUNOFF_STATION_DDS_DSS/FeatureServer/1/query"
)


async def _get_json(
    url: str,
    settings: Settings,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return await get_json(url, settings, params=params, headers=headers)


def _exceeded_limit(payload: dict[str, Any]) -> bool:
    # ArcGIS puts the flag at top level (f=json) or under "properties" (f=geojson).
    return bool(payload.get("exceededTransferLimit") or (payload.get("properties") or {}).get("exceededTransferLimit"))


async def _get_arcgis_all(url: str, settings: Settings, params: dict[str, Any]) -> dict[str, Any]:
    """Follow ArcGIS pagination so results over one page are not silently truncated."""
    features: list[dict[str, Any]] = []
    first_page_ids: set[str] = set()
    for page in range(ARCGIS_MAX_PAGES):
        payload = await _get_json(
            url, settings, params={**params, "resultOffset": page * ARCGIS_PAGE_SIZE, "resultRecordCount": ARCGIS_PAGE_SIZE},
        )
        batch = payload.get("features", [])
        # Guard: a server that ignores resultOffset would return page 0 forever.
        batch_ids = {str(f.get("id", f.get("properties"))) for f in batch[:5]}
        if page == 0:
            first_page_ids = batch_ids
        elif batch_ids and batch_ids == first_page_ids:
            log.warning("ArcGIS ignored resultOffset for %s; keeping first page only", url)
            break
        features.extend(batch)
        if not batch or not _exceeded_limit(payload):
            return {"type": "FeatureCollection", "features": features, "truncated": False}
    log.warning("ArcGIS result for %s still exceeds %d pages; data truncated", url, ARCGIS_MAX_PAGES)
    return {"type": "FeatureCollection", "features": features, "truncated": True}


async def fetch_radar(settings: Settings) -> dict[str, Any]:
    return await _get_json(RAINVIEWER_METADATA_URL, settings)


async def fetch_weather(lat: float, lon: float, settings: Settings) -> dict[str, Any]:
    return await _get_json(
        OPEN_METEO_WEATHER_URL,
        settings,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "precipitation,rain,showers,weather_code",
            "hourly": "precipitation_probability,precipitation",
            # 3 days so a full 48 h ahead exists at any time of day.
            "forecast_days": 3,
            "timezone": "Asia/Bangkok",
        },
    )


async def fetch_river(lat: float, lon: float, settings: Settings) -> dict[str, Any]:
    return await _get_json(
        OPEN_METEO_FLOOD_URL,
        settings,
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "river_discharge,river_discharge_max",
            "forecast_days": 7,
        },
    )


async def fetch_gistda_flood(
    bbox: tuple[float, float, float, float],
    settings: Settings,
) -> dict[str, Any]:
    if not settings.gistda_flood_url:
        return {
            "type": "FeatureCollection",
            "features": [],
            "source_status": "not_configured",
        }

    headers = {}
    if settings.gistda_api_key:
        headers[settings.gistda_api_key_header] = settings.gistda_api_key
    min_lon, min_lat, max_lon, max_lat = bbox
    data = await _get_json(
        settings.gistda_flood_url,
        settings,
        params={"bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}"},
        headers=headers,
    )
    if data.get("type") == "FeatureCollection":
        data.setdefault("source_status", "live")
        return data
    return {
        "type": "FeatureCollection",
        "features": data.get("features", []),
        "source_status": "live",
    }


def _name_key(value: str | None) -> str:
    return "".join((value or "").split()).replace("อ่างเก็บน้ำ", "เขื่อน")


def dam_storage_status(percent: float | int | None) -> tuple[str, str]:
    """Reservoir storage band for display only; NOT a flood severity.

    Project display thresholds (not an official RID classification):
    <30% ต่ำ, 30–80% ปกติ, 80–100% สูง, >100% สูงมาก (เกินความจุ).
    """
    if percent is None:
        return "unknown", "ไม่มีข้อมูล"
    value = float(percent)
    if value > 100:
        return "very_high", "สูงมาก (เกินความจุ)"
    if value >= 80:
        return "high", "สูง"
    if value >= 30:
        return "normal", "ปกติ"
    return "low", "ต่ำ"


def normalize_dams(
    rid_payload: dict[str, Any],
    location_payload: dict[str, Any],
) -> dict[str, Any]:
    locations: dict[str, dict[str, Any]] = {}
    for feature in location_payload.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        key = _name_key(properties.get("name") or properties.get("STATION_NAME_TH"))
        if key and geometry:
            locations[key] = {"geometry": geometry, "properties": properties}

    features = []
    unmatched = []
    for group in rid_payload.get("data", []):
        region = group.get("region")
        for dam in group.get("dam", []):
            location = locations.get(_name_key(dam.get("name")))
            if not location:
                unmatched.append(dam.get("name"))
                continue
            status, label = dam_storage_status(dam.get("percent_storage"))
            features.append(
                {
                    "type": "Feature",
                    "geometry": location["geometry"],
                    "properties": {
                        **dam,
                        "region": region,
                        "province": location["properties"].get("PROV_NAM_T"),
                        "storage_status": status,
                        "storage_label": label,
                        "observed_at": rid_payload.get("date"),
                        "source": "กรมชลประทาน / ปภ.",
                    },
                }
            )
    return {
        "type": "FeatureCollection",
        "features": features,
        "observed_at": rid_payload.get("date"),
        # Dams reported by RID but not placeable on the map; shown so a bad join never reads as "no dam".
        "unmatched": unmatched,
    }


def _river_severity(properties: dict[str, Any]) -> tuple[str, float | None]:
    water = properties.get("WATER_LEVEL_MSL")
    bank = properties.get("BANK_LEVEL")
    if water is None or bank is None:
        return "unknown", None
    clearance = round(float(bank) - float(water), 2)
    if clearance <= 0:
        return "critical", clearance
    if clearance <= 0.5:
        return "high", clearance
    if clearance <= 1.5:
        return "moderate", clearance
    return "normal", clearance


def dpm_utc_to_local(value: Any) -> str | None:
    """DPM road-flood DATA_DT is a naive string in UTC (layer metadata: timeZone "UTC").

    Verified 2026-09-27: "2026-09-27 08:30:00" was published at 15:30 Bangkok time.
    Returns Bangkok local "YYYY-MM-DD HH:MM" (same format as ThaiWater) or the input unchanged
    if it cannot be parsed, so nothing is silently dropped.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return parsed.astimezone(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M")
    return value


def _road_severity(depth_cm: float | int | None) -> str:
    if depth_cm is None:
        return "unknown"
    depth = float(depth_cm)
    if depth >= 30:
        return "critical"
    if depth >= 20:
        return "high"
    if depth >= 10:
        return "moderate"
    if depth > 0:
        return "low"
    return "normal"


def normalize_flood_points(
    river_payload: dict[str, Any],
    road_payload: dict[str, Any],
) -> dict[str, Any]:
    features = []
    for feature in river_payload.get("features", []):
        if not feature.get("geometry"):
            continue
        raw = feature.get("properties") or {}
        severity, clearance = _river_severity(raw)
        features.append(
            {
                "type": "Feature",
                "geometry": feature["geometry"],
                "properties": {
                    "id": raw.get("STATION_DISPLAY_NAME"),
                    "name": raw.get("STATION_DISPLAY_NAME") or "สถานีระดับน้ำ",
                    "point_type": "river_gauge",
                    "severity": severity,
                    "water_level_msl": raw.get("WATER_LEVEL_MSL"),
                    "bank_level": raw.get("BANK_LEVEL"),
                    "bank_clearance_m": clearance,
                    "capacity_percent": raw.get("PERCENT_CAPACITY"),
                    "status_label": raw.get("WATER_LEVEL_LABEL"),
                    "province": raw.get("PROV_NAM_T"),
                    "observed_at": raw.get("DATA_DT"),
                    "source": raw.get("AGENCY_NAME_TH") or "กรมชลประทาน / ปภ.",
                },
            }
        )

    for feature in road_payload.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        if geometry.get("type") == "MultiPoint" and geometry.get("coordinates"):
            geometry = {"type": "Point", "coordinates": geometry["coordinates"][0]}
        raw = feature.get("properties") or {}
        depth = raw.get("WATER_LEVEL_CM")
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "id": raw.get("STATION_OLDCODE") or raw.get("MASTER_STATION_ID"),
                    "name": raw.get("STATION_NAME_TH") or raw.get("ROAD_NAME") or "จุดวัดน้ำท่วมถนน",
                    "point_type": "road_flood",
                    "severity": _road_severity(depth),
                    "water_depth_cm": depth,
                    "maximum_depth_cm": raw.get("FLOOD_MAX"),
                    "status_label": raw.get("CHK_STATUSTXT"),
                    "province": raw.get("PROV_NAM_T"),
                    "observed_at": dpm_utc_to_local(raw.get("DATA_DT")),
                    "observed_at_utc": raw.get("DATA_DT"),  # upstream value, kept for provenance
                    "source": raw.get("AGENCY_NAME_TH") or "สำนักการระบายน้ำ กรุงเทพมหานคร",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


async def fetch_dams(settings: Settings) -> dict[str, Any]:
    rid_payload, locations = await asyncio.gather(
        _get_json(RID_DAM_URL, settings),
        _get_arcgis_all(
            DPM_DAM_GEO_URL,
            settings,
            {
                "where": "1=1",
                "outFields": "name,STATION_NAME_TH,PROV_NAM_T",
                "returnGeometry": "true",
                "outSR": 4326,
                "f": "geojson",
            },
        ),
    )
    return normalize_dams(rid_payload, locations)


def _arcgis_bbox_params(
    bbox: tuple[float, float, float, float],
    out_fields: str,
) -> dict[str, Any]:
    return {
        "where": "1=1",
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": 4326,
        "geometry": ",".join(str(value) for value in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "f": "geojson",
    }


async def fetch_flood_points(settings: Settings) -> dict[str, Any]:
    """National DPM river gauges + road-flood sensors, fetched once and filtered per viewport.

    If one of the two feeds fails the other is still returned, flagged `partial`.
    """
    river_fields = (
        "STATION_DISPLAY_NAME,WATER_STORAGE_LEVEL_ID,WATER_LEVEL_LABEL,"
        "WATER_LEVEL_MSL,BANK_LEVEL,PERCENT_CAPACITY,AGENCY_NAME_TH,"
        "PROV_NAM_T,DATA_DT"
    )
    road_fields = (
        "MASTER_STATION_ID,STATION_OLDCODE,STATION_NAME_TH,ROAD_NAME,"
        "WATER_LEVEL_CM,FLOOD_MAX,CHK_STATUSTXT,AGENCY_NAME_TH,"
        "PROV_NAM_T,DATA_DT"
    )
    river, road = await asyncio.gather(
        _get_arcgis_all(DPM_RIVER_STATION_URL, settings, _arcgis_bbox_params(THAILAND_BBOX, river_fields)),
        _get_arcgis_all(DPM_ROAD_FLOOD_URL, settings, _arcgis_bbox_params(THAILAND_BBOX, road_fields)),
        return_exceptions=True,
    )
    errors = {
        name: f"{type(result).__name__}: {str(result)[:160]}"
        for name, result in (("river", river), ("road", road))
        if isinstance(result, BaseException)
    }
    if len(errors) == 2:
        raise RuntimeError(f"all flood-point feeds failed: {errors}")
    empty: dict[str, Any] = {"features": []}
    result = normalize_flood_points(
        empty if isinstance(river, BaseException) else river,
        empty if isinstance(road, BaseException) else road,
    )
    truncated = [
        name for name, payload in (("river", river), ("road", road))
        if isinstance(payload, dict) and payload.get("truncated")
    ]
    if errors or truncated:
        result["partial"] = True
        result["source_errors"] = errors
        result["truncated_feeds"] = truncated
    return result
