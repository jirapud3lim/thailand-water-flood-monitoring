import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from backend.app.services.freshness import BANGKOK_TZ


def payload_version(value: Any) -> str:
    """Short content hash; changes only when the normalized payload changes."""
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:12]


@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    fetched_at: datetime
    version: str


@dataclass
class CacheResult:
    value: Any
    status: str  # "live" (just fetched) | "cached" (within TTL) | "stale" (upstream failed, old value)
    fetched_at: datetime
    version: str


@dataclass
class KeyHealth:
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    hits: int = 0
    misses: int = 0
    stale_served: int = 0
    upstream_errors: int = 0


class AsyncTTLCache:
    """In-process TTL cache with request coalescing, stale fallback and LRU bound.

    `clock` is injectable so tests can move time without sleeping.
    """

    def __init__(self, max_entries: int = 512, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._health: OrderedDict[str, KeyHealth] = OrderedDict()

    def _fresh(self, key: str) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry and entry.expires_at > self._clock():
            self._entries.move_to_end(key)
            return entry
        return None

    def _store(self, key: str, entry: CacheEntry) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            evicted, _ = self._entries.popitem(last=False)
            lock = self._locks.get(evicted)
            if lock is not None and not lock.locked():
                del self._locks[evicted]
        while len(self._health) > self.max_entries * 2:
            self._health.popitem(last=False)

    async def load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl_seconds: int,
    ) -> CacheResult:
        health = self._health.setdefault(key, KeyHealth())
        entry = self._fresh(key)
        if entry:
            health.hits += 1
            return CacheResult(entry.value, "cached", entry.fetched_at, entry.version)

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._fresh(key)
            if entry:  # another request refreshed it while we waited
                health.hits += 1
                return CacheResult(entry.value, "cached", entry.fetched_at, entry.version)
            health.misses += 1
            previous = self._entries.get(key)
            try:
                value = await loader()
            except Exception as exc:
                health.upstream_errors += 1
                health.last_error_at = datetime.now(BANGKOK_TZ)
                health.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                if previous:
                    health.stale_served += 1
                    return CacheResult(previous.value, "stale", previous.fetched_at, previous.version)
                raise
            fetched_at = datetime.now(BANGKOK_TZ)
            health.last_success_at = fetched_at
            entry = CacheEntry(value, self._clock() + ttl_seconds, fetched_at, payload_version(value))
            self._store(key, entry)
            return CacheResult(value, "live", fetched_at, entry.version)

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl_seconds: int,
    ) -> tuple[Any, bool]:
        """Backward-compatible wrapper: returns (value, stale)."""
        result = await self.load(key, loader, ttl_seconds)
        return result.value, result.status == "stale"

    def clear(self) -> None:
        self._entries.clear()
        self._locks.clear()
        self._health.clear()

    def health(self) -> dict[str, dict[str, Any]]:
        """Per-key freshness, error and hit/miss info, without touching any upstream."""
        now = self._clock()
        report = {}
        for key in sorted(set(self._entries) | set(self._health)):
            entry = self._entries.get(key)
            health = self._health.get(key, KeyHealth())
            report[key] = {
                "cached": entry is not None,
                "expired": entry is None or entry.expires_at <= now,
                "fetched_at": entry.fetched_at.isoformat(timespec="seconds") if entry else None,
                "last_error_at": health.last_error_at.isoformat(timespec="seconds") if health.last_error_at else None,
                "last_error": health.last_error,
                "failing": bool(
                    health.last_error_at
                    and (health.last_success_at is None or health.last_error_at > health.last_success_at)
                ),
                "hits": health.hits,
                "misses": health.misses,
                "stale_served": health.stale_served,
                "upstream_errors": health.upstream_errors,
            }
        return report


cache = AsyncTTLCache()
