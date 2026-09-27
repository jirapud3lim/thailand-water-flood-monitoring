"""Fixed-host proxy for public Mapzen Terrarium elevation tiles on AWS.

Only z/x/y numbers supplied by the client enter the URL. The upstream tiles
are static, so browsers can cache them; no API key or user-controlled host is
involved. These are mixed-source terrain tiles, not a flood-risk product.
"""

from backend.app.config import Settings
from backend.app.services.http import get_bytes

MAX_ZOOM = 12  # SRTM's nominal ~30 m resolution; higher tiles only oversample it in Thailand.
MAX_TILE_BYTES = 1_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TILE_HOST = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium"


async def fetch_terrain_tile(z: int, x: int, y: int, settings: Settings) -> bytes:
    content = await get_bytes(f"{TILE_HOST}/{z}/{x}/{y}.png", settings)
    if not content.startswith(PNG_SIGNATURE) or len(content) > MAX_TILE_BYTES:
        raise ValueError("Invalid terrain PNG tile")
    return content
