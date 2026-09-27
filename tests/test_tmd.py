import httpx
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.services import tmd
from backend.app.services.cache import cache
from backend.app.services.freshness import BANGKOK_TZ


client = TestClient(app)
TOKEN = "tmd-token"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def with_key():
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, tmd_api_key=TOKEN)


def _response(payload, remaining="99000"):
    request = httpx.Request("GET", "https://data.tmd.go.th/nwpapi/v1/x")
    return httpx.Response(200, json=payload, request=request,
                          headers={"X-Datapoint-Limit": "100000", "X-Datapoint-Remaining": remaining})


def _region(names_codes):
    return {"WeatherForecasts": [{
        "location": {"name": name, "geocode": code, "areatype": "province"},
        "forecasts": [{"time": f"2026-09-{27 + i}T00:00:00+07:00", "data": {"rain": rain, "cond": 4}} for i, rain in enumerate(rains)],
    } for name, code, rains in names_codes]}


def test_settings_accepts_misspelled_env_name(monkeypatch) -> None:
    monkeypatch.setenv("TDM_API_KEY", "from-typo")
    assert Settings(_env_file=None).tmd_api_key == "from-typo"


@pytest.mark.parametrize("mm, expected", [(None, None), (0, None), (0.4, "เล็กน้อย"), (10.0, "เล็กน้อย"),
                                          (10.1, "ปานกลาง"), (35.1, "หนัก"), (90.1, "หนักมาก")])
def test_rain_class_uses_tmd_24h_bands(mm, expected) -> None:
    assert tmd.rain_class(mm) == expected


def test_bare_name_strips_admin_prefixes() -> None:
    assert [tmd.bare_name(n) for n in ("จ.สุโขทัย", "อ.กงไกรลาศ", "ต.ท่าฉนวน", "เขตบางนา", "ท่าฉนวน")] == [
        "สุโขทัย", "กงไกรลาศ", "ท่าฉนวน", "บางนา", "ท่าฉนวน"]


@pytest.mark.anyio
async def test_province_forecast_covers_regions_and_tracks_quota(monkeypatch) -> None:
    calls = []

    async def fake(url, settings, *, params=None, headers=None):
        calls.append((params["region"], headers["Authorization"]))
        data = {"C": [("สุโขทัย", "64", [2.0, 48.0, 5.0])], "S": [("ยะลา", "95", [0, 52.4, 0])]}.get(params["region"], [])
        return _response(_region(data), remaining="98500")

    monkeypatch.setattr(tmd, "get_response", fake)
    data = await tmd.fetch_province_forecast(Settings(_env_file=None, tmd_api_key=TOKEN))
    assert sorted(r for r, _ in calls) == sorted(tmd.REGIONS)
    assert all(auth == f"Bearer {TOKEN}" for _, auth in calls)
    assert [(p["code"], p["name"]) for p in data["provinces"]] == [(64, "สุโขทัย"), (95, "ยะลา")]
    tomorrow = data["provinces"][0]["days"][1]
    assert tomorrow == {"date": "2026-09-28", "rain_mm": 48.0, "rain_class": "หนัก", "cond": 4, "cond_label": "มีเมฆมาก"}
    assert tmd.quota["remaining"] == 98500 and tmd.quota["limit"] == 100000


@pytest.mark.anyio
async def test_tambon_forecast_sums_rain_and_picks_rain_condition(monkeypatch) -> None:
    seen = {}

    async def fake(url, settings, *, params=None, headers=None):
        seen.update(params)
        hours = [(0.0, 3), (4.5, 7), (12.0, 8), (0.5, 12)]
        return _response({"WeatherForecasts": [{"location": {"name": "ท่าฉนวน"}, "forecasts": [
            {"time": f"2026-09-27T{20 + i}:00:00+07:00", "data": {"rain": r, "cond": c}} for i, (r, c) in enumerate(hours)]}]})

    monkeypatch.setattr(tmd, "get_response", fake)
    f = await tmd.fetch_tambon_forecast("จ.สุโขทัย", "อ.กงไกรลาศ", "ต.ท่าฉนวน", Settings(_env_file=None, tmd_api_key=TOKEN))
    assert (seen["province"], seen["amphoe"], seen["tambon"]) == ("สุโขทัย", "กงไกรลาศ", "ท่าฉนวน")
    assert f["rain_mm"] == 17.0 and f["rain_class"] == "ปานกลาง"
    assert f["peak_mm"] == 12.0 and f["peak_at"] == "2026-09-27T22:00:00+07:00"
    assert f["worst_cond"] == 8  # 12 ("very hot") is a temperature class, not worse rain


