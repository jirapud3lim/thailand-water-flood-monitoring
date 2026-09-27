"""Local province/district/sub-district registry used to validate TMD place requests."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "administrative_areas.json"
VALID_CODE_LENGTHS = {2: "province", 4: "district", 6: "subdistrict"}


@lru_cache
def registry() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


@lru_cache
def _indexes() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    areas: dict[str, dict[str, Any]] = {}
    children: dict[str, list[dict[str, str]]] = {"": []}
    for province in registry()["provinces"]:
        province_area = {
            "code": province["code"], "level": "province", "province": province["name"],
            "district": None, "subdistrict": None,
        }
        areas[province["code"]] = province_area
        children[""].append({"code": province["code"], "name": province["name"], "level": "province"})
        children[province["code"]] = []
        for district in province["districts"]:
            district_area = {
                "code": district["code"], "level": "district", "province": province["name"],
                "district": district["name"], "subdistrict": None,
            }
            areas[district["code"]] = district_area
            children[province["code"]].append({
                "code": district["code"], "name": district["name"], "level": "district",
            })
            children[district["code"]] = []
            for subdistrict in district["subdistricts"]:
                subdistrict_area = {
                    "code": subdistrict["code"], "level": "subdistrict", "province": province["name"],
                    "district": district["name"], "subdistrict": subdistrict["name"],
                }
                areas[subdistrict["code"]] = subdistrict_area
                children[district["code"]].append({
                    "code": subdistrict["code"], "name": subdistrict["name"], "level": "subdistrict",
                })
    return areas, children


def area(code: str) -> dict[str, Any] | None:
    text = str(code).strip()
    if not text.isdigit() or len(text) not in VALID_CODE_LENGTHS:
        return None
    return _indexes()[0].get(text)


def child_areas(parent_code: str | None = None) -> list[dict[str, str]] | None:
    text = (parent_code or "").strip()
    if text and (not text.isdigit() or len(text) not in (2, 4)):
        return None
    return _indexes()[1].get(text)


def source() -> dict[str, str]:
    return registry()["source"]
