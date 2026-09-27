"""Real photos of the large dams, from Wikimedia Commons, with attribution.

`data/dam_photos.json` maps each RID dam name to a Commons file that was checked by eye on
2026-09-27 to actually show that dam or its reservoir (Wikipedia's automatic page image
was wrong for several dams, e.g. a temple for ลำนางรอง). Ten dams have no suitable free
photo and are left out; the UI falls back to the dam icon.

License and author are read live from Commons, and anything flagged non-free is dropped,
so a relicensed or deleted file never shows without correct credit.
"""

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.services.http import get_json

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "dam_photos.json"
THUMB_WIDTH = 480
TAG_RE = re.compile(r"<[^>]+>")


@lru_cache
def photo_files() -> dict[str, str]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _text(meta: dict[str, Any], key: str) -> str | None:
    value = (meta.get(key) or {}).get("value")
    if not value:
        return None
    return " ".join(html.unescape(TAG_RE.sub("", str(value))).split()) or None


async def fetch_dam_photos(settings: Settings) -> dict[str, Any]:
    files = photo_files()
    # One batched request: the Commons API accepts up to 50 titles.
    result = await get_json(COMMONS_API, settings, params={
        "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiurlwidth": THUMB_WIDTH, "titles": "|".join(f"File:{name}" for name in files.values()),
    })
    query = result.get("query") or {}
    # Commons normalizes titles (e.g. "_" -> " "); map back to what we asked for.
    asked = {entry["to"]: entry["from"] for entry in query.get("normalized") or []}
    by_file = {}
    for page in (query.get("pages") or {}).values():
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = info.get("extmetadata") or {}
        if _text(meta, "NonFree"):
            continue
        by_file[asked.get(page["title"], page["title"]).removeprefix("File:")] = {
            "thumb_url": info.get("thumburl"),
            "page_url": info.get("descriptionurl"),
            "license": _text(meta, "LicenseShortName"),
            "license_url": _text(meta, "LicenseUrl"),
            "artist": _text(meta, "Artist"),
        }
    photos = {dam: by_file[f] for dam, f in files.items() if f in by_file and by_file[f]["thumb_url"]}
    return {"photos": photos, "source": "Wikimedia Commons", "source_status": "live"}