@pytest.mark.anyio
async def test_hourly_forecast_uses_validated_area_and_preserves_hours(monkeypatch) -> None:
    seen = {}

    async def fake(url, settings, *, params=None, headers=None):
        seen.update(params)
        return _response({"WeatherForecasts": [{
            "location": {"name": "ไร่ขิง", "geocode": "730608", "lat": 13.74, "lon": 100.27},
            "forecasts": [
                {"time": f"2026-09-28T{i:02d}:00:00+07:00", "data": {"rain": i / 10}}
                for i in range(24)
            ],
        }]})

    monkeypatch.setattr(tmd, "get_response", fake)
    area = {"code": "730608", "level": "subdistrict", "province": "นครปฐม", "district": "สามพราน", "subdistrict": "ไร่ขิง"}
    result = await tmd.fetch_hourly_forecast(area, 24, Settings(_env_file=None, tmd_api_key=TOKEN))
    assert seen == {"province": "นครปฐม", "amphoe": "สามพราน", "tambon": "ไร่ขิง", "fields": "rain", "duration": 27}
    assert result["found"] is True and len(result["forecasts"]) == 24
    assert result["forecasts"][5]["rain_mm"] == 0.5
    assert result["reference_point"] == {"lat": 13.74, "lon": 100.27}


def test_hourly_view_flags_missing_or_null_hours() -> None:
    now = datetime(2026, 9, 28, 8, 35, tzinfo=BANGKOK_TZ)
    raw = {
        "area": {"code": "73"}, "found": True, "source_status": "live", "reference_point": {},
        "forecasts": [
            {"time": "2026-09-28T08:00:00+07:00", "rain_mm": 1.0},
            {"time": "2026-09-28T09:00:00+07:00", "rain_mm": None},
            {"time": "2026-09-28T11:00:00+07:00", "rain_mm": 3.0},
        ],
    }
    view = tmd.hourly_view(raw, 24, now)
    assert view["partial"] is True and view["source_status"] == "partial"
    assert view["summary"]["hours_returned"] == 3
    assert view["summary"]["hours_with_rain"] == 2
    assert view["summary"]["total_rain_mm"] == 4.0
    assert view["summary"]["gaps"] == 1


@pytest.mark.anyio
async def test_hourly_forecast_rejects_tmd_geocode_mismatch(monkeypatch) -> None:
    async def fake(url, settings, *, params=None, headers=None):
        return _response({"WeatherForecasts": [{"location": {"geocode": "730607"}, "forecasts": []}]})

    monkeypatch.setattr(tmd, "get_response", fake)
    area = {"code": "730608", "level": "subdistrict", "province": "นครปฐม", "district": "สามพราน", "subdistrict": "ไร่ขิง"}
    result = await tmd.fetch_hourly_forecast(area, 24, Settings(_env_file=None, tmd_api_key=TOKEN))
    assert result["found"] is False
    assert result["mapping_mismatch"] is True
    assert result["source_status"] == "mapping_mismatch"


@pytest.mark.anyio
async def test_hourly_forecast_stops_before_spending_reserved_quota(monkeypatch) -> None:
    called = False

    async def fake(url, settings, *, params=None, headers=None):
        nonlocal called
        called = True
        return _response({})

    monkeypatch.setattr(tmd, "get_response", fake)
    previous = dict(tmd.quota)
    tmd.quota.update(limit=100000, remaining=5010, checked_at="test")
    tmd._request_times.clear()
    area = {"code": "73", "level": "province", "province": "นครปฐม", "district": None, "subdistrict": None}
    try:
        with pytest.raises(tmd.TMDBudgetError, match="reserve"):
            await tmd.fetch_hourly_forecast(area, 24, Settings(_env_file=None, tmd_api_key=TOKEN))
        assert called is False
    finally:
        tmd.quota.clear()
        tmd.quota.update(previous)


