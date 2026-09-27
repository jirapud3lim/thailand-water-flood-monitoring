from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import router
from backend.app.config import get_settings
from backend.app.services.cache import cache


app = FastAPI(
    title="Thailand Water Flood Monitoring API",
    version="0.2.0",
    description="Backend facade for flood, rainfall, radar, and river data.",
)
# National station layers are large JSON (rain ~2.6 MB raw); compress anything over 1 KB.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(router)
cache.max_entries = get_settings().cache_max_entries

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
