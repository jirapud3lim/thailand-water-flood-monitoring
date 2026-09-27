import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.main import app
from backend.app.services import radar_tiles
from backend.app.services.cache import cache


client = TestClient(app)
METADATA = {"host": "https://tiles.example", "radar": {"past": [{"time": 1, "path": "/v2/radar/abc123def456"}], "nowcast": []}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    cache.clear()
    radar_tiles.tile_cache.clear()
    radar_tiles.limiter._stamps.clear()

    async def fake_radar(settings):
        return METADATA

    monkeypatch.setattr(routes, "fetch_radar", fake_radar)
    yield
    cache.clear()
    radar_tiles.tile_cache.clear()


def test_tile_is_fetched_once_and_then_served_from_cache(monkeypatch):
    calls = []

    async def fake_bytes(url, settings, **kwargs):
        calls.append(url)
        return b"PNG"

    monkeypatch.setattr(radar_tiles, "get_bytes", fake_bytes)
    first = client.get("/api/v1/radar/tiles/abc123def456/5/25/14.png")
    second = client.get("/api/v1/radar/tiles/abc123def456/5/25/14.png")
    assert first.status_code == second.status_code == 200
    assert first.content == b"PNG" and first.headers["content-type"] == "image/png"
    assert "immutable" in first.headers["cache-control"]
    assert calls == ["https://tiles.example/v2/radar/abc123def456/512/5/25/14/2/1_1.png"]


def test_only_listed_frames_and_free_tier_zooms(monkeypatch):
    async def fake_bytes(url, settings, **kwargs):
        raise AssertionError("must not reach upstream")

    monkeypatch.setattr(radar_tiles, "get_bytes", fake_bytes)
    assert client.get("/api/v1/radar/tiles/000000000000/5/25/14.png").status_code == 404   # not listed
    assert client.get("/api/v1/radar/tiles/..%2Fetc/5/25/14.png").status_code in (404, 422)
    assert client.get("/api/v1/radar/tiles/abc123def456/8/1/1.png").status_code == 422     # zoom > 7
    assert client.get("/api/v1/radar/tiles/abc123def456/2/9/0.png").status_code == 422     # x out of range


def test_limiter_waits_then_refuses_long_waits():
    now = [0.0]
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = radar_tiles.TileLimiter(per_minute=2, clock=lambda: now[0], sleep=fake_sleep)

    async def run():
        await limiter.acquire()                    # t=0
        now[0] = 10.0
        await limiter.acquire()                    # t=10: budget of 2 used
        now[0] = 55.0
        await limiter.acquire(max_wait=8)          # t=0 slot frees in 5 s: waits
        with pytest.raises(radar_tiles.RateLimited):
            await limiter.acquire(max_wait=1)      # t=10 slot frees in 10 s: refuses

    asyncio.run(run())
    assert slept == [5.0]


def test_concurrent_requests_share_one_fetch():
    calls = []

    async def run():
        async def fetch():
            calls.append(1)
            await asyncio.sleep(0.01)
            return b"x"

        cache_ = radar_tiles.TileCache()
        results = await asyncio.gather(*(cache_.load(("p", 1, 0, 0), fetch) for _ in range(5)))
        return results

    assert asyncio.run(run()) == [b"x"] * 5
    assert calls == [1]
