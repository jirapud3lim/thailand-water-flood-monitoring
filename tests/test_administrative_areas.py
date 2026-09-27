from backend.app.services import administrative_areas


def test_registry_has_three_complete_levels() -> None:
    provinces = administrative_areas.child_areas()
    assert provinces is not None and len(provinces) == 77
    districts = [row for province in provinces for row in administrative_areas.child_areas(province["code"])]
    subdistricts = [row for district in districts for row in administrative_areas.child_areas(district["code"])]
    assert len(districts) == 928
    assert len(subdistricts) == 7658
    assert len({row["code"] for row in subdistricts}) == len(subdistricts)


def test_area_resolves_parent_names_and_rejects_invalid_code() -> None:
    assert administrative_areas.area("73") == {
        "code": "73", "level": "province", "province": "นครปฐม", "district": None, "subdistrict": None,
    }
    assert administrative_areas.area("7306")["district"] == "สามพราน"
    assert administrative_areas.area("730608") == {
        "code": "730608", "level": "subdistrict", "province": "นครปฐม",
        "district": "สามพราน", "subdistrict": "ไร่ขิง",
    }
    assert administrative_areas.area("73060") is None
    assert administrative_areas.area("not-a-code") is None


def test_children_require_real_parent() -> None:
    assert administrative_areas.child_areas("730608") is None
    assert administrative_areas.child_areas("9999") is None
