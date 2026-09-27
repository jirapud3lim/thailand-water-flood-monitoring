"""TomTom Traffic Flow raster tiles, proxied so the API key never reaches the browser."""

from backend.app.config import Settings
from backend.app.services.http import get_bytes

# "relative0" colours road speed relative to free flow on a transparent background.
TILE_URL = "https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png"
MAX_ZOOM = 22


async def fetch_traffic_tile(z: int, x: int, y: int, settings: Settings) -> bytes:
    return await get_bytes(TILE_URL.format(z=z, x=x, y=y), settings, params={"key": settings.tomtom_api_key})
