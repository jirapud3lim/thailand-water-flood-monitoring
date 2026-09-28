import hashlib
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from backend.app.config import Settings, get_settings
from backend.app.services.cache import CacheResult, cache
from backend.app.services import administrative_areas
from backend.app.services.freshness import max_observed
from backend.app.services.geo import Bbox, filter_bbox
from backend.app.services.gistda import FLOOD_WINDOWS, TILE_PATHS, fetch_gistda_flood, fetch_gistda_tile
from backend.app.services.thaiwater import LAYERS, drop_stale, fetch_thaiwater_layer
from backend.app.services.provinces import normalize_province, province as find_province, provinces
from backend.app.services.twins import link_river_twins
from backend.app.services import radar_tiles, tmd
from backend.app.services.river_flow import build_river_flow, segments_in_bbox
from backend.app.services.dam_photos import fetch_dam_photos
from backend.app.services.local_news import fetch_local_news
from backend.app.services.local_social import fetch_official_video_feed, for_province as social_for_province
from backend.app.services.traffic import MAX_ZOOM as TRAFFIC_MAX_ZOOM, fetch_traffic_tile
from backend.app.services.terrain_tiles import MAX_ZOOM as TERRAIN_MAX_ZOOM, fetch_terrain_tile
from backend.app.services.upstreams import (
    fetch_dams,
    fetch_flood_points,
    fetch_radar,
    fetch_river,
    fetch_weather,
)


router = APIRouter(prefix="/api/v1")

# What `alerts_only=true` (and the priority list) keeps. "low" (e.g. 1-9 cm road flooding) is
# noise for both: same threshold the map's default risk-only view uses client-side.
FLOOD_POINT_ALERTS = {"moderate", "high", "critical"}
STATION_ALERTS = {"moderate", "high", "critical"}

# Registry used by /sources/status. Keys are cache-key prefixes.
SOURCES: dict[str, dict[str, str]] = {
    "radar:": {"id": "radar", "label": "เรดาร์ฝน (RainViewer)"},
    "dams:": {"id": "dams", "label": "ปริมาณน้ำในเขื่อน (กรมชลประทาน / ปภ.)"},
    "flood-points:": {"id": "flood-points", "label": "จุดเฝ้าระวังน้ำท่วม (ปภ.)"},
    "flood:": {"id": "gistda", "label": "ขอบเขตน้ำท่วมจากดาวเทียม (GISTDA)"},
    **{
        f"thaiwater:{layer}": {"id": f"thaiwater-{layer}", "label": f"ThaiWater {layer} (สสน.)"}
        for layer in LAYERS
    },
    "weather:": {"id": "weather", "label": "พยากรณ์ฝน (Open-Meteo)"},
    "river:": {"id": "river", "label": "พยากรณ์ river discharge (Open-Meteo GloFAS)"},
    "tmd:": {"id": "tmd", "label": "พยากรณ์ฝน (กรมอุตุนิยมวิทยา NWP)"},
    "local-news:": {"id": "local-news", "label": "ข่าวพื้นที่ (กรมประชาสัมพันธ์)"},
    "local-social:": {"id": "local-social", "label": "คลิปข่าวพื้นที่ (News NBT2HD)"},
}


def _etag(result: CacheResult, *query_parts: Any) -> str:
    # fetched_at + stale flag are included so a 304 never hides a change in freshness status.
    raw = "|".join(map(str, (result.version, result.fetched_at.isoformat(), result.status == "stale", *query_parts)))
    return '"' + hashlib.sha1(raw.encode()).hexdigest()[:16] + '"'


def _not_modified(request: Request, etag: str) -> bool:
    header = request.headers.get("if-none-match", "")
    candidates = {tag.strip().removeprefix("W/") for tag in header.split(",") if tag.strip()}
    return etag in candidates or "*" in candidates


