import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.config import Settings, get_settings
from backend.app.main import app


client = TestClient(app)


def _override(key: str):
    # Explicit key so a real TOMTOM_API_KEY in a local .env never changes results.
    app.dependency_overrides[get_settings] = lambda: Settings(tomtom_api_key=key)
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def with_key():
    yield from _override("secret-key")


@pytest.fixture
def without_key():
    yield from _override("")


def test_status_reports_not_configured_without_key(without_key) -> None:
    assert client.get("/api/v1/traffic/status").json()["configured"] is False


def test_tile_is_404_without_key(monkeypatch, without_key) -> None:
    async def must_not_run(*args, **kwargs):
        raise AssertionError("must not call TomTom without a key")

    monkeypatch.setattr(routes, "fetch_traffic_tile", must_not_run)
    assert client.get("/api/v1/traffic/tiles/10/800/480.png").status_code == 404


def test_tile_proxies_png(monkeypatch, with_key) -> None:
    seen = {}

    async def fake(z, x, y, settings):
        seen.update(z=z, x=x, y=y, key=settings.tomtom_api_key)
        return b"\x89PNG"

    monkeypatch.setattr(routes, "fetch_traffic_tile", fake)
    response = client.get("/api/v1/traffic/tiles/10/800/480.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG"
    assert seen == {"z": 10, "x": 800, "y": 480, "key": "secret-key"}
    assert client.get("/api/v1/traffic/status").json()["configured"] is True


def test_tile_rejects_out_of_range_coordinates(with_key) -> None:
    assert client.get("/api/v1/traffic/tiles/2/4/0.png").status_code == 422
    assert client.get("/api/v1/traffic/tiles/23/0/0.png").status_code == 422


def test_upstream_error_does_not_leak_key(monkeypatch, with_key) -> None:
    async def failing(z, x, y, settings):
        request = httpx.Request("GET", f"https://api.tomtom.com/tile?key={settings.tomtom_api_key}")
        raise httpx.HTTPStatusError("403", request=request, response=httpx.Response(403, request=request))

    monkeypatch.setattr(routes, "fetch_traffic_tile", failing)
    response = client.get("/api/v1/traffic/tiles/10/800/480.png")
    assert response.status_code == 502
    assert "secret-key" not in response.text
    assert "403" in response.text
