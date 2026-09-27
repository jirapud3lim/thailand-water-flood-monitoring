import hashlib
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from backend.app.config import Settings, get_settings
from backend.app.services.cache import CacheResult, cache
from backend.app.services.freshness import max_observed
from backend.app.services.geo import Bbox, filter_bbox
from backend.app.services.thaiwater import LAYERS, drop_stale, fetch_thaiwater_layer
from backend.app.services.upstreams import (
    fetch_dams,
    fetch_flood_points,
    fetch_gistda_flood,
    fetch_radar,
    fetch_river,
    fetch_weather,
)


router = APIRouter(prefix="/api/v1")

# What `alerts_only=true` keeps. Mirrors the zoomed-out rule the map used client-side.
FLOOD_POINT_ALERTS = {"low", "moderate", "high", "critical"}
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
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "source": source, "message": f"{type(exc).__name__}: {str(exc)[:200]}"},
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
        if info["id"] == "gistda" and not settings.gistda_flood_url:
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
        })
    return {"sources": report}


@router.get("/radar/latest")
async def radar_latest(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    return await _cached_response(
        request, "radar:metadata", "radar", lambda: fetch_radar(settings), settings.ttl("radar"),
    )


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
    alerts_only: bool = Query(False, description="Keep only severity low..critical"),
    settings: Settings = Depends(get_settings),
) -> Response:
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)

    def in_view(data: dict) -> tuple[dict, dict]:
        features, counts = _viewport(data["features"], bbox, alerts_only, FLOOD_POINT_ALERTS)
        return {**data, "features": features}, counts

    # One national cache entry: upstream calls no longer scale with users or map moves.
    return await _cached_response(
        request, "flood-points:national", "flood-points",
        lambda: fetch_flood_points(settings), settings.ttl("flood-points"),
        in_view, (_bbox_key(bbox), alerts_only),
    )


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
    settings: Settings = Depends(get_settings),
) -> Response:
    bbox = _bbox(min_lon, min_lat, max_lon, max_lat)
    key = "flood:" + ":".join(f"{value:.2f}" for value in bbox)
    return await _cached_response(
        request, key, "gistda", lambda: fetch_gistda_flood(bbox, settings), settings.ttl("gistda"),
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
