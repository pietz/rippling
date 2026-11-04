"""Rippling domain logic: resolve ids, build write payloads, read state.

Pure helpers (parsing, time conversion, payload building) are kept separate
from the API calls so they can be tested without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from .api import Client

ANNUAL_POLICY = "Annual Leave Policy"
INACTIVE_LEAVE = {"CANCELED", "CANCELLED", "REJECTED", "DECLINED", "DELETED"}

TIME_ENTRY_PATH = "/time_products/twirp/time_products.shared.v1.TimesheetService/CreateOrUpdateTimeEntry"
LEAVE_REQUEST_PATH = "/pto/api/leave_requests/"


# ---------------------------------------------------------------- parsing


def parse_date(value: str, today: date | None = None) -> date:
    today = today or date.today()
    v = value.strip().lower()
    if v == "today":
        return today
    if v == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(
            f"Invalid date {value!r}; use YYYY-MM-DD, 'today' or 'yesterday'."
        )


def parse_time(value: str) -> time:
    v = value.strip()
    try:
        if ":" in v:
            h, m = v.split(":")
            return time(int(h), int(m))
        return time(int(v), 0)
    except ValueError:
        raise ValueError(f"Invalid time {value!r}; use HH:MM (24h).")


def parse_span(value: str) -> tuple[time, time]:
    try:
        a, b = value.split("-")
    except ValueError:
        raise ValueError(f"Invalid break {value!r}; use HH:MM-HH:MM.")
    return parse_time(a), parse_time(b)


# ---------------------------------------------------------------- time entries


def utc_iso(day: date, t: time, tz: str) -> str:
    """Local wall time in `tz` -> UTC ISO string as JS Date.toISOString() writes it."""
    local = datetime.combine(day, t, tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def local_hhmm(iso: str, tz: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%H:%M")


def local_date(iso: str, tz: str) -> date:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).date()


def minutes(a: time, b: time) -> int:
    return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)


@dataclass
class BreakType:
    id: str
    name: str
    paid: bool


def build_time_entry(
    role_id: str,
    day: date,
    start: time,
    end: time,
    breaks: list[tuple[time, time]],
    tz: str,
    break_type: BreakType | None,
    object_id: str | None = None,
) -> dict:
    if minutes(start, end) <= 0:
        raise ValueError(
            "End time must be after start time (overnight entries are not supported)."
        )
    for bs, be in breaks:
        if minutes(bs, be) <= 0:
            raise ValueError(f"Break {bs:%H:%M}-{be:%H:%M} must end after it starts.")
        if minutes(start, bs) < 0 or minutes(be, end) < 0:
            raise ValueError(
                f"Break {bs:%H:%M}-{be:%H:%M} lies outside {start:%H:%M}-{end:%H:%M}."
            )
    ordered = sorted(breaks)
    for (_, prev_end), (next_start, _) in pairwise(ordered):
        if minutes(prev_end, next_start) < 0:
            raise ValueError("Breaks overlap.")
    if breaks and break_type is None:
        raise ValueError("No break type available for manual breaks.")

    start_iso, end_iso = utc_iso(day, start, tz), utc_iso(day, end, tz)
    body: dict = {}
    if object_id:
        body["object_id"] = object_id
    body |= {
        "role_id": role_id,
        "start_time": start_iso,
        "end_time": end_iso,
        # Existing entries store one job shift spanning the entry; mirror that.
        "job_shifts": [{"start_time": start_iso, "end_time": end_iso}],
        "breaks": [
            {
                "start_time": utc_iso(day, bs, tz),
                "end_time": utc_iso(day, be, tz),
                "company_break_type_id": break_type.id,
                "duration_minutes": minutes(bs, be),
            }
            for bs, be in ordered
        ],
        "shift_input_values": [],
        "comments": [],
        "attachments": [],
        "shift_input_group_values": [],
        "missing_break_justifications": [],
        "override_minimum_shift_engagement": False,
    }
    return body


def worked_minutes(
    start: time,
    end: time,
    breaks: list[tuple[time, time]],
    break_type: BreakType | None,
) -> int:
    unpaid = (
        0 if (break_type and break_type.paid) else sum(minutes(a, b) for a, b in breaks)
    )
    return minutes(start, end) - unpaid


def timezone(c: Client) -> str:
    data = c.get(
        "/time_tracking/api/time_entry_policies/get_default_timezone_country_for_roles",
        roles=c.creds.role_id,
    )
    return data[c.creds.role_id]["timezone"]


def pay_period_start(c: Client, day: date) -> date | None:
    data = c.get(
        "/time_tracking/api/time_entries/get_pay_periods_for_role", role=c.creds.role_id
    )
    for start, end in data["periods"]:
        if date.fromisoformat(start) <= day <= date.fromisoformat(end):
            return date.fromisoformat(start)
    return None


def summary_id(c: Client, period_start: date) -> str | None:
    data = c.get(
        "/time_tracking/api/time_entry_period_summaries_cached/getAllIdsAndPageSize/",
        role=c.creds.role_id,
        payPeriod__startDate=period_start.isoformat(),
    )
    return data["ids"][0] if data["ids"] else None


def entries_on(c: Client, day: date, tz: str) -> list[dict]:
    """Existing time entries whose local start date is `day`."""
    period = pay_period_start(c, day)
    sid = summary_id(c, period) if period else None
    if not sid:
        return []
    events = c.twirp("ListTimeEntryEvents", {"summary_id": sid}).get("events", [])
    return [e for e in events if local_date(e["event_times"]["start_time"], tz) == day]


def manual_break_type(c: Client) -> BreakType:
    role = c.creds.role_id
    policy = c.get(
        "/time_tracking/api/time_entry_policies/get_active_policy_v2",
        roles=role,
        includeTerminated="true",
    )[role]["breakPolicy"]
    if not policy:
        raise ValueError(
            "Your time policy has no break policy, so breaks cannot be logged."
        )
    bp = c.get(f"/time_tracking/api/time_entry_break_policies/{policy}/")
    manual = {
        e["breakType"] for e in bp.get("eligibleBreakTypes", []) if e.get("allowManual")
    }
    types = [
        t
        for t in bp.get("companyBreakTypes", [])
        if t["id"] in manual and not t.get("isDeleted")
    ]
    if len(types) != 1:
        names = ", ".join(t["description"] for t in types) or "none"
        raise ValueError(f"Expected exactly one manual break type, found: {names}.")
    t = types[0]
    return BreakType(id=t["id"], name=t["description"], paid=bool(t.get("isPaid")))


def describe_entry(e: dict, tz: str) -> str:
    times = e["event_times"]
    details = e.get("time_entry_event_details") or {}
    span = (
        f"{local_hhmm(times['start_time'], tz)}-{local_hhmm(times['end_time'], tz)}"
        if times.get("end_time")
        else (f"{local_hhmm(times['start_time'], tz)}-open")
    )
    brks = [
        f"{local_hhmm(b['start_time'], tz)}-{local_hhmm(b['end_time'], tz)}"
        for b in details.get("breaks") or []
        if b.get("end_time")
    ]
    status = (
        (e.get("approval_status") or "")
        .removeprefix("TIME_ENTRY_APPROVAL_STATUS_")
        .lower()
    )
    out = span + (f", break {', '.join(brks)}" if brks else "")
    if e.get("worked_hours") is not None:
        out += f", {e['worked_hours']:g} h worked"
    return out + (f" [{status}]" if status else "")


# ---------------------------------------------------------------- leave


def pto_summary(c: Client) -> list[dict]:
    return c.get(
        "/pto/api/data/get_pto_summary_v2/",
        role=c.creds.role_id,
        includeLongTerm="true",
        includeUnfulfilledPaidOut="false",
    )["pto_summary"]


def annual_policy(summary: list[dict]) -> dict:
    for p in summary:
        if p["policyDisplayName"] == ANNUAL_POLICY:
            return p
    names = ", ".join(p["policyDisplayName"] for p in summary)
    raise ValueError(f"No {ANNUAL_POLICY!r} found. Policies: {names}.")


def build_leave_request(
    role_id: str,
    company_id: str,
    policy_id: str,
    start: date,
    end: date,
    reason: str | None = None,
) -> dict:
    if end < start:
        raise ValueError("End date must not be before start date.")
    body = {
        "role": role_id,
        "company": company_id,
        "leavePolicy": policy_id,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "isOpenEnded": False,
        "leaveOccurrence": None,
        "proofOfLeaves": [],
        "updatedProofOfLeaves": None,
    }
    if reason:
        body["reasonForLeave"] = reason
    return body


def working_days(
    start: date, end: date, schedule_hours: list[float], holidays: set[date]
) -> list[date]:
    """Days in [start, end] with scheduled hours (schedule is Monday-first) that aren't holidays."""
    days, d = [], start
    while d <= end:
        if schedule_hours[d.weekday()] > 0 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


