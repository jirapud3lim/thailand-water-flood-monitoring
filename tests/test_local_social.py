from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from backend.app.main import app
from backend.app.services import local_social
from backend.app.services.cache import cache
from backend.app.services.freshness import BANGKOK_TZ


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _entry(video_id, title, description, published, channel_id=local_social.CHANNEL_ID):
    return f"""<entry>
      <yt:videoId>{video_id}</yt:videoId><yt:channelId>{channel_id}</yt:channelId>
      <title>{title}</title><published>{published}</published>
      <media:group><media:description>{description}</media:description></media:group>
    </entry>"""


def test_social_video_feed_filters_source_topic_age_and_province(monkeypatch):
    recent = datetime.now(BANGKOK_TZ).isoformat()
    old = (datetime.now(BANGKOK_TZ) - timedelta(days=8)).isoformat()
    entries = [
        _entry("AAAAAAAAAAA", "สุโขทัย น้ำท่วม", "รายงานจากพื้นที่", recent),
        _entry("BBBBBBBBBBB", "เชียงใหม่ ฝนตกหนัก", "เชียงใหม่", recent),
        _entry("CCCCCCCCCCC", "สุโขทัย งานกีฬา", "ทั่วไป", recent),
        _entry("DDDDDDDDDDD", "สุโขทัย น้ำท่วม", "เก่า", old),
        _entry("EEEEEEEEEEE", "สุโขทัย น้ำท่วม", "ปลอม", recent, channel_id="UCinvalid"),
        _entry("not-a-video-id", "สุโขทัย น้ำท่วม", "ปลอม", recent),
    ]
    xml = ("<feed xmlns=\"http://www.w3.org/2005/Atom\" "
           "xmlns:yt=\"http://www.youtube.com/xml/schemas/2015\" "
           "xmlns:media=\"http://search.yahoo.com/mrss/\">" + "".join(entries) + "</feed>").encode()
    calls = []

    async def fake_response(url, settings, *, headers=None):
        calls.append(url)
        return type("Response", (), {"content": xml})()

    monkeypatch.setattr(local_social, "get_response", fake_response)
    first = client.get("/api/v1/local-social", params={"province": "สุโขทัย"})
    assert first.status_code == 200
    assert [item["url"] for item in first.json()["data"]["items"]] == ["https://www.youtube.com/watch?v=AAAAAAAAAAA"]
    assert first.json()["data"]["items"][0]["match_field"] == "title"
    assert first.json()["data"]["coverage"] == "latest_channel_uploads_only"
    second = client.get("/api/v1/local-social", params={"province": "เชียงใหม่"})
    assert [item["url"] for item in second.json()["data"]["items"]] == ["https://www.youtube.com/watch?v=BBBBBBBBBBB"]
    assert second.json()["meta"]["status"] == "cached"
    assert calls == [local_social.FEED_URL]


def test_social_rejects_unknown_province_without_fetch(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected upstream call")

    monkeypatch.setattr(local_social, "get_response", forbidden)
    assert client.get("/api/v1/local-social", params={"province": "แอตแลนติส"}).status_code == 422


def test_social_rejects_invalid_feed(monkeypatch):
    async def fake_response(url, settings, *, headers=None):
        return type("Response", (), {"content": b"<html/>"})()

    monkeypatch.setattr(local_social, "get_response", fake_response)
    response = client.get("/api/v1/local-social", params={"province": "สุโขทัย"})
    assert response.status_code == 503


def test_social_description_match_does_not_claim_point_location():
    videos = [{
        "title": "ติดตามสถานการณ์น้ำล่าสุด", "description": "รายงานจากจังหวัดพระนครศรีอยุธยา",
        "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA",
        "published_at": datetime.now(BANGKOK_TZ).isoformat(),
    }]
    data = local_social.for_province(videos, "พระนครศรีอยุธยา")
    assert data["items"][0]["match_field"] == "description"
    assert data["matching"] == "province_text"
    assert "district" not in data["items"][0]


def test_ambiguous_province_name_needs_location_cue():
    video = {"title": "น้ำท่วมหนักเลย", "description": "รายงานทั่วไป", "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA",
             "published_at": datetime.now(BANGKOK_TZ).isoformat()}
    assert local_social.for_province([video], "เลย")["items"] == []
    video["title"] = "น้ำท่วม จ.เลย"
    assert len(local_social.for_province([video], "เลย")["items"]) == 1