async def _cached_response(
    request: Request,
    key: str,
    source: str,
    loader: Callable[[], Awaitable[Any]],
    ttl_seconds: int,
    transform: Callable[[Any], tuple[Any, dict[str, Any]]] | None = None,
    query_parts: tuple[Any, ...] = (),
) -> Response:
    """Serve from cache with a `meta` block describing freshness, plus ETag/304.

    Keeps the original {data, stale} shape so older frontends keep working.
    `transform` returns (data, extra_meta).
    """
    try:
        result = await cache.load(key, loader, ttl_seconds)
    except Exception as exc:
        upstream_status = getattr(getattr(exc, "response", None), "status_code", None)
        rate_limited = isinstance(exc, tmd.TMDBudgetError) or upstream_status == 429
        raise HTTPException(
            status_code=429 if rate_limited else 503,
            detail={
                "status": "rate_limited" if rate_limited else "error",
                "source": source,
                "message": f"{type(exc).__name__}: {str(exc)[:200]}",
            },
        ) from exc

    etag = _etag(result, *query_parts)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)

    data, extra_meta = transform(result.value) if transform else (result.value, {})
    status = result.status
    if isinstance(data, dict):
        if data.get("source_status") == "not_configured":
            status = "not_configured"
        elif data.get("partial") and status != "stale":
            status = "partial"
        elif data.get("source_status") in {"mapping_mismatch", "rate_limited"} and status != "stale":
            status = data["source_status"]
    body = {
        "data": data,
        "stale": result.status == "stale",
        "meta": {
            "source": source,
            "status": status,
            "fetched_at": result.fetched_at.isoformat(timespec="seconds"),
            "observed_at_max": max_observed(data),
            "ttl_seconds": ttl_seconds,
            "version": result.version,
            **extra_meta,
        },
    }
    return JSONResponse(body, headers=headers)


def _bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> Bbox:
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="Invalid bounding box")
    return (min_lon, min_lat, max_lon, max_lat)


