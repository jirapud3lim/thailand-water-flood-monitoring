"""Which way the river runs between ThaiWater gauges, and whether the water upstream is rising.

Water runs downhill, so along one river the gauge whose water surface (m MSL) is higher is
upstream. Checked on 2026-09-28 against the live feed: sorting by `water_level_msl` puts the
Chao Phraya (Nakhon Sawan -> Samut Prakan), Ping (Chiang Dao -> Nakhon Sawan) and Mun
(Nakhon Ratchasima -> Ubon) gauges in their true order.

Limits, surfaced to the UI rather than hidden:
- Near the sea the tide can push water back upstream, so segments whose downstream gauge sits
  below TIDAL_MSL are marked `tidal` and carry no rising/falling reading.
- `trend_m` is ThaiWater's change since its previous reading; it says the water is rising at
  a gauge, not when that rise will reach the next one. No travel times are claimed.
- Gauges sharing a river name in the same basin are assumed to be on one channel; links longer
  than MAX_LINK_KM are dropped so separate branches are not joined.
"""

from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from typing import Any

TIDAL_MSL = 2.5          # m: below this the Chao Phraya/Tha Chin/Bang Pakong reaches are tidal
RISING_M = 0.05          # m change per reading counted as rising / falling
MAX_LINK_KM = 150.0      # longer gaps are likely different branches (or a reservoir in between)
SAME_PLACE_KM = 1.0      # co-located gauges (e.g. 3 at Rasi Salai) collapse into one


def _km(a: list[float], b: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def trend_state(trend_m: float | None) -> str:
    if trend_m is None:
        return "unknown"
    if trend_m >= RISING_M:
        return "rising"
    if trend_m <= -RISING_M:
        return "falling"
    return "steady"


def _station(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature["properties"]
    return {
        "id": p.get("id"), "name": p.get("name"), "province": p.get("province"),
        "coordinates": feature["geometry"]["coordinates"], "water_level_msl": p.get("water_level_msl"),
        "trend_m": p.get("trend_m"), "severity": p.get("severity"),
    }


def build_river_flow(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Upstream -> downstream links per river, from ThaiWater water-level features."""
    rivers: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for feature in features:
        p = feature.get("properties") or {}
        if p.get("river") and p.get("water_level_msl") is not None and feature.get("geometry"):
            rivers[(p.get("basin") or "", p["river"].strip())].append(_station(feature))

    segments = []
    for (basin, river), stations in rivers.items():
        stations.sort(key=lambda s: s["water_level_msl"], reverse=True)
        chain: list[dict[str, Any]] = []
        for station in stations:
            # Several gauges at one spot: keep the first (highest) one, prefer one with a trend.
            if chain and _km(chain[-1]["coordinates"], station["coordinates"]) < SAME_PLACE_KM:
                if chain[-1]["trend_m"] is None and station["trend_m"] is not None:
                    chain[-1] = station
                continue
            chain.append(station)
        for up, down in zip(chain, chain[1:]):
            km = _km(up["coordinates"], down["coordinates"])
            if km > MAX_LINK_KM:
                continue
            tidal = down["water_level_msl"] < TIDAL_MSL
            segments.append({
                "basin": basin, "river": river,
                "from": up, "to": down,
                "length_km": round(km, 1),
                "drop_m": round(up["water_level_msl"] - down["water_level_msl"], 2),
                "tidal": tidal,
                # The state that travels downstream is the upstream gauge's.
                "trend": "unknown" if tidal else trend_state(up["trend_m"]),
            })
    return {"segments": segments, "rivers": len({(s["basin"], s["river"]) for s in segments})}


def segments_in_bbox(segments: list[dict[str, Any]], bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    min_lon, min_lat, max_lon, max_lat = bbox

    def inside(c: list[float]) -> bool:
        return min_lon <= c[0] <= max_lon and min_lat <= c[1] <= max_lat

    return [s for s in segments if inside(s["from"]["coordinates"]) or inside(s["to"]["coordinates"])]
