from rippling import people

ROWS = [
    {
        "RoleWithCompany:id": {"id": "r1", "fullName": "Ada Lovelace"},
        "Job:title": "Engineer",
        "RoleWithCompany:department": ["Engineering"],
        "RoleWithCompany:manager": {"id": "r2", "fullName": "Charles Babbage"},
        "RoleWithCompany:workEmail": "ada@example.com",
        "RoleWithCompany:startDate": "2020-01-01",
        "Job:location.nickname": "Berlin",
    },
    {
        "RoleWithCompany:id": {"id": "r2", "fullName": "Charles Babbage"},
        "Job:title": "CTO",
        "RoleWithCompany:department": ["Engineering", "Leadership"],
        "RoleWithCompany:workEmail": "charles@example.com",
    },
]


def test_person_view_flattens_org_chart_row():
    p = people.person_view(ROWS[1])
    assert (
        p["name"] == "Charles Babbage" and p["department"] == "Engineering, Leadership"
    )
    assert p["manager"] is None


def test_search_matches_all_words_across_fields():
    everyone = [people.person_view(r) for r in ROWS]
    assert [p["id"] for p in people.search(everyone, "engineer ada")] == ["r1"]
    assert len(people.search(everyone, "engineering")) == 2
    assert people.search(everyone, "nobody") == []
    assert people.search(everyone, "") == []  # no directory dumps
    assert people.search(everyone, "   ") == []
    assert people.find(everyone, "r2")["title"] == "CTO"


def test_search_returns_only_colleague_fields():
    everyone = [people.person_view(r) for r in ROWS]
    (ada,) = people.search(everyone, "ada")
    assert set(ada) == set(people.COLLEAGUE_FIELDS)
    assert "start_date" not in ada and "location" not in ada
