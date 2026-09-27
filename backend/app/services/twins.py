"""Link DPM river gauges to the same physical station in ThaiWater.

DPM's river layer republishes RID (กรมชลประทาน) gauges, and ThaiWater carries the same
gauges too. Checked 2026-09-27: 281 of 378 DPM gauges sit at the exact ThaiWater
coordinate with the same name, and ~20 more match by RID code ("P.7A" vs "P7A") or sit
within a few metres under another name. Drawn from both layers they stack two markers,
and because DPM is a daily 00:00 reading while ThaiWater is hourly, the two often disagree.
"""

import math
import re
from typing import Any

from backend.app.services.freshness import parse_observed

SAME_NAME_MAX_M = 300
SAME_CODE_MAX_M = 1000
CO_LOCATED_MAX_M = 20  # markers overlap on screen whatever the names say
# RID gauge codes: P.7A, N.13A, Kgt.3, X.269, Y.15 ...
CODE_RE = re.compile(r"\b([A-Za-z]{1,3})\.?\s?(\d{1,3}[A-Za-z]?)\b")
TWIN_FIELDS = ("id", "name", "severity", "observed_at", "water_level_msl", "storage_percent", "trend_m", "river", "source")


def _base_name(name: str | None) -> str:
    return re.sub(r"\s*\(.*?\)\s*", "", name or "").replace(" ", "")


def _codes(name: str | None) -> set[str]:
    return {f"{letters.upper()}{number.upper()}" for letters, number in CODE_RE.findall(name or "")}


def _distance_m(a: list[float], b: list[float]) -> float:
    dx = (a[0] - b[0]) * 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110_540
    return math.hypot(dx, dy)


def _is_same_station(dpm: dict[str, Any], tw: dict[str, Any], distance: float) -> bool:
    if distance <= CO_LOCATED_MAX_M:
        return True
    dpm_name, tw_name = dpm["properties"].get("name"), tw["properties"].get("name")
    if distance <= SAME_NAME_MAX_M and _base_name(dpm_name) == _base_name(tw_name):
        return True
    return distance <= SAME_CODE_MAX_M and bool(_codes(dpm_name) & _codes(tw_name))


GRID_DEG = 0.01  # >= SAME_CODE_MAX_M in latitude, so a 3x3 neighbourhood covers every candidate


def _cell(coords: list[float]) -> tuple[int, int]:
    return (math.floor(coords[0] / GRID_DEG), math.floor(coords[1] / GRID_DEG))


def _newer(a: Any, b: Any) -> bool:
    left, right = parse_observed(a), parse_observed(b)
    return left is not None and (right is None or left > right)


def link_river_twins(features: list[dict[str, Any]], thaiwater: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate DPM river gauges with their ThaiWater twin and adopt the newer reading.

    Adds `twin_id`, `twin` (ThaiWater summary), `dpm_severity`, `dpm_observed_at` and
    `severity_source`. `severity`/`observed_at` switch to ThaiWater only when its reading
    is newer and not "unknown", so the alert list ranks on the freshest known value.
    Returns new feature dicts; the cached inputs are not mutated.
    """
    grid: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for station in thaiwater:
        if (station.get("geometry") or {}).get("type") == "Point":
            grid.setdefault(_cell(station["geometry"]["coordinates"]), []).append(station)
    linked = []
    for feature in features:
        p = feature.get("properties") or {}
        if p.get("point_type") != "river_gauge" or not grid:
            linked.append(feature)
            continue
        here = feature["geometry"]["coordinates"]
        cx, cy = _cell(here)
        nearby = [s for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in grid.get((cx + dx, cy + dy), [])]
        best, best_distance = None, math.inf
        for station in nearby:
            distance = _distance_m(here, station["geometry"]["coordinates"])
            if distance < best_distance and distance <= SAME_CODE_MAX_M and _is_same_station(feature, station, distance):
                best, best_distance = station, distance
        if best is None:
            linked.append(feature)
            continue
        twin = {field: best["properties"].get(field) for field in TWIN_FIELDS}
        twin["distance_m"] = round(best_distance)
        use_twin = twin["severity"] != "unknown" and _newer(twin["observed_at"], p.get("observed_at"))
        linked.append({
            **feature,
            "properties": {
                **p,
                "twin_id": twin["id"],
                "twin": twin,
                "dpm_severity": p.get("severity"),
                "dpm_observed_at": p.get("observed_at"),
                "severity": twin["severity"] if use_twin else p.get("severity"),
                "observed_at": twin["observed_at"] if use_twin else p.get("observed_at"),
                "severity_source": "thaiwater" if use_twin else "dpm",
            },
        })
    return linked
