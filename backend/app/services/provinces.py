"""Canonical province table shared by every source.

`data/provinces.json` holds the 77 provinces with the standard Thai province code (the same
number GISTDA uses as `pv_idn`; all 77 names were checked against GISTDA on 2026-09-27) and
a bbox for zooming. The bboxes were derived from the ThaiWater/DPM/RID station positions in
each province, padded by 0.05°, so they approximate the populated/gauged area rather than
the legal boundary.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "provinces.json"
# Spellings seen in upstream payloads that differ from the canonical name.
ALIASES = {"กรุงเทพฯ": "กรุงเทพมหานคร", "กทม.": "กรุงเทพมหานคร", "กรุงเทพ": "กรุงเทพมหานคร"}


@lru_cache
def provinces() -> list[dict[str, Any]]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


@lru_cache
def _by_name() -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in provinces()}


def normalize_province(name: Any) -> str | None:
    """Canonical Thai name, or None for blanks and places outside Thailand."""
    if not isinstance(name, str):
        return None
    text = name.strip().removeprefix("จังหวัด").removeprefix("จ.").strip()
    text = ALIASES.get(text, text)
    return text if text in _by_name() else None


def province(name: str) -> dict[str, Any] | None:
    canonical = normalize_province(name)
    return _by_name()[canonical] if canonical else None