def test_routes_report_not_configured_without_key() -> None:
    assert client.get("/api/v1/forecast/provinces").json()["meta"]["status"] == "not_configured"
    status = {s["id"]: s for s in client.get("/api/v1/sources/status").json()["sources"]}
    assert status["tmd"]["status"] == "not_configured" and "quota" in status["tmd"]


def test_tambon_route_validates_province(with_key) -> None:
    assert client.get("/api/v1/forecast/tambon", params={"province": "ไม่มี", "district": "a", "subdistrict": "b"}).status_code == 422
    assert client.get("/api/v1/forecast/tambon", params={"province": "สุโขทัย", "district": "", "subdistrict": "b"}).status_code == 422


def test_area_routes_are_cascading_and_validate_codes() -> None:
    provinces_response = client.get("/api/v1/areas")
    assert provinces_response.status_code == 200
    assert len(provinces_response.json()["areas"]) == 77
    districts = client.get("/api/v1/areas", params={"parent_code": "73"}).json()["areas"]
    assert {row["name"] for row in districts} >= {"เมืองนครปฐม", "สามพราน"}
    subdistricts = client.get("/api/v1/areas", params={"parent_code": "7306"}).json()["areas"]
    assert {row["code"] for row in subdistricts} >= {"730608"}
    assert client.get("/api/v1/areas", params={"parent_code": "9999"}).status_code == 404


def test_hourly_route_validates_area_before_upstream(with_key) -> None:
    assert client.get("/api/v1/forecast/hourly", params={"area_code": "999999"}).status_code == 404
    assert client.get("/api/v1/forecast/hourly", params={"area_code": "730608", "hours": 25}).status_code == 422


def test_hourly_route_caches_per_area(monkeypatch, with_key) -> None:
    calls = {"n": 0}
    start = datetime.now(BANGKOK_TZ).replace(minute=0, second=0, microsecond=0)

    async def fake(area, hours, settings):
        calls["n"] += 1
        return {
            "area": area, "found": True, "source_status": "live", "reference_point": {"lat": 13.74, "lon": 100.27},
            "forecasts": [
                {"time": (start + timedelta(hours=i)).isoformat(), "rain_mm": float(i % 3)} for i in range(27)
            ],
        }

    monkeypatch.setattr(tmd, "fetch_hourly_forecast", fake)
    params = {"area_code": "730608", "hours": 24}
    first = client.get("/api/v1/forecast/hourly", params=params).json()
    second = client.get("/api/v1/forecast/hourly", params=params).json()
    assert calls["n"] == 1
    assert first["data"]["area"]["subdistrict"] == "ไร่ขิง"
    assert first["data"]["summary"]["hours_returned"] == 24
    assert first["meta"]["status"] == "live" and second["meta"]["status"] == "cached"


def test_hourly_route_reports_local_quota_guard(monkeypatch, with_key) -> None:
    async def fake(area, hours, settings):
        raise tmd.TMDBudgetError("TMD datapoint reserve reached")

    monkeypatch.setattr(tmd, "fetch_hourly_forecast", fake)
    response = client.get("/api/v1/forecast/hourly", params={"area_code": "730608"})
    assert response.status_code == 429
    assert response.json()["detail"]["status"] == "rate_limited"


def test_tambon_route_caches_per_area(monkeypatch, with_key) -> None:
    calls = {"n": 0}

    async def fake(province, district, subdistrict, settings):
        calls["n"] += 1
        return {"found": True, "rain_mm": 3.0, "source_status": "live"}

    monkeypatch.setattr(tmd, "fetch_tambon_forecast", fake)
    params = {"province": "จ.สุโขทัย", "district": "อ.กงไกรลาศ", "subdistrict": "ต.ท่าฉนวน"}
    assert client.get("/api/v1/forecast/tambon", params=params).json()["data"]["rain_mm"] == 3.0
    client.get("/api/v1/forecast/tambon", params={**params, "district": "กงไกรลาศ", "subdistrict": "ท่าฉนวน"})
    assert calls["n"] == 1  # prefixed and bare names share one cache entry
