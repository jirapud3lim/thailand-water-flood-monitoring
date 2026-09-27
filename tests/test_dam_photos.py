import json

import pytest

from backend.app.config import Settings
from backend.app.services import dam_photos


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_curated_list_covers_known_dams() -> None:
    files = dam_photos.photo_files()
    assert len(files) >= 20 and all(name.startswith("เขื่อน") for name in files)
    assert files["เขื่อนลำนางรอง"] != "Phanomrung.jpg"  # Wikipedia's auto image was a temple


@pytest.mark.anyio
async def test_fetch_maps_back_normalized_titles_and_drops_non_free(monkeypatch) -> None:
    monkeypatch.setattr(dam_photos, "photo_files", lambda: {
        "เขื่อนภูมิพล": "Bhumibol_dam_front.jpg", "เขื่อนทดสอบ": "Fair_use.jpg", "เขื่อนหาย": "Deleted.jpg",
    })
    seen = {}

    async def fake(url, settings, *, params=None, headers=None):
        seen.update(params)
        meta = lambda nonfree: {  # noqa: E731
            "LicenseShortName": {"value": "CC BY-SA 4.0"}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
            "Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:X">Photographer &amp; Co</a>'},
            **({"NonFree": {"value": "true"}} if nonfree else {}),
        }
        return {"query": {
            "normalized": [{"from": "File:Bhumibol_dam_front.jpg", "to": "File:Bhumibol dam front.jpg"},
                           {"from": "File:Fair_use.jpg", "to": "File:Fair use.jpg"}],
            "pages": {
                "1": {"title": "File:Bhumibol dam front.jpg", "imageinfo": [{"thumburl": "https://t/1.jpg", "descriptionurl": "https://c/1", "extmetadata": meta(False)}]},
                "2": {"title": "File:Fair use.jpg", "imageinfo": [{"thumburl": "https://t/2.jpg", "descriptionurl": "https://c/2", "extmetadata": meta(True)}]},
                "-1": {"title": "File:Deleted.jpg", "missing": ""},
            },
        }}

    monkeypatch.setattr(dam_photos, "get_json", fake)
    data = await dam_photos.fetch_dam_photos(Settings(_env_file=None))
    assert list(data["photos"]) == ["เขื่อนภูมิพล"]
    photo = data["photos"]["เขื่อนภูมิพล"]
    assert photo["artist"] == "Photographer & Co" and photo["license"] == "CC BY-SA 4.0"
    assert photo["thumb_url"] == "https://t/1.jpg" and photo["page_url"] == "https://c/1"
    assert seen["titles"].count("File:") == 3 and seen["iiurlwidth"] == dam_photos.THUMB_WIDTH