def _viewport(
    features: list[dict[str, Any]], bbox: Bbox, alerts_only: bool, alert_levels: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    in_bbox = filter_bbox(features, bbox)
    returned = [f for f in in_bbox if f["properties"].get("severity") in alert_levels] if alerts_only else in_bbox
    return returned, {
        "total_national": len(features),
        "total_in_bbox": len(in_bbox),
        "returned_count": len(returned),
        "alerts_only": alerts_only,
    }


def _bbox_key(bbox: Bbox) -> str:
    return ",".join(f"{v:.3f}" for v in bbox)


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness of this app only; upstream health is in /sources/status."""
    return {"status": "ok"}


@router.get("/sources/status")
async def sources_status(settings: Settings = Depends(get_settings)) -> dict:
    """Last known state of every upstream, from cache bookkeeping (no upstream calls)."""
    keys = cache.health()
    report = []
    for prefix, info in SOURCES.items():
        matching = {k: v for k, v in keys.items() if k.startswith(prefix)}
        if (info["id"] == "gistda" and not settings.gistda_api_key) or (info["id"] == "tmd" and not settings.tmd_api_key):
            status = "not_configured"
        elif not matching:
            status = "idle"  # nobody has requested this source since startup
        elif any(v["failing"] for v in matching.values()):
            status = "stale" if any(v["cached"] for v in matching.values()) else "error"
        else:
            status = "ok"
        fetched = [v["fetched_at"] for v in matching.values() if v["fetched_at"]]
        errors = [v for v in matching.values() if v["last_error_at"]]
        latest_error = max(errors, key=lambda v: v["last_error_at"]) if errors else None
        totals = {name: sum(v[name] for v in matching.values())
                  for name in ("hits", "misses", "stale_served", "upstream_errors")}
        report.append({
            **info,
            "status": status,
            "ttl_seconds": settings.ttl(info["id"]),
            "last_fetched_at": max(fetched) if fetched else None,
            "last_error_at": latest_error["last_error_at"] if latest_error else None,
            "last_error": latest_error["last_error"] if latest_error else None,
            "cache_keys": len(matching),
            **totals,
            # TMD spends a fixed datapoint quota; surface what is left.
            **({"quota": dict(tmd.quota), "metrics": dict(tmd.metrics)} if info["id"] == "tmd" else {}),
        })
    return {"sources": report}


@router.get("/radar/latest")
async def radar_latest(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    return await _cached_response(
        request, "radar:metadata", "radar", lambda: fetch_radar(settings), settings.ttl("radar"),
    )


@router.get("/radar/tiles/{frame}/{z}/{x}/{y}.png")
async def radar_tile(
    frame: str,
    z: int = Path(ge=0, le=radar_tiles.MAX_ZOOM),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    settings: Settings = Depends(get_settings),
) -> Response:
    """RainViewer radar tile via the shared server cache (see services/radar_tiles.py).

    Only frames RainViewer currently lists are served, so this is not an open proxy.
    """
    if not radar_tiles.FRAME_RE.match(frame):
        raise HTTPException(status_code=422, detail="Bad frame id")
    if x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=422, detail="Tile x/y out of range for zoom")
    try:
        metadata = (await cache.load("radar:metadata", lambda: fetch_radar(settings), settings.ttl("radar"))).value
    except Exception:
        raise HTTPException(status_code=502, detail="Radar metadata unavailable")
    path = radar_tiles.current_frames(metadata).get(frame)
    if not path:
        raise HTTPException(status_code=404, detail="Unknown or expired radar frame")
    try:
        content = await radar_tiles.fetch_radar_tile(metadata.get("host") or "", path, z, x, y, settings)
    except radar_tiles.RateLimited as error:
        raise HTTPException(status_code=503, detail=str(error), headers={"Retry-After": "30"})
    except (httpx.HTTPError, OSError) as error:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        raise HTTPException(status_code=502, detail=f"Radar upstream failed ({status or type(error).__name__})")
    # A past frame's image never changes; it drops out of RainViewer's list after ~2 hours.
    return Response(content, media_type="image/png", headers={"Cache-Control": "public, max-age=7200, immutable"})


@router.get("/dams")
async def dams(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    def with_counts(data: dict) -> tuple[dict, dict]:
        return data, {"returned_count": len(data.get("features", [])), "unmatched_count": len(data.get("unmatched", []))}

    return await _cached_response(
        request, "dams:current", "dams", lambda: fetch_dams(settings), settings.ttl("dams"), with_counts,
    )


@router.get("/flood-points")
async def flood_points(
    request: Request,
    min_lon: float = Query(97.0, ge=-180, le=180),
    min_lat: float = Query(5.0, ge=-90, le=90),
    max_lon: float = Query(106.0, ge=-180, le=180),
    max_lat: float = Query(21.0, ge=-90, le=90),
    alerts_only: bool = Query(False, description="Keep only severity moderate..critical"),
    settings: Settings = Depends(get_settings),
) -> Response:
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)
    twins, twins_version = await _river_twins(settings)

    def in_view(data: dict) -> tuple[dict, dict]:
        # Link before the viewport filter so alerts_only sees the merged (fresher) severity.
        linked = link_river_twins(data["features"], twins)
        features, counts = _viewport(linked, bbox, alerts_only, FLOOD_POINT_ALERTS)
        return {**data, "features": features}, {**counts, "twins_linked": sum(1 for f in linked if f["properties"].get("twin_id"))}

    # One national cache entry: upstream calls no longer scale with users or map moves.
    return await _cached_response(
        request, "flood-points:national", "flood-points",
        lambda: fetch_flood_points(settings), settings.ttl("flood-points"),
        in_view, (_bbox_key(bbox), alerts_only, twins_version),
    )


async def _river_twins(settings: Settings) -> tuple[list[dict[str, Any]], Any]:
    """ThaiWater river gauges from the same cache entry /thaiwater/water-level uses.

    Twins are an enhancement: if ThaiWater is down, flood points are served unlinked.
    """
    try:
        result = await cache.load(
            "thaiwater:water-level", lambda: fetch_thaiwater_layer("water-level", settings),
            settings.ttl("thaiwater-water-level"),
        )
    except Exception:
        return [], None
    return drop_stale(result.value["features"], LAYERS["water-level"].max_age_hours), result.version


@router.get("/weather/current")
async def weather_current(
    request: Request,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    settings: Settings = Depends(get_settings),
) -> Response:
    return await _cached_response(
        request, f"weather:{lat:.3f}:{lon:.3f}", "weather",
        lambda: fetch_weather(lat, lon, settings), settings.ttl("weather"),
    )


@router.get("/river")
async def river(
    request: Request,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    settings: Settings = Depends(get_settings),
) -> Response:
    return await _cached_response(
        request, f"river:{lat:.3f}:{lon:.3f}", "river",
        lambda: fetch_river(lat, lon, settings), settings.ttl("river"),
    )


@router.get("/flood/current")
async def flood_current(
    request: Request,
    min_lon: float = Query(97.0, ge=-180, le=180),
    min_lat: float = Query(5.0, ge=-90, le=90),
    max_lon: float = Query(106.0, ge=-180, le=180),
    max_lat: float = Query(21.0, ge=-90, le=90),
    window: str = Query("3days", description="GISTDA rolling window: " + ", ".join(FLOOD_WINDOWS)),
    detail: bool = Query(False, description="Return flood cells and per-area impact (zoomed-in views only)"),
    province: str | None = Query(None, description="Thai province name; replaces the bbox with GISTDA pv_idn"),
    areas_only: bool = Query(False, description="With detail: drop cell geometry, keep the per-sub-district summary"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """GISTDA satellite flood cells. Without `detail` only the cell count is fetched."""
    if window not in FLOOD_WINDOWS:
        raise HTTPException(status_code=422, detail=f"Unknown window. Use one of: {', '.join(FLOOD_WINDOWS)}")
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)
    scope = _province_or_422(province) if province else None
    key = f"flood:{window}:{int(detail)}:" + (f"pv{scope['code']}" if scope else ":".join(f"{value:.2f}" for value in bbox))

    def shape(data: dict) -> tuple[dict, dict]:
        return ({**data, "features": []} if areas_only else data), {}

    return await _cached_response(
        request, key, "gistda",
        lambda: fetch_gistda_flood(bbox, settings, window, detail, scope["code"] if scope else None),
        settings.ttl("gistda"), shape, (areas_only,),
    )


def _province_or_422(name: str) -> dict[str, Any]:
    found = find_province(name)
    if found is None:
        raise HTTPException(status_code=422, detail="Unknown province. See /api/v1/provinces")
    return found


@router.get("/gistda/tiles/{layer}/{z}/{x}/{y}.png")
async def gistda_tile(
    layer: str,
    z: int = Path(ge=0, le=20),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    settings: Settings = Depends(get_settings),
) -> Response:
    """GISTDA flood / recurring-flood tiles (512 px, XYZ), proxied to keep the key server-side."""
    if layer not in TILE_PATHS:
        raise HTTPException(status_code=404, detail=f"Unknown layer. Use one of: {', '.join(TILE_PATHS)}")
    if not settings.gistda_api_key:
        raise HTTPException(status_code=404, detail="GISTDA not configured (GISTDA_API_KEY)")
    if x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=422, detail="Tile x/y out of range for zoom")
    try:
        content = await fetch_gistda_tile(layer, z, x, y, settings)
    except (httpx.HTTPError, OSError) as error:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        raise HTTPException(status_code=502, detail=f"GISTDA tile failed ({status or type(error).__name__})")
    # Flood windows rebuild about daily; recurring-flood areas are a multi-year summary.
    max_age = 86400 if layer == "flood-freq" else 900
    return Response(content, media_type="image/png", headers={"Cache-Control": f"public, max-age={max_age}"})


@router.get("/river-flow")
async def river_flow(
    request: Request,
    min_lon: float = Query(97.0, ge=-180, le=180),
    min_lat: float = Query(5.0, ge=-90, le=90),
    max_lon: float = Query(106.0, ge=-180, le=180),
    max_lat: float = Query(21.0, ge=-90, le=90),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Upstream -> downstream links between ThaiWater river gauges, with the upstream trend.

    Built from the same cached ThaiWater water-level entry as /thaiwater/water-level, so it
    costs no extra upstream call. See services/river_flow.py for the method and its limits.
    """
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)

    def in_view(data: dict) -> tuple[dict, dict]:
        flow = build_river_flow(drop_stale(data["features"], LAYERS["water-level"].max_age_hours))
        segments = segments_in_bbox(flow["segments"], bbox)
        return {"segments": segments}, {"returned_count": len(segments), "rivers_national": flow["rivers"]}

    return await _cached_response(
        request, "thaiwater:water-level", "thaiwater-water-level",
        lambda: fetch_thaiwater_layer("water-level", settings), settings.ttl("thaiwater-water-level"),
        in_view, ("river-flow", _bbox_key(bbox)),
    )


