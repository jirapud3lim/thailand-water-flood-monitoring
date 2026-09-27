"""Parse upstream observation timestamps into comparable Bangkok-time datetimes."""

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def parse_observed(value: Any) -> datetime | None:
    """Accept ThaiWater/RID local strings, ISO strings, and epoch seconds/milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10**11 else value  # ArcGIS uses epoch ms
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(BANGKOK_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or value.startswith("0001-"):
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(BANGKOK_TZ) if parsed.tzinfo else parsed.replace(tzinfo=BANGKOK_TZ)
    except ValueError:
        pass
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=BANGKOK_TZ)
        except ValueError:
            continue
    return None


def max_observed(data: Any) -> str | None:
    """Latest observation time in a payload, as ISO-8601 Bangkok time.

    Looks at a top-level ``observed_at`` and at ``features[].properties.observed_at``.
    """
    if not isinstance(data, dict):
        return None
    candidates = [data.get("observed_at")]
    for feature in data.get("features") or []:
        candidates.append((feature.get("properties") or {}).get("observed_at"))
    parsed = [dt for dt in map(parse_observed, candidates) if dt is not None]
    return max(parsed).isoformat(timespec="minutes") if parsed else None