def schedule_hours(c: Client, anchor: date) -> list[float]:
    data = c.post(
        "/pto/api/get_working_schedule/",
        {
            "roleId": c.creds.role_id,
            "allow_time_admin": False,
            "anchorDate": anchor.strftime("%m/%d/%Y"),
        },
    )
    return [float(h) for h in data]


def holidays(c: Client) -> dict[date, str]:
    data = c.post(
        "/pto/api/get_holiday_calendar/",
        {"role": c.creds.role_id, "allow_time_admin": False, "only_payable": False},
    )
    out: dict[date, str] = {}
    for year in data:
        for h in year["holidays"]:
            if h.get("type") != "FULL_DAY":
                continue
            d, last = (
                date.fromisoformat(h["startDate"]),
                date.fromisoformat(h["endDate"]),
            )
            while d <= last:
                out[d] = h["name"]
                d += timedelta(days=1)
    return out


def validate_leave(c: Client, body: dict) -> list[str]:
    """Rippling's own read-only form validation. Returns error messages (empty = ok)."""
    res = c.post("/pto/api/leave_requests/validate_request/", {"leaveRequest": body})
    if res is True:
        return []
    errors = res.get("error", []) if isinstance(res, dict) else []
    return [
        e.get("message") or e.get("error_code", "?")
        for e in errors
        if e.get("error_code") != "SUCCESS"
    ]


def overlapping_leave(c: Client, start: date, end: date) -> list[dict]:
    reqs = c.get("/pto/api/leave_requests/", role=c.creds.role_id)
    return [
        r
        for r in reqs
        if r.get("status") not in INACTIVE_LEAVE
        and date.fromisoformat(r["startDate"]) <= end
        and date.fromisoformat(r["endDate"]) >= start
    ]
