"""Shared upstream HTTP client with bounded retry for transient failures."""

import asyncio
from typing import Any

import httpx

from backend.app.config import Settings

USER_AGENT = "thailand-flood-monitor/0.2 (+https://github.com/; flood situational awareness)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def get_json(
    url: str,
    settings: Settings,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET JSON; retry timeouts, connection errors, 429 and 5xx with exponential backoff.

    4xx other than 429 is a caller/config problem and is never retried.
    """
    return (await get_response(url, settings, params=params, headers=headers)).json()


async def get_response(
    url: str,
    settings: Settings,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Same retry policy as get_json, but returns the response (for callers that read headers)."""
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    attempts = 1 + max(0, settings.upstream_retries)
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=merged) as client:
        for attempt in range(attempts):
            try:
                response = await client.get(url, params=params)
                if response.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
                    await asyncio.sleep(_backoff(attempt, response))
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= attempts - 1:
                    raise
                await asyncio.sleep(_backoff(attempt))
    raise RuntimeError("unreachable")  # pragma: no cover


_tile_client: httpx.AsyncClient | None = None


async def get_bytes(
    url: str,
    settings: Settings,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET raw bytes (map tiles) on one pooled client, without retry.

    A map view loads ~20 tiles at once, so per-request TLS setup adds up; a failed
    tile is cheaper to drop than to retry because the map re-requests on pan.
    """
    global _tile_client
    if _tile_client is None or _tile_client.is_closed:
        _tile_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers={"User-Agent": USER_AGENT})
    response = await _tile_client.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.content


def _backoff(attempt: int, response: httpx.Response | None = None) -> float:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 10.0)
    return 0.5 * (2 ** attempt)
