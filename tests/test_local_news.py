from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from backend.app.main import app
from backend.app.services import local_news
from backend.app.services.cache import cache
from backend.app.services.freshness import BANGKOK_TZ
from backend.app.services.provinces import provinces


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_prd_host_matches_official_directory():
    assert len({_row["name"] for _row in provinces()}) == 77
    assert local_news._host("สุโขทัย") == "sukhothai.prd.go.th"
    assert local_news._host("พระนครศรีอยุธยา") == "ayutthaya.prd.go.th"
    assert local_news._host("กรุงเทพมหานคร") == "www.prd.go.th"


def test_news_filters_topic_place_age_and_unsafe_links(monkeypatch):
    now = datetime.now(BANGKOK_TZ)
    recent = now.isoformat()
    old = (now - timedelta(days=8)).isoformat()
    calls = []

    async def landing(url, settings, *, headers=None):
        calls.append(url)
        return type("Response", (), {"text": '<a href="/th/rss/page/contentjson/id/5/cid/9">JSON</a>'})()

    async def feed(url, settings):
        calls.append(url)
        return {"items": [
            {"title": "สุโขทัย ฝนตกหนักและน้ำท่วม", "url": "https://sukhothai.prd.go.th/th/content/1", "date_published": recent},
            {"title": "เชียงใหม่ ฝนตกหนัก", "content_html": "เชียงใหม่", "url": "https://sukhothai.prd.go.th/th/content/2", "date_published": recent},
            {"title": "สุโขทัย จัดงานกีฬา", "url": "https://sukhothai.prd.go.th/th/content/3", "date_published": recent},
            {"title": "สุโขทัย น้ำท่วม", "url": "https://evil.example/th/content/4", "date_published": recent},
            {"title": "สุโขทัย น้ำท่วมเก่า", "url": "https://sukhothai.prd.go.th/th/content/5", "date_published": old},
        ]}

    monkeypatch.setattr(local_news, "get_response", landing)
    monkeypatch.setattr(local_news, "get_json", feed)
    response = client.get("/api/v1/local-news", params={"province": "สุโขทัย"})
    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body["data"]["items"]] == ["สุโขทัย ฝนตกหนักและน้ำท่วม"]
    assert body["data"]["matching"] == "province_text"
    assert len(calls) == 2
    assert client.get("/api/v1/local-news", params={"province": "สุโขทัย"}).json()["meta"]["status"] == "cached"
    assert len(calls) == 2


def test_news_unknown_province_does_not_fetch(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected upstream call")

    monkeypatch.setattr(local_news, "get_response", forbidden)
    assert client.get("/api/v1/local-news", params={"province": "แอตแลนติส"}).status_code == 422
