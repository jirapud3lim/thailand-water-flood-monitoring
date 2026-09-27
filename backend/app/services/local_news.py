"""Recent, province-matched water news from official PRD JSON feeds.

PRD's provincial directory lists 76 `<province>.prd.go.th` sites. The sole
hostname exception is Ayutthaya; Bangkok uses the central PRD feed. Feed ids
vary by site, so discover the general-news (`cid/9`) JSON link on its RSS page.
"""

import html
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from backend.app.config import Settings
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.http import get_json, get_response
from backend.app.services.provinces import province as find_province

MAX_AGE = timedelta(days=7)
TOPIC = re.compile(r"น้ำท่วม|อุทกภัย|น้ำป่า|น้ำหลาก|น้ำล้น|น้ำขัง|ฝนตกหนัก|ฝนสะสม|ระบายน้ำ|เขื่อน|ระดับน้ำ|ดินถล่ม|พายุฝน")


class _FeedLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href and "contentjson" in href and re.search(r"/cid/9(?:$|[?#])", href):
                self.links.append(href)


def _host(province_name: str) -> str:
    row = find_province(province_name)
    if row is None:
        raise ValueError("Unknown province")
    if row["name"] == "กรุงเทพมหานคร":
        return "www.prd.go.th"
    slug = "".join(c for c in row["name_en"].lower() if c.isascii() and c.isalpha())
    if row["name"] == "พระนครศรีอยุธยา":
        slug = "ayutthaya"
    return f"{slug}.prd.go.th"


def _safe_url(url: str, host: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname == host and parsed.port is None and not parsed.username
    except ValueError:
        return False


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


async def fetch_local_news(province_name: str, settings: Settings) -> dict:
    """Return only current water news explicitly mentioning the requested province.

    A provincial feed can contain stories from elsewhere. Feed location alone
    never counts as a match; no story is attributed to a district or station.
    """
    row = find_province(province_name)
    if row is None:
        raise ValueError("Unknown province")
    name = row["name"]
    host = _host(name)
    source_url = f"https://{host}/th/rss/page/index/id/1"
    landing = await get_response(source_url, settings, headers={"Accept": "text/html"})
    parser = _FeedLinks()
    parser.feed(landing.text)
    feeds = [urljoin(source_url, href) for href in parser.links]
    feed_url = next((url for url in feeds if _safe_url(url, host)), None)
    if feed_url is None:
        raise ValueError("PRD general-news JSON feed unavailable")
    data = await get_json(feed_url, settings)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Invalid PRD JSON feed")

    now = datetime.now(BANGKOK_TZ)
    stories = []
    seen: set[str] = set()
    place_terms = ("กรุงเทพมหานคร", "กรุงเทพฯ", "กทม.") if name == "กรุงเทพมหานคร" else (name,)
    for item in data["items"][:50]:
        if not isinstance(item, dict):
            continue
        title = _plain(str(item.get("title") or ""))[:240]
        url = str(item.get("url") or "")
        if not title or not _safe_url(url, host) or url in seen:
            continue
        try:
            published = datetime.fromisoformat(str(item.get("date_published") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if published.tzinfo is None or not timedelta(0) <= now - published.astimezone(BANGKOK_TZ) <= MAX_AGE:
            continue
        lead = _plain(str(item.get("content_html") or ""))[:1200]
        if not TOPIC.search(f"{title} {lead[:400]}") or not any(term in f"{title} {lead}" for term in place_terms):
            continue
        seen.add(url)
        stories.append({"title": title, "url": url, "published_at": published.isoformat(), "province": name})
    stories.sort(key=lambda item: item["published_at"], reverse=True)
    return {
        "province": name,
        "items": stories[:5],
        "source_name": "กรมประชาสัมพันธ์",
        "source_url": source_url,
        "matching": "province_text",
        "max_age_days": MAX_AGE.days,
    }
