"""GISTDA Disaster Platform: satellite (SAR) flood extent and recurring-flood areas.

Facts checked against the live API on 2026-09-27:
- Auth is the `API-Key` header. Responses echo the key inside `links`, so those are
  never passed on and pagination uses our own offset, not the `next` link.
- Flood cells are H3 resolution-9 hexagons (~0.12 km²) with impact counts per cell.
  The rolling windows (1day/3days/7days/30days) are rebuilt about once a day, and
  `1day` is empty whenever the last satellite pass is older than 24 h, so an empty
  window never means "no flooding".
- SAR cannot see water between buildings: Bangkok returns zero cells.
- `/maps/.../tms/{z}/{x}/{y}` tiles use XYZ row order despite the name, 512 px each.
"""

import asyncio
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from backend.app.config import Settings
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.http import get_bytes, get_json

BASE_URL = "https://api-gateway.gistda.or.th/api/2.0/resources"
FLOOD_WINDOWS = ("1day", "3days", "7days", "30days")
TILE_PATHS = {
    **{f"flood-{window}": f"maps/flood/{window}/tms" for window in FLOOD_WINDOWS},
    "flood-freq": "maps/flood-freq/tms",
}
PAGE_SIZE = 1000
MAX_CELLS = 3000  # ~2 KB per cell upstream; beyond this the client is told to zoom in
SQM_PER_RAI = 1600
# Acquisition times in `file_name`, e.g. "S1C_20260921_0558, rd2_20260926_0613".
# They are Thai local time: the SAR satellites used fly dawn-dusk orbits and every
# stamp seen falls at 05:50-06:15 or 18:05-18:30, i.e. local 06:00/18:00 passes.
ACQUISITION_RE = re.compile(r"_(\d{8})_(\d{4})")
IMPACT_FIELDS = ("population", "building", "hospital", "school")


def headers(settings: Settings) -> dict[str, str]:
    return {"API-Key": settings.gistda_api_key}


def acquisitions(file_name: str | None) -> list[datetime]:
    stamps = []
    for day, hhmm in ACQUISITION_RE.findall(file_name or ""):
        try:
            stamps.append(datetime.strptime(day + hhmm, "%Y%m%d%H%M").replace(tzinfo=BANGKOK_TZ))
        except ValueError:
            continue
    return stamps


def _round(coords: Any) -> Any:
    if isinstance(coords, (int, float)):
        return round(coords, 5)
    return [_round(c) for c in coords]


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _slim(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature.get("properties") or {}
    stamps = acquisitions(p.get("file_name"))
    geometry = feature.get("geometry") or {}
    return {
        "type": "Feature",
        "geometry": {"type": geometry.get("type"), "coordinates": _round(geometry.get("coordinates") or [])},
        "properties": {
            "h3": p.get("h3_address"),
            "province": p.get("pv_tn"),
            "district": p.get("ap_tn"),
            "subdistrict": p.get("tb_tn"),
            "subdistrict_id": p.get("tb_idn"),
            "flood_rai": round(_number(p.get("f_area")) / SQM_PER_RAI, 1),
            "population": int(_number(p.get("population"))),
            "building": int(_number(p.get("building"))),
            "hospital": int(_number(p.get("hospital"))),
            "school": int(_number(p.get("school"))),
            "road_km": round(_number(p.get("length_road")) / 1000, 2),
            "rice_rai": round(_number(p.get("rice_area")) / SQM_PER_RAI, 1),
            "observed_at": max(stamps).isoformat(timespec="minutes") if stamps else None,
            "passes": len(stamps),
        },
    }


def _centroid(feature: dict[str, Any]) -> tuple[float, float] | None:
    coords = feature["geometry"]["coordinates"]
    while coords and isinstance(coords[0], list) and isinstance(coords[0][0], list):
        coords = coords[0]  # MultiPolygon/Polygon -> outer ring
    if not coords:
        return None
    return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))


