"""TMD (กรมอุตุนิยมวิทยา) NWP forecast API: model rain forecasts, not observations.

Checked against the live API on 2026-09-27:
- Auth is `Authorization: Bearer <token>`.
- Every call spends quota: datapoints = locations x time steps x fields, 100,000 in total
  (`X-Datapoint-Limit`), plus 60 requests/minute. The reset period is not documented, so
  the last `X-Datapoint-Remaining` is kept and shown in /sources/status.
- `daily/region` returns all provinces of one region with `geocode` = standard province
  code; the six regions together cover all 77 provinces for ~460 datapoints (3 days, 2 fields).
- `hourly/place` resolves province/amphoe/tambon by Thai name without prefixes.
- Only domains 1 (10 days) and 2 (72 h) are current; 0 and 3 hold 2023/2024 data.
"""

import asyncio
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from backend.app.config import Settings
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.http import get_response
from backend.app.services.provinces import normalize_province

BASE_URL = "https://data.tmd.go.th/nwpapi/v1"
REGIONS = ("C", "N", "NE", "E", "S", "W")
FORECAST_DAYS = 3
TAMBON_HOURS = 24
HOURLY_BUFFER_HOURS = 3
# TMD's own 24-hour rainfall classes (มม.).
RAIN_CLASSES = ((90.0, "หนักมาก"), (35.0, "หนัก"), (10.0, "ปานกลาง"), (0.0, "เล็กน้อย"))
COND_LABELS = {
    1: "ท้องฟ้าแจ่มใส", 2: "มีเมฆบางส่วน", 3: "เมฆเป็นส่วนมาก", 4: "มีเมฆมาก", 5: "ฝนตกเล็กน้อย",
    6: "ฝนปานกลาง", 7: "ฝนตกหนัก", 8: "ฝนฟ้าคะนอง", 9: "อากาศหนาวจัด", 10: "อากาศหนาว",
    11: "อากาศเย็น", 12: "อากาศร้อนจัด",
}
# Administrative prefixes GISTDA puts in names; TMD wants bare names.
PREFIX_RE = re.compile(r"^(จังหวัด|จ\.|อำเภอ|อ\.|กิ่งอำเภอ|เขต|ตำบล|ต\.|แขวง)\s*")

quota: dict[str, Any] = {"limit": None, "remaining": None, "checked_at": None}
metrics: dict[str, int] = {
    "upstream_requests": 0,
    "estimated_datapoints": 0,
    "not_found": 0,
    "mapping_mismatch": 0,
    "local_rate_limited": 0,
    "quota_guarded": 0,
    "upstream_rate_limited": 0,
}
_request_times: deque[float] = deque()
_request_lock = asyncio.Lock()


class TMDBudgetError(RuntimeError):
    """A local guard stopped a TMD call before it spent quota."""


async def _reserve_request(estimated_datapoints: int, settings: Settings) -> None:
    now = time.monotonic()
    async with _request_lock:
        while _request_times and now - _request_times[0] >= 60:
            _request_times.popleft()
        if len(_request_times) >= settings.tmd_requests_per_minute:
            metrics["local_rate_limited"] += 1
            raise TMDBudgetError("TMD local request limit reached; retry later")
        remaining = quota.get("remaining")
        if remaining is not None and remaining - estimated_datapoints < settings.tmd_quota_reserve:
            metrics["quota_guarded"] += 1
            raise TMDBudgetError("TMD datapoint reserve reached; retry after quota reset")
        _request_times.append(now)
        metrics["upstream_requests"] += 1
        metrics["estimated_datapoints"] += estimated_datapoints


def rain_class(mm: float | None) -> str | None:
    if mm is None or mm <= 0:
        return None
    return next(label for floor, label in RAIN_CLASSES if mm > floor)


def bare_name(name: str | None) -> str:
    return PREFIX_RE.sub("", (name or "").strip()).strip()


