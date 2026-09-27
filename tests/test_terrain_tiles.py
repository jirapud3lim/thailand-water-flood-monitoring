from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import terrain_tiles


client = TestClient(app)


def test_terrain_tile_uses_fixed_public_host_and_caches(monkeypatch):
    calls = []

    async def fake_bytes(url, settings):
        calls.append(url)
        return terrain_tiles.PNG_SIGNATURE + b"tile"

    monkeypatch.setattr(terrain_tiles, "get_bytes", fake_bytes)
    response = client.get("/api/v1/terrain/tiles/6/49/29.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "max-age=604800" in response.headers["cache-control"]
    assert calls == ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/6/49/29.png"]


def test_terrain_rejects_bad_coordinates_without_fetch(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("unexpected upstream call")

    monkeypatch.setattr(terrain_tiles, "get_bytes", forbidden)
    assert client.get("/api/v1/terrain/tiles/13/0/0.png").status_code == 422
    assert client.get("/api/v1/terrain/tiles/6/64/29.png").status_code == 422
    assert client.get("/api/v1/terrain/tiles/6/49/64.png").status_code == 422
    assert client.get("/api/v1/terrain/tiles/6/-1/0.png").status_code in (404, 422)


def test_terrain_does_not_serve_non_png_or_oversized_tiles(monkeypatch):
    async def html(*args, **kwargs):
        return b"<html>upstream error</html>"

    monkeypatch.setattr(terrain_tiles, "get_bytes", html)
    assert client.get("/api/v1/terrain/tiles/6/49/29.png").status_code == 502

    async def oversized(*args, **kwargs):
        return terrain_tiles.PNG_SIGNATURE + b"x" * terrain_tiles.MAX_TILE_BYTES

    monkeypatch.setattr(terrain_tiles, "get_bytes", oversized)
    assert client.get("/api/v1/terrain/tiles/6/49/29.png").status_code == 502
