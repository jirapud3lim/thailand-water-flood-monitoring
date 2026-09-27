"""Province-matched links from a vetted public-broadcaster video feed.

The YouTube Atom feed is documented for channel notifications and needs no API
key. It contains only a short window of recent uploads; an empty match must not
be interpreted as evidence that a province has no flood reports.
"""

import re
from datetime import datetime, timedelta
from xml.etree import ElementTree

from backend.app.config import Settings
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.http import get_response
from backend.app.services.local_news import TOPIC
from backend.app.services.provinces import normalize_province

CHANNEL_ID = "UCwlP2cdknwTQueF3x866CEg"  # News NBT2HD; verified against its YouTube channel page
CHANNEL_NAME = "News NBT2HD"
CHANNEL_URL = "https://www.youtube.com/@newsnbt2hd"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"
MAX_FEED_BYTES = 2_000_000
MAX_AGE = timedelta(days=7)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
SOCIAL_TOPIC = re.compile(f"{TOPIC.pattern}|สถานการณ์น้ำ|เฝ้าระวังน้ำ")
PLACE_ALIASES = {
    "กรุงเทพมหานคร": ("กรุงเทพมหานคร", "กรุงเทพฯ", "กทม."),
    "พระนครศรีอยุธยา": ("พระนครศรีอยุธยา", "อยุธยา"),
    "นครราชสีมา": ("นครราชสีมา", "โคราช"),
    # These bare names are common words or river names; require a location cue.
    "เลย": ("จังหวัดเลย", "จ.เลย", "เมืองเลย"),
    "แพร่": ("จังหวัดแพร่", "จ.แพร่", "เมืองแพร่"),
    "ตาก": ("จังหวัดตาก", "จ.ตาก", "เมืองตาก"),
    "น่าน": ("จังหวัดน่าน", "จ.น่าน", "เมืองน่าน"),
}


def _published(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


async def fetch_official_video_feed(settings: Settings) -> list[dict]:
    """Fetch and normalize recent videos from one known channel, never arbitrary URLs."""
    response = await get_response(FEED_URL, settings, headers={"Accept": "application/atom+xml"})
    if len(response.content) > MAX_FEED_BYTES:
        raise ValueError("NBT video feed too large")
    root = ElementTree.fromstring(response.content)
    if root.tag != f"{ATOM}feed":
        raise ValueError("Invalid NBT video feed")
    now = datetime.now(BANGKOK_TZ)
    videos: list[dict] = []
    seen: set[str] = set()
    for entry in root.findall(f"{ATOM}entry"):
        if entry.findtext(f"{YT}channelId") != CHANNEL_ID:
            continue
        video_id = entry.findtext(f"{YT}videoId") or ""
        published = _published(entry.findtext(f"{ATOM}published"))
        if not VIDEO_ID.fullmatch(video_id) or video_id in seen or published is None:
            continue
        if not timedelta(0) <= now - published.astimezone(BANGKOK_TZ) <= MAX_AGE:
            continue
        title = (entry.findtext(f"{ATOM}title") or "").strip()[:240]
        description = (entry.findtext(f"{MEDIA}group/{MEDIA}description") or "").strip()[:800]
        if not title or not SOCIAL_TOPIC.search(f"{title} {description[:300]}"):
            continue
        seen.add(video_id)
        videos.append({
            "title": title,
            "description": description,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "published_at": published.isoformat(),
        })
    videos.sort(key=lambda item: item["published_at"], reverse=True)
    return videos


def for_province(videos: list[dict], province_name: str) -> dict:
    """Match explicit place text; do not infer video geolocation from channel."""
    province = normalize_province(province_name)
    if province is None:
        raise ValueError("Unknown province")
    terms = PLACE_ALIASES.get(province, (province,))
    matched = []
    for video in videos:
        title = video["title"]
        description = video["description"]
        match_field = "title" if any(term in title for term in terms) else (
            "description" if any(term in description for term in terms) else None
        )
        if not match_field:
            continue
        matched.append({
            "title": title,
            "url": video["url"],
            "published_at": video["published_at"],
            "province": province,
            "platform": "YouTube",
            "source_name": CHANNEL_NAME,
            "match_field": match_field,
        })
    return {
        "province": province,
        "items": matched[:4],
        "source_name": CHANNEL_NAME,
        "source_url": CHANNEL_URL,
        "matching": "province_text",
        "coverage": "latest_channel_uploads_only",
        "max_age_days": MAX_AGE.days,
    }
