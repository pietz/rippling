from rippling import people

ROWS = [
    {
        "RoleWithCompany:id": {"id": "r1", "fullName": "Ada Lovelace"},
        "Job:title": "Engineer",
        "RoleWithCompany:department": ["Engineering"],
        "RoleWithCompany:manager": {"id": "r2", "fullName": "Charles Babbage"},
        "RoleWithCompany:workEmail": "ada@example.com",
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
    assert len(people.search(everyone, "")) == 2
    assert people.find(everyone, "r2")["title"] == "CTO"