async def _get(
    path: str, settings: Settings, params: dict[str, Any], estimated_datapoints: int | None = None,
) -> dict[str, Any]:
    fields = len(str(params.get("fields", "")).split(",")) if params.get("fields") else 1
    estimate = estimated_datapoints or max(1, int(params.get("duration", 1))) * fields
    await _reserve_request(estimate, settings)
    try:
        response = await get_response(
            f"{BASE_URL}/{path}", settings, params=params,
            headers={"Authorization": f"Bearer {settings.tmd_api_key}"},
        )
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 429:
            metrics["upstream_rate_limited"] += 1
        raise
    limit, remaining = response.headers.get("X-Datapoint-Limit"), response.headers.get("X-Datapoint-Remaining")
    if remaining is not None:
        quota.update(
            limit=int(limit) if limit and limit.isdigit() else quota["limit"],
            remaining=int(remaining) if remaining.isdigit() else quota["remaining"],
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    return response.json()


def _not_configured() -> dict[str, Any]:
    return {"source_status": "not_configured"}


async def fetch_province_forecast(settings: Settings) -> dict[str, Any]:
    """Daily rain and weather condition for all 77 provinces, today + next 2 days."""
    if not settings.tmd_api_key:
        return {**_not_configured(), "provinces": []}
    params = {"fields": "rain,cond", "duration": FORECAST_DAYS}
    results = await asyncio.gather(*(
        _get("forecast/location/daily/region", settings, {**params, "region": region}) for region in REGIONS
    ))
    provinces = []
    for result in results:
        for item in result.get("WeatherForecasts") or []:
            location = item.get("location") or {}
            days = []
            for forecast in item.get("forecasts") or []:
                data = forecast.get("data") or {}
                rain = data.get("rain")
                days.append({
                    "date": str(forecast.get("time", ""))[:10],
                    "rain_mm": rain,
                    "rain_class": rain_class(rain),
                    "cond": data.get("cond"),
                    "cond_label": COND_LABELS.get(data.get("cond")),
                })
            provinces.append({
                "code": int(location["geocode"]) if str(location.get("geocode", "")).isdigit() else None,
                "name": normalize_province(location.get("name")) or location.get("name"),
                "days": days,
            })
    provinces.sort(key=lambda p: p["code"] or 0)
    first_day = provinces[0]["days"][0]["date"] if provinces and provinces[0]["days"] else None
    return {"provinces": provinces, "first_date": first_day, "source_status": "live"}


async def fetch_tambon_forecast(province: str, district: str, subdistrict: str, settings: Settings) -> dict[str, Any]:
    """Next 24 h of hourly rain for one sub-district (used for flooded GISTDA areas)."""
    if not settings.tmd_api_key:
        return _not_configured()
    result = await _get("forecast/location/hourly/place", settings, {
        "province": bare_name(province), "amphoe": bare_name(district), "tambon": bare_name(subdistrict),
        "fields": "rain,cond", "duration": TAMBON_HOURS,
    })
    items = result.get("WeatherForecasts") or []
    if not items:
        return {"found": False, "source_status": "live"}
    hours = [f for f in items[0].get("forecasts") or [] if (f.get("data") or {}).get("rain") is not None]
    rain = [f["data"]["rain"] for f in hours]
    peak = max(hours, key=lambda f: f["data"]["rain"]) if hours else None
    # Codes 5-8 rise with rain intensity; 9-12 are temperature classes, not "worse" weather.
    worst_cond = max((c for f in hours if 5 <= (c := f["data"].get("cond") or 0) <= 8), default=None)
    total = round(sum(rain), 1)
    return {
        "found": True,
        "location": items[0].get("location"),
        "hours": len(hours),
        "from": hours[0]["time"] if hours else None,
        "rain_mm": total,
        "rain_class": rain_class(total),
        "peak_mm": peak["data"]["rain"] if peak else None,
        "peak_at": peak["time"] if peak and peak["data"]["rain"] > 0 else None,
        "worst_cond": worst_cond,
        "worst_cond_label": COND_LABELS.get(worst_cond),
        "source_status": "live",
    }


async def fetch_hourly_forecast(area: dict[str, Any], hours: int, settings: Settings) -> dict[str, Any]:
    """Raw TMD hourly rain for one validated administrative area, with a cache-time buffer."""
    if not settings.tmd_api_key:
        return {**_not_configured(), "area": area, "found": False, "forecasts": []}
    duration = min(48, hours + HOURLY_BUFFER_HOURS)
    params: dict[str, Any] = {"province": area["province"], "fields": "rain", "duration": duration}
    if area.get("district"):
        params["amphoe"] = area["district"]
    if area.get("subdistrict"):
        params["tambon"] = area["subdistrict"]
    result = await _get("forecast/location/hourly/place", settings, params, estimated_datapoints=duration)
    items = result.get("WeatherForecasts") or []
    if not items:
        metrics["not_found"] += 1
        return {"area": area, "found": False, "forecasts": [], "source_status": "live"}

    item = items[0]
    location = item.get("location") or {}
    returned_code = str(location.get("geocode") or "")
    if returned_code != area["code"]:
        metrics["mapping_mismatch"] += 1
        return {
            "area": area, "found": False, "mapping_mismatch": True,
            "returned_geocode": returned_code or None, "forecasts": [], "source_status": "mapping_mismatch",
        }
    forecasts = []
    for forecast in item.get("forecasts") or []:
        rain = (forecast.get("data") or {}).get("rain")
        forecasts.append({
            "time": forecast.get("time"),
            "rain_mm": float(rain) if isinstance(rain, (int, float)) else None,
        })
    forecasts.sort(key=lambda row: row.get("time") or "")
    return {
        "area": area,
        "reference_point": {"lat": location.get("lat"), "lon": location.get("lon")},
        "found": True,
        "forecasts": forecasts,
        "source_status": "live",
    }


def hourly_view(data: dict[str, Any], hours: int, now: datetime | None = None) -> dict[str, Any]:
    """Trim buffered cached data to the requested future horizon and flag gaps/nulls."""
    if not data.get("found"):
        return {**data, "hours": [], "summary": {"hours_requested": hours, "hours_returned": 0}}
    current = (now or datetime.now(BANGKOK_TZ)).astimezone(BANGKOK_TZ).replace(minute=0, second=0, microsecond=0)
    parsed: list[tuple[datetime, dict[str, Any]]] = []
    malformed = 0
    for row in data.get("forecasts") or []:
        try:
            when = datetime.fromisoformat(str(row.get("time"))).astimezone(BANGKOK_TZ)
        except (TypeError, ValueError):
            malformed += 1
            continue
        if when >= current:
            parsed.append((when, row))
    selected = parsed[:hours]
    rows = [row for _, row in selected]
    rain = [row["rain_mm"] for row in rows if row.get("rain_mm") is not None]
    gaps = sum(1 for index in range(1, len(selected)) if (selected[index][0] - selected[index - 1][0]).total_seconds() != 3600)
    partial = len(rows) < hours or len(rain) < len(rows) or gaps > 0 or malformed > 0
    peak = max(rain) if rain else None
    return {
        "area": data["area"],
        "reference_point": data.get("reference_point"),
        "found": True,
        "hours": rows,
        "summary": {
            "hours_requested": hours,
            "hours_returned": len(rows),
            "hours_with_rain": len(rain),
            "total_rain_mm": round(sum(rain), 1),
            "peak_rain_mm": peak,
            "peak_at": next((row["time"] for row in rows if row.get("rain_mm") == peak), None),
            "from": rows[0]["time"] if rows else None,
            "to": rows[-1]["time"] if rows else None,
            "gaps": gaps,
        },
        "partial": partial,
        "source_status": "partial" if partial else data.get("source_status", "live"),
    }
