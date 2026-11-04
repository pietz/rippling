"""People directory, read from the org chart (one call returns everyone visible)."""

from __future__ import annotations

from .api import Client

ORG_CHART_PATH = (
    "/hub/api/org_chart_app/data"
    "?page_size=1000&pagination_threshold=1000&page=1&force_skip_pagination=true"
)
ORG_CHART_BODY = {
    "visualizationType": "GRID",
    "template": "hris_eom_org_chart_non_admin_view",
    "embeddedContext": {"appName": "ORG_CHART_EXTERNAL_APP"},
}


# What `people search` shows about colleagues; `whoami` shows all of `person_view`.
COLLEAGUE_FIELDS = (
    "id",
    "name",
    "title",
    "department",
    "manager",
    "manager_id",
    "email",
)


def person_view(row: dict) -> dict:
    who = row.get("RoleWithCompany:id") or {}
    manager = row.get("RoleWithCompany:manager") or {}
    return {
        "id": who.get("id"),
        "name": who.get("fullName"),
        "title": row.get("Job:title"),
        "department": ", ".join(row.get("RoleWithCompany:department") or []) or None,
        "manager": manager.get("fullName"),
        "manager_id": manager.get("id"),
        "email": row.get("RoleWithCompany:workEmail"),
        "location": row.get("Job:location.nickname"),
        "country": row.get("Job:location.address.country"),
        "remote": row.get("Job:location.isRemote"),
        "start_date": row.get("RoleWithCompany:startDate"),
        "employment_type": row.get("Job:companyEmploymentType.label"),
    }


def directory(c: Client) -> list[dict]:
    data = c.post(ORG_CHART_PATH, ORG_CHART_BODY)
    people = [person_view(r) for r in data.get("data", [])]
    return sorted(
        (p for p in people if p["id"]), key=lambda p: (p["name"] or "").lower()
    )


def search(people: list[dict], query: str) -> list[dict]:
    """Colleagues matching every word of `query` in name, title, department or email.

    Returns only COLLEAGUE_FIELDS. An empty query matches nobody: no directory dumps.
    """
    words = query.lower().split()
    if not words:
        return []
    fields = ("name", "title", "department", "email")

    def text(p: dict) -> str:
        return " ".join(str(p[f] or "") for f in fields).lower()

    return [
        {f: p[f] for f in COLLEAGUE_FIELDS}
        for p in people
        if all(w in text(p) for w in words)
    ]


def find(people: list[dict], role_id: str) -> dict | None:
    return next((p for p in people if p["id"] == role_id), None)