@router.get("/thaiwater/{layer}")
async def thaiwater_layer(
    request: Request,
    layer: str,
    min_lon: float = Query(97.0, ge=-180, le=180),
    min_lat: float = Query(5.0, ge=-90, le=90),
    max_lon: float = Query(106.0, ge=-180, le=180),
    max_lat: float = Query(21.0, ge=-90, le=90),
    alerts_only: bool = Query(False, description="Keep only severity moderate..critical"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """ThaiWater station layers: water-level, rain, canal, watergate."""
    if layer not in LAYERS:
        raise HTTPException(status_code=404, detail=f"Unknown layer. Use one of: {', '.join(LAYERS)}")
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)
    source = f"thaiwater-{layer}"

    def in_view(data: dict) -> tuple[dict, dict]:
        # Re-check age on every serve: a stale-fallback or long-cached entry must not
        # resurrect stations whose last reading has since expired.
        fresh = drop_stale(data["features"], LAYERS[layer].max_age_hours)
        features, counts = _viewport(fresh, bbox, alerts_only, STATION_ALERTS)
        return {**data, "features": features, "total_national": len(fresh)}, counts

    return await _cached_response(
        request, f"thaiwater:{layer}", source,
        lambda: fetch_thaiwater_layer(layer, settings), settings.ttl(source),
        in_view, (_bbox_key(bbox), alerts_only),
    )


@router.get("/traffic/status")
async def traffic_status(settings: Settings = Depends(get_settings)) -> dict:
    """Whether the traffic tile proxy has a key; the map hides traffic when it does not."""
    return {"configured": bool(settings.tomtom_api_key), "provider": "TomTom Traffic Flow"}


@router.get("/terrain/tiles/{z}/{x}/{y}.png")
async def terrain_tile(
    z: int = Path(ge=0, le=TERRAIN_MAX_ZOOM),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Static Terrarium PNG for the optional, non-risk elevation map."""
    if x >= 1 << z or y >= 1 << z:
        raise HTTPException(status_code=422, detail="Tile coordinates out of range")
    try:
        content = await fetch_terrain_tile(z, x, y, settings)
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        raise HTTPException(status_code=502, detail=f"Terrain tile failed ({status or type(error).__name__})") from error
    return Response(content, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/traffic/tiles/{z}/{x}/{y}.png")
async def traffic_tile(
    z: int = Path(ge=0, le=TRAFFIC_MAX_ZOOM),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.tomtom_api_key:
        raise HTTPException(status_code=404, detail="Traffic layer not configured (TOMTOM_API_KEY)")
    if x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=422, detail="Tile x/y out of range for zoom")
    try:
        content = await fetch_traffic_tile(z, x, y, settings)
    except (httpx.HTTPError, OSError) as error:
        # Generic detail on purpose: httpx errors carry the upstream URL, which includes the key.
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        raise HTTPException(status_code=502, detail=f"Traffic upstream failed ({status or type(error).__name__})")
    # Flow tiles refresh about every minute upstream.
    return Response(content, media_type="image/png", headers={"Cache-Control": "public, max-age=60"})


@router.get("/provinces")
async def list_provinces() -> dict:
    """The 77 provinces: canonical name, GISTDA/standard code, and a bbox for zooming."""
    return {"provinces": provinces()}


@router.get("/local-news")
async def local_news(
    request: Request,
    province: str = Query(..., min_length=1, max_length=80),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Recent PRD water news explicitly mentioning a province, not a flood alert."""
    name = normalize_province(province)
    if name is None:
        raise HTTPException(status_code=422, detail="Unknown province")
    return await _cached_response(
        request, f"local-news:{name}", "local-news",
        lambda: fetch_local_news(name, settings), settings.ttl("local-news"),
    )


@router.get("/local-social")
async def local_social(
    request: Request,
    province: str = Query(..., min_length=1, max_length=80),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Vetted channel video links mentioning a province; not an incident confirmation."""
    name = normalize_province(province)
    if name is None:
        raise HTTPException(status_code=422, detail="Unknown province")
    return await _cached_response(
        request, "local-social:nbt-feed", "local-social",
        lambda: fetch_official_video_feed(settings), settings.ttl("local-social"),
        lambda videos: (social_for_province(videos, name), {}), (name,),
    )


@router.get("/areas")
async def list_administrative_areas(
    parent_code: str | None = Query(None, max_length=6, pattern=r"^\d{2}(?:\d{2})?$"),
) -> dict:
    """Cascading province, district and sub-district choices from the local DDPM registry."""
    rows = administrative_areas.child_areas(parent_code)
    if rows is None:
        raise HTTPException(status_code=404, detail="Administrative area not found")
    return {
        "areas": rows,
        "parent_code": parent_code,
        "source": administrative_areas.source(),
    }


SEVERITY_RANK = {"critical": 4, "high": 3, "moderate": 2}


def _thaiwater_station(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature["properties"]
    return {
        "type": "Feature",
        "geometry": feature["geometry"],
        "properties": {
            "id": p.get("id"), "tw_id": p.get("id"), "name": p.get("name"), "point_type": "river_gauge",
            "severity": p.get("severity"), "observed_at": p.get("observed_at"), "province": p.get("province"),
            "river": p.get("river"), "water_level_msl": p.get("water_level_msl"),
            "storage_percent": p.get("storage_percent"), "source": p.get("source"), "origin": "thaiwater",
        },
    }


@router.get("/alerts")
async def alerts(
    request: Request,
    min_lon: float = Query(97.0, ge=-180, le=180),
    min_lat: float = Query(5.0, ge=-90, le=90),
    max_lon: float = Query(106.0, ge=-180, le=180),
    max_lat: float = Query(21.0, ge=-90, le=90),
    province: str | None = Query(None, description="Thai province name; replaces the bbox"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Alert-level stations for the priority list, one entry per physical station.

    DPM road-flood points, DPM river gauges (merged with their ThaiWater twin), and the
    ThaiWater river gauges DPM does not carry. `by_province` counts the whole country so the
    province picker can rank provinces without another request.
    """
    scope = _province_or_422(province) if province else None
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)
    twins, twins_version = await _river_twins(settings)

    def build(data: dict) -> tuple[dict, dict]:
        linked = link_river_twins(data["features"], twins)
        linked_ids = {f["properties"]["twin_id"] for f in linked if f["properties"].get("twin_id")}
        stations = linked + [_thaiwater_station(f) for f in twins if f["properties"].get("id") not in linked_ids]
        alerting = []
        for feature in stations:
            p = feature["properties"]
            if p.get("severity") not in FLOOD_POINT_ALERTS:
                continue
            # New dicts: the cached upstream features must stay untouched.
            alerting.append({**feature, "properties": {
                **p, "province": normalize_province(p.get("province")) or p.get("province"),
                "tw_id": p.get("tw_id") or p.get("twin_id"),
            }})
        counts = {pv["name"]: {"alerts": 0, "critical": 0, "high": 0} for pv in provinces()}
        for feature in alerting:
            p = feature["properties"]
            if p["province"] in counts:
                counts[p["province"]]["alerts"] += 1
                if p["severity"] in ("critical", "high"):
                    counts[p["province"]][p["severity"]] += 1
        in_scope = [f for f in alerting if f["properties"]["province"] == scope["name"]] if scope else filter_bbox(alerting, bbox)
        in_scope.sort(key=lambda f: (SEVERITY_RANK.get(f["properties"]["severity"], 0), str(f["properties"].get("observed_at") or "")), reverse=True)
        return {
            "type": "FeatureCollection",
            "features": in_scope,
            "scope": {"province": scope["name"] if scope else None},
            "by_province": [{"name": name, **c} for name, c in counts.items()],
        }, {"returned_count": len(in_scope), "total_alerts_national": len(alerting)}

    return await _cached_response(
        request, "flood-points:national", "flood-points",
        lambda: fetch_flood_points(settings), settings.ttl("flood-points"),
        build, (scope["code"] if scope else _bbox_key(bbox), twins_version),
    )


@router.get("/forecast/provinces")
async def forecast_provinces(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """TMD model rain forecast per province: today and the next two days (~460 datapoints per refresh)."""
    return await _cached_response(
        request, "tmd:provinces", "tmd", lambda: tmd.fetch_province_forecast(settings), settings.ttl("tmd-provinces"),
    )


@router.get("/forecast/tambon")
async def forecast_tambon(
    request: Request,
    province: str = Query(..., max_length=80),
    district: str = Query(..., min_length=1, max_length=80),
    subdistrict: str = Query(..., min_length=1, max_length=80),
    settings: Settings = Depends(get_settings),
) -> Response:
    """TMD hourly rain for the next 24 h in one sub-district (~48 datapoints, cached 3 h)."""
    scope = _province_or_422(province)
    district, subdistrict = tmd.bare_name(district), tmd.bare_name(subdistrict)
    return await _cached_response(
        request, f"tmd:tambon:{scope['code']}:{district}:{subdistrict}", "tmd",
        lambda: tmd.fetch_tambon_forecast(scope["name"], district, subdistrict, settings), settings.ttl("tmd-tambon"),
    )


@router.get("/forecast/hourly")
async def forecast_hourly(
    request: Request,
    area_code: str = Query(..., min_length=2, max_length=6, pattern=r"^\d{2}(?:\d{2}){0,2}$"),
    hours: int = Query(24, ge=24, le=48),
    settings: Settings = Depends(get_settings),
) -> Response:
    """TMD hourly rain at the reference point for a validated province/district/sub-district."""
    if hours not in (24, 48):
        raise HTTPException(status_code=422, detail="hours must be 24 or 48")
    selected = administrative_areas.area(area_code)
    if selected is None:
        raise HTTPException(status_code=404, detail="Administrative area not found")

    def future_horizon(data: dict) -> tuple[dict, dict]:
        view = tmd.hourly_view(data, hours)
        summary = view.get("summary") or {}
        return view, {
            "area_code": area_code,
            "hours_requested": hours,
            "hours_returned": summary.get("hours_returned", 0),
        }

    return await _cached_response(
        request,
        f"tmd:hourly:{area_code}:{hours}",
        "tmd",
        lambda: tmd.fetch_hourly_forecast(selected, hours, settings),
        settings.ttl("tmd-hourly"),
        future_horizon,
        (area_code, hours),
    )


@router.get("/dams/photos")
async def dam_photos(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Curated Wikimedia Commons photo per dam, with author and license for attribution."""
    return await _cached_response(
        request, "dam-photos", "dam-photos", lambda: fetch_dam_photos(settings), settings.ttl("dam-photos"),
    )
