from typing import Any

Bbox = tuple[float, float, float, float]

# Generous national envelope (lon/lat) used to fetch whole-country datasets once.
THAILAND_BBOX: Bbox = (97.0, 5.0, 106.0, 21.0)


def filter_bbox(features: list[dict[str, Any]], bbox: Bbox) -> list[dict[str, Any]]:
    """Keep Point features inside bbox (inclusive). Non-point geometries are dropped."""
    min_lon, min_lat, max_lon, max_lat = bbox
    kept = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not coords or len(coords) < 2:
            continue
        if min_lon <= coords[0] <= max_lon and min_lat <= coords[1] <= max_lat:
            kept.append(feature)
    return kept
