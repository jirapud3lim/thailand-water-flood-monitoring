"""RainViewer radar tiles through our own cache.

Since 2026-01-01 the free RainViewer API allows 100 requests per IP per minute and zoom <= 7.
Smooth animation needs every frame preloaded (13 frames x the tiles in view), which one
browser would exceed on its own. Past radar frames never change, so the server fetches each
(frame, z, x, y) once, keeps it in a byte-bounded LRU, and shares it with every viewer; a
limiter keeps our own upstream calls under the per-IP budget.
"""

import asyncio
import re
import time
from collections import OrderedDict, deque

from backend.app.config import Settings
from backend.app.services.http import get_bytes

MAX_ZOOM = 7                       # RainViewer free tier
# 512 px tiles (still served on the free tier, checked 2026-09-27): a quarter of the requests of
# 256 px tiles for the same view, which keeps a cold cache inside the per-minute budget.
TILE_SIZE = 512
COLOR_SCHEME = 2                   # "Universal Blue", the only scheme left on the free tier
OPTIONS = "1_1"                    # smoothed, snow shown
FRAME_RE = re.compile(r"^[0-9a-f]{6,32}$|^\d{9,11}$")
UPSTREAM_PER_MINUTE = 90           # below RainViewer's 100/IP/min, leaving room for metadata calls
MAX_WAIT_SECONDS = 8.0
CACHE_BYTES = 96 * 1024 * 1024


class RateLimited(Exception):
    """Upstream budget used up for longer than a tile request should wait."""


class TileLimiter:
    """Sliding one-minute window; callers wait for a slot instead of failing straight away."""

    def __init__(self, per_minute: int = UPSTREAM_PER_MINUTE, clock=time.monotonic, sleep=asyncio.sleep) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, max_wait: float = MAX_WAIT_SECONDS) -> None:
        async with self._lock:
            now = self._clock()
            while self._stamps and now - self._stamps[0] >= 60:
                self._stamps.popleft()
            if len(self._stamps) >= self.per_minute:
                wait = 60 - (now - self._stamps[0])
                if wait > max_wait:
                    raise RateLimited(f"radar tile budget used, retry in {wait:.0f}s")
                await self._sleep(wait)
                now = self._clock()
                self._stamps.popleft()
            self._stamps.append(now)


class TileCache:
    """LRU bounded by total bytes; concurrent requests for one tile share a single fetch."""

    def __init__(self, max_bytes: int = CACHE_BYTES) -> None:
        self.max_bytes = max_bytes
        self._items: OrderedDict[tuple, bytes] = OrderedDict()
        self._size = 0
        self._inflight: dict[tuple, asyncio.Future] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> bytes | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
            self.hits += 1
        return value

    def put(self, key: tuple, value: bytes) -> None:
        if key in self._items:
            self._size -= len(self._items.pop(key))
        self._items[key] = value
        self._size += len(value)
        while self._size > self.max_bytes and self._items:
            _, old = self._items.popitem(last=False)
            self._size -= len(old)

    async def load(self, key: tuple, fetch) -> bytes:
        cached = self.get(key)
        if cached is not None:
            return cached
        if key in self._inflight:
            return await asyncio.shield(self._inflight[key])
        self.misses += 1
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await fetch()
            self.put(key, value)
            future.set_result(value)
            return value
        except BaseException as error:
            future.set_exception(error)
            future.exception()  # mark retrieved so a waiter-less failure isn't logged
            raise
        finally:
            del self._inflight[key]

    def clear(self) -> None:
        self._items.clear()
        self._size = 0


tile_cache = TileCache()
limiter = TileLimiter()


def frame_id(path: str) -> str:
    """'/v2/radar/66d556dbcc25' -> '66d556dbcc25'."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def current_frames(metadata: dict) -> dict[str, str]:
    """Frame id -> upstream path for the frames RainViewer currently lists."""
    radar = metadata.get("radar") or {}
    frames = (radar.get("past") or []) + (radar.get("nowcast") or [])
    return {frame_id(f["path"]): f["path"] for f in frames if f.get("path")}


async def fetch_radar_tile(host: str, path: str, z: int, x: int, y: int, settings: Settings) -> bytes:
    key = (path, z, x, y)

    async def fetch() -> bytes:
        await limiter.acquire()
        return await get_bytes(f"{host}{path}/{TILE_SIZE}/{z}/{x}/{y}/{COLOR_SCHEME}/{OPTIONS}.png", settings)

    return await tile_cache.load(key, fetch)