def summarize_areas(features: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Group cells by sub-district and rank by who and what is affected, then extent.

    Ranking is a display order for triage, not an official severity.
    """
    groups: dict[Any, dict[str, Any]] = {}
    centers: dict[Any, list[tuple[float, float]]] = defaultdict(list)
    for feature in features:
        p = feature["properties"]
        key = p["subdistrict_id"] or (p["province"], p["district"], p["subdistrict"])
        group = groups.setdefault(key, {
            "province": p["province"], "district": p["district"], "subdistrict": p["subdistrict"],
            "cells": 0, "flood_rai": 0.0, "road_km": 0.0, "rice_rai": 0.0, "observed_at": None,
            **{field: 0 for field in IMPACT_FIELDS},
        })
        group["cells"] += 1
        for field in ("flood_rai", "road_km", "rice_rai", *IMPACT_FIELDS):
            group[field] += p[field]
        if p["observed_at"] and (group["observed_at"] is None or p["observed_at"] > group["observed_at"]):
            group["observed_at"] = p["observed_at"]
        if (center := _centroid(feature)) is not None:
            centers[key].append(center)
    for key, group in groups.items():
        points = centers[key]
        group["center"] = [
            round(sum(c[0] for c in points) / len(points), 5), round(sum(c[1] for c in points) / len(points), 5),
        ] if points else None
        for field in ("flood_rai", "road_km", "rice_rai"):
            group[field] = round(group[field], 1)
    ranked = sorted(
        groups.values(),
        key=lambda g: (g["hospital"], g["school"], g["population"], g["building"], g["flood_rai"]),
        reverse=True,
    )
    return ranked[:limit]


async def fetch_gistda_flood(
    bbox: tuple[float, float, float, float],
    settings: Settings,
    window: str = "3days",
    detail: bool = True,
    province_code: int | None = None,
) -> dict[str, Any]:
    """Flood cells in bbox, or in one province when `province_code` (GISTDA `pv_idn`) is set.

    `detail=False` only counts cells (one tiny request).
    """
    if not settings.gistda_api_key:
        return {"type": "FeatureCollection", "features": [], "source_status": "not_configured"}

    url = f"{BASE_URL}/features/flood/{window}"
    base = {"pv_idn": province_code} if province_code else {"bbox": ",".join(f"{v:.5f}" for v in bbox)}

    async def page(offset: int, limit: int) -> dict[str, Any]:
        return await get_json(url, settings, params={**base, "limit": limit, "offset": offset}, headers=headers(settings))

    first = await page(0, PAGE_SIZE if detail else 1)
    total = int(first.get("numberMatched") or 0)
    raw = list(first.get("features") or [])
    if detail and len(raw) < min(total, MAX_CELLS):
        offsets = range(len(raw), min(total, MAX_CELLS), PAGE_SIZE)
        for result in await asyncio.gather(*(page(o, min(PAGE_SIZE, MAX_CELLS - o)) for o in offsets)):
            raw.extend(result.get("features") or [])

    published = [str(f.get("properties", {}).get("_createdAt")) for f in raw if f.get("properties", {}).get("_createdAt")]
    features = [_slim(f) for f in raw] if detail else []
    observed = [f["properties"]["observed_at"] for f in features if f["properties"]["observed_at"]]
    return {
        "type": "FeatureCollection",
        "features": features,
        "window": window,
        "detail": detail,
        "total_matched": total,
        "truncated": detail and total > len(features),
        # Collection build time; all cells in a window share one nightly rebuild.
        "published_at": max(published) if published else None,
        # Latest satellite pass among cells returned (detail only); drives meta.observed_at_max.
        "observed_at": max(observed) if observed else None,
        "areas": summarize_areas(features) if detail else [],
        "source_status": "live",
    }


async def fetch_gistda_tile(layer: str, z: int, x: int, y: int, settings: Settings) -> bytes:
    return await get_bytes(f"{BASE_URL}/{TILE_PATHS[layer]}/{z}/{x}/{y}", settings, headers=headers(settings))
