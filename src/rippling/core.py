"""Rippling domain logic: resolve ids, build write payloads, read state.

Pure helpers (parsing, time conversion, payload building) are kept separate
from the API calls so they can be tested without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from .api import TWIRP, Client

ANNUAL_POLICY = "Annual Leave Policy"
MAX_LEAVE_DAYS = 366
INACTIVE_LEAVE = {"CANCELED", "CANCELLED", "REJECTED", "DECLINED", "DELETED"}

TIME_ENTRY_PATH = TWIRP + "CreateOrUpdateTimeEntry"
DELETE_ENTRY_PATH = TWIRP + "DeleteTimeEntry"
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


def instant(day: date, t: time, tz: str) -> datetime:
    """Local wall time in `tz` -> aware UTC datetime.

    Rejects wall times that a DST change skips or repeats, instead of guessing.
    """
    zone = ZoneInfo(tz)
    first = datetime.combine(day, t, tzinfo=zone)
    if first.utcoffset() != first.replace(fold=1).utcoffset():
        roundtrip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        kind = (
            "does not exist"
            if roundtrip != first.replace(tzinfo=None)
            else "is ambiguous"
        )
        raise ValueError(f"{day} {t:%H:%M} {kind} in {tz} (daylight saving change).")
    return first.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    """UTC ISO string as JS Date.toISOString() writes it."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def utc_iso(day: date, t: time, tz: str) -> str:
    return to_iso(instant(day, t, tz))


def local_hhmm(iso: str, tz: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%H:%M")


def local_date(iso: str, tz: str) -> date:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).date()


def today_in(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def _minutes(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


@dataclass
class BreakType:
    id: str
    name: str
    paid: bool


def local_end_after(iso: str, day: date, tz: str) -> bool:
    """Whether the instant `iso` lies after the end of local `day` (local midnight)."""
    local = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    return local > datetime.combine(day + timedelta(days=1), time())


def update_blocker(entry: dict, tz: str) -> str | None:
    """Why an existing entry can't be safely replaced by a plain times+breaks entry."""
    details = entry.get("time_entry_event_details") or {}
    shifts = details.get("job_shifts") or []
    if len(shifts) > 1:
        return "it has several job shifts"
    if any(s.get("job_codes") for s in shifts):
        return "it has job codes"
    if entry.get("can_mutate") is not True:
        return "Rippling doesn't mark it as editable"
    times = entry.get("event_times") or {}
    start, end = times.get("start_time"), times.get("end_time")
    if not start or not end:
        return "it has no end time"
    if local_end_after(end, local_date(start, tz), tz):
        return "it runs past midnight"
    return None


def delete_blocker(entry: dict, tz: str) -> str | None:
    """Why an existing entry shouldn't be deleted from here: the replace rules, plus
    approved entries (removing one changes hours that are already signed off)."""
    if entry.get("approval_status") == "TIME_ENTRY_APPROVAL_STATUS_APPROVED":
        return "it's already approved"
    return update_blocker(entry, tz)


def build_delete(entry: dict, comment: str | None = None) -> dict:
    """DeleteTimeEntry body for an entry from the event list."""
    return {"id": entry["id"], "comment": comment or ""}


# Entry data `hours log` can't carry over. The event list doesn't include it, so the
# entry's detail record is checked before replacing.
UNMANAGED_FIELDS = {
    "attachments": "attachments",
    "comments": "comments",
    "photos": "photos",
    "shiftInputValues": "custom shift inputs",
    "shiftInputGroupValues": "custom shift inputs",
    "missingBreakJustifications": "missing-break justifications",
}


def unmanaged_data(c: Client, entry: dict) -> str | None:
    """Why replacing `entry` would drop data, or None. Fails closed on odd shapes."""
    eid = str(entry["id"]).removeprefix("TimeEntry:")
    record = c.get(f"/time_tracking/api/time_entries/{eid}/")
    if not isinstance(record, dict):
        return "its details couldn't be checked"
    for key, label in UNMANAGED_FIELDS.items():
        if key not in record:
            return "its details couldn't be checked"
        if record[key]:
            return f"it has {label}"
    return None


def build_time_entry(
    role_id: str,
    day: date,
    start: time,
    end: time,
    breaks: list[tuple[time, time]],
    tz: str,
    break_type: BreakType | None,
    existing: dict | None = None,
) -> dict:
    """CreateOrUpdateTimeEntry body. With `existing`, it replaces that entry."""
    t0, t1 = instant(day, start, tz), instant(day, end, tz)
    if t1 <= t0:
        raise ValueError(
            "End time must be after start time (overnight entries are not supported)."
        )
    spans = sorted((instant(day, a, tz), instant(day, b, tz), a, b) for a, b in breaks)
    for bs, be, a, b in spans:
        if be <= bs:
            raise ValueError(f"Break {a:%H:%M}-{b:%H:%M} must end after it starts.")
        if bs < t0 or be > t1:
            raise ValueError(
                f"Break {a:%H:%M}-{b:%H:%M} lies outside {start:%H:%M}-{end:%H:%M}."
            )
    for prev, nxt in pairwise(spans):
        if nxt[0] < prev[1]:
            raise ValueError("Breaks overlap.")
    if breaks and break_type is None:
        raise ValueError("No break type available for manual breaks.")

    body: dict = {}
    if existing:
        body["object_id"] = existing["id"]
    body |= {
        "role_id": role_id,
        "start_time": to_iso(t0),
        "end_time": to_iso(t1),
        # Existing entries store one job shift spanning the entry; mirror that.
        "job_shifts": [{"start_time": to_iso(t0), "end_time": to_iso(t1)}],
        "breaks": [
            {
                "start_time": to_iso(bs),
                "end_time": to_iso(be),
                "company_break_type_id": break_type.id,
                "duration_minutes": _minutes(bs, be),
            }
            for bs, be, _, _ in spans
        ],
        "shift_input_values": [],
        "comments": [],
        "attachments": [],
        "shift_input_group_values": [],
        "missing_break_justifications": [],
        "override_minimum_shift_engagement": False,
    }
    if existing:
        # Keep fields of the old entry this command doesn't manage.
        details = existing.get("time_entry_event_details") or {}
        for key in ("note", "schedule_id"):
            if details.get(key):
                body[key] = details[key]
    return body


def worked_minutes(body: dict, paid_breaks: bool) -> int:
    """Worked minutes of a time-entry body (elapsed time, so DST-safe)."""
    total = _minutes(
        datetime.fromisoformat(body["start_time"]),
        datetime.fromisoformat(body["end_time"]),
    )
    unpaid = 0 if paid_breaks else sum(b["duration_minutes"] for b in body["breaks"])
    return total - unpaid


def timezone(c: Client) -> str:
    data = c.get(
        "/time_tracking/api/time_entry_policies/get_default_timezone_country_for_roles",
        roles=c.creds.role_id,
    )
    return data[c.creds.role_id]["timezone"]


def pay_periods(c: Client) -> list[tuple[date, date]]:
    data = c.get(
        "/time_tracking/api/time_entries/get_pay_periods_for_role", role=c.creds.role_id
    )
    return [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in data["periods"]]


def covering_period(
    periods: list[tuple[date, date]], day: date
) -> tuple[date, date] | None:
    return next(((p0, p1) for p0, p1 in periods if p0 <= day <= p1), None)


def summary_id(c: Client, period_start: date) -> str | None:
    data = c.get(
        "/time_tracking/api/time_entry_period_summaries_cached/getAllIdsAndPageSize/",
        role=c.creds.role_id,
        payPeriod__startDate=period_start.isoformat(),
    )
    return data["ids"][0] if data["ids"] else None


def time_entries(
    c: Client,
    start: date,
    end: date,
    tz: str,
    periods: list[tuple[date, date]] | None = None,
) -> list[dict]:
    """Time entries whose local start date lies in [start, end], oldest first."""
    found = []
    for p0, p1 in periods if periods is not None else pay_periods(c):
        if p1 < start or p0 > end:
            continue
        sid = summary_id(c, p0)
        if not sid:
            continue
        events = c.twirp("ListTimeEntryEvents", {"summary_id": sid}).get("events", [])
        found += [
            e
            for e in events
            # The event list can also carry leave/holiday/shift events; only time entries count.
            if e.get("time_entry_event_details")
            and start <= local_date(e["event_times"]["start_time"], tz) <= end
        ]
    return sorted(found, key=lambda e: e["event_times"]["start_time"])


def entries_on(c: Client, day: date, tz: str) -> list[dict]:
    """Existing time entries whose local start date is `day`.

    Raises ValueError when Rippling can't show that day (no pay period covers it) or
    an entry from the day before runs into it: neither is safe to write next to.
    """
    periods = pay_periods(c)
    if not covering_period(periods, day):
        raise ValueError(
            f"Rippling has no pay period covering {day} yet, so existing entries "
            "can't be checked."
        )
    prev = day - timedelta(days=1)
    found = time_entries(c, prev, day, tz, periods)
    for e in found:
        t = e["event_times"]
        if local_date(t["start_time"], tz) == prev and local_end_after(
            t.get("end_time") or t["start_time"], prev, tz
        ):
            raise ValueError(
                f"An entry from {prev} runs into {day} ({describe_entry(e, tz)}). "
                "Edit it in the web app."
            )
    return [e for e in found if local_date(e["event_times"]["start_time"], tz) == day]


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


def entry_view(e: dict, tz: str) -> dict:
    """A time entry in local time, reduced to what people care about."""
    times = e["event_times"]
    details = e.get("time_entry_event_details") or {}
    breaks = [b for b in details.get("breaks") or [] if b.get("end_time")]
    worked = e.get("worked_hours")
    if worked is None and times.get("end_time"):
        start = datetime.fromisoformat(times["start_time"])
        end = datetime.fromisoformat(times["end_time"])
        unpaid = sum(
            b.get("duration_minutes") or 0 for b in breaks if not b.get("is_paid")
        )
        worked = round(((end - start).total_seconds() / 60 - unpaid) / 60, 2)
    status = (e.get("approval_status") or "").removeprefix(
        "TIME_ENTRY_APPROVAL_STATUS_"
    )
    return {
        "id": e["id"],
        "date": local_date(times["start_time"], tz).isoformat(),
        "start": local_hhmm(times["start_time"], tz),
        "end": local_hhmm(times["end_time"], tz) if times.get("end_time") else None,
        "breaks": [
            {
                "start": local_hhmm(b["start_time"], tz),
                "end": local_hhmm(b["end_time"], tz),
            }
            for b in breaks
        ],
        "worked_hours": worked,
        "status": status.lower() or None,
    }


def span_text(v: dict) -> str:
    """'09:00-18:00, break 12:00-13:00' for an entry_view dict."""
    out = f"{v['start']}-{v['end'] or 'open'}"
    if v["breaks"]:
        out += ", break " + ", ".join(f"{b['start']}-{b['end']}" for b in v["breaks"])
    return out


def describe_entry(e: dict, tz: str) -> str:
    v = entry_view(e, tz)
    out = span_text(v)
    if v["worked_hours"] is not None:
        out += f", {v['worked_hours']:g} h worked"
    return out + (f" [{v['status']}]" if v["status"] else "")


@dataclass
class Day:
    date: date
    entries: list[dict]  # entry_view dicts
    scheduled: float  # hours expected; 0 on weekends, holidays and leave days
    holiday: str | None = None
    leave: str | None = None

    @property
    def worked(self) -> float:
        return round(sum(e["worked_hours"] or 0 for e in self.entries), 2)

    def missing(self, today: date) -> bool:
        return self.scheduled > 0 and not self.entries and self.date < today


def leave_hours_on(r: dict, d: date, scheduled: float) -> float:
    """Hours of leave request `r` that fall on day `d` (partial days use custom hours)."""
    first, last = date.fromisoformat(r["startDate"]), date.fromisoformat(r["endDate"])
    if not first <= d <= last:
        return 0.0
    for edge, prefix in ((first, "startDate"), (last, "endDate")):
        if d == edge:
            custom = float(r.get(f"{prefix}CustomHours") or 0)
            if custom > 0:
                return min(custom, scheduled)
            if r.get(f"{prefix}HalfDay"):
                return scheduled / 2
    return scheduled


def timesheet(c: Client, start: date, end: date, tz: str) -> list[Day]:
    """One row per day in [start, end]: entries, expected hours, holiday, leave."""
    entries = [entry_view(e, tz) for e in time_entries(c, start, end, tz)]
    schedule = scheduled_hours(c, start, end)
    hol = holidays(c)
    leaves = overlapping_leave(c, start, end)
    days, d = [], start
    while d <= end:
        holiday = hol.get(d)
        scheduled = 0.0 if holiday else schedule.get(d, 0.0)
        leave, off = None, 0.0
        for r in leaves:
            if hours := leave_hours_on(r, d, scheduled):
                off += hours
                partial = f" {hours:g} h" if hours < scheduled else ""
                leave = (
                    f"{r['policyDisplayName'].strip()}{partial} ({r['status'].lower()})"
                )
        todays = [e for e in entries if e["date"] == d.isoformat()]
        days.append(Day(d, todays, max(0.0, scheduled - off), holiday, leave))
        d += timedelta(days=1)
    return days


# ---------------------------------------------------------------- pay period


def _num(v: object) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def display_text(v: object) -> str:
    """Readable text from an API value of loosely known shape (alerts, task titles)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for key in (
            "message",
            "title",
            "text",
            "description",
            "label",
            "name",
            "value",
        ):
            if text := display_text(v.get(key)):
                return text
        v = list(v.values())
    if isinstance(v, list):
        return "; ".join(t for t in map(display_text, v) if t)
    return str(v)


def period_summary_id(c: Client, day: date) -> tuple[tuple[date, date], str]:
    """The pay period containing `day` and its timesheet summary id."""
    period = covering_period(pay_periods(c), day)
    if not period:
        raise ValueError(f"Rippling has no pay period covering {day} yet.")
    sid = summary_id(c, period[0])
    if not sid:
        raise ValueError(
            f"Rippling has no timesheet for the pay period {period[0]} to {period[1]} yet."
        )
    return period, sid


# (key, timecardSummary field), in display order
PERIOD_HOURS = (
    ("total", "total_hours"),
    ("approved", "approved_hours"),
    ("unapproved", "unapproved_hours"),
    ("paid", "paid_hours"),
    ("overtime", "total_overtime_hours"),
    ("double_overtime", "total_double_overtime_hours"),
    ("paid_time_off", "total_paid_time_off_hours"),
    ("unpaid_time_off", "total_unpaid_time_off_hours"),
    ("holiday", "total_holiday_hours"),
)


def period_view(item: dict) -> dict:
    """A pay-period summary reduced to hours, earnings and alerts."""
    s = item.get("timecardSummary") or {}
    period = item.get("payPeriod") or {}
    alerts = (s.get("period_alerts") or []) + (s.get("entry_alerts") or [])
    return {
        "start": period.get("startDate"),
        "end": period.get("endDate"),
        "hours": {key: _num(s.get(src)) for key, src in PERIOD_HOURS},
        "earnings": [
            {"name": (e.get("name") or "?").strip(), "hours": _num(e.get("value"))}
            for e in s.get("earning_summaries") or []
            if _num(e.get("value"))
        ],
        "alerts": [t for t in map(display_text, alerts) if t],
    }


def period_summary(c: Client, day: date) -> dict:
    _, sid = period_summary_id(c, day)
    items = c.get(
        "/time_tracking/api/time_entry_period_summaries_cached/",
        ids=sid,
        page=1,
        pagination_v2="true",
        role=c.creds.role_id,
    )
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ValueError("Rippling sent an unexpected pay period summary.")
    return period_view(items[0])


# ---------------------------------------------------------------- change history


def pb_value(v: object) -> object:
    """A protobuf JSON Value ({"string_value": ...}, {"list_value": ...}) as plain data."""
    if not isinstance(v, dict):
        return v
    if "struct_value" in v:
        fields = (v["struct_value"] or {}).get("fields") or {}
        return {k: pb_value(x) for k, x in fields.items()}
    if "list_value" in v:
        return [pb_value(x) for x in (v["list_value"] or {}).get("values") or []]
    for key in ("string_value", "number_value", "bool_value"):
        if key in v:
            return v[key]
    return None


# Bookkeeping fields Rippling records with every edit; not shown.
QUIET_FIELDS = {
    "_activePolicy",
    "timezone",
    "attestation",
    "isManuallyPaid",
    "jobShifts",
    "shiftInputGroupValues",
    "isDeleted",
}
ACTIONS = {"ACTION_REQUEST_APPROVAL": "sent for approval"}


def _clock(iso: str, day: date | None, tz: str) -> str:
    """Local HH:MM, with the date when it isn't `day`."""
    d, t = local_date(iso, tz), local_hhmm(iso, tz)
    return t if d == day else f"{d} {t}"


def edit_summary(fields: dict, day: date | None, tz: str) -> list[str]:
    """What one batch of field edits set, in words (deletion is the caller's)."""
    parts = []
    for key, label in (("startTime", "start"), ("endTime", "end")):
        if key in fields:
            v = fields[key]
            parts.append(f"{label} {_clock(v, day, tz)}" if v else f"{label} cleared")
    if "breaks" in fields:
        spans = [
            f"{_clock(b['start_time'], day, tz)}-{_clock(b['end_time'], day, tz)}"
            for b in fields["breaks"] or []
            if isinstance(b, dict) and b.get("start_time") and b.get("end_time")
        ]
        parts.append("breaks " + ", ".join(spans) if spans else "no breaks")
    if "status" in fields:
        parts.append(f"status {str(fields['status']).lower()}")
    if "note" in fields:
        parts.append(f"note {fields['note']!r}" if fields["note"] else "note removed")
    handled = {"startTime", "endTime", "breaks", "status", "note"}
    other = [k for k in fields if k not in handled and k not in QUIET_FIELDS]
    if other:
        parts.append("also " + ", ".join(other))
    return parts


def change_view(e: dict, tz: str) -> dict:
    """One entry's edit history: who changed what, when (local time), oldest first."""
    times = e.get("event_times") or {}
    start, end = times.get("start_time"), times.get("end_time")
    day = local_date(start, tz) if start else None
    edits, created = [], False
    for b in sorted(
        e.get("batch_edits") or [], key=lambda b: b.get("created_at") or ""
    ):
        fields = {
            f.get("field"): pb_value(f.get("new_value"))
            for f in b.get("field_edits") or []
        }
        if fields:
            if fields.get("isDeleted") is True:
                action = "deleted"
            else:
                action = "changed" if created else "created"
            created = True
            details = edit_summary(fields, day, tz)
        elif b.get("action_type"):
            kind = b["action_type"]
            action, details = ACTIONS.get(kind, kind.lower().replace("_", " ")), []
        else:
            continue
        source = b.get("source") or ""
        by = b.get("author_name") or (b.get("author") or {}).get("full_name")
        at = b.get("created_at")
        local = datetime.fromisoformat(at).astimezone(ZoneInfo(tz)) if at else None
        edits.append(
            {
                "at": f"{local:%Y-%m-%d %H:%M}" if local else None,
                "action": action,
                "by": by or ("Rippling" if source == "SYSTEM" else None),
                "source": source.lower().replace("_", " ") or None,
                "details": details,
            }
        )
    return {
        "id": e.get("id"),
        "date": day.isoformat() if day else None,
        "start": local_hhmm(start, tz) if start else None,
        "end": local_hhmm(end, tz) if end else None,
        "edits": edits,
    }


def change_history(
    c: Client, day: date, tz: str
) -> tuple[tuple[date, date], list[dict]]:
    """The pay period containing `day` and the recorded edits of its entries."""
    period, sid = period_summary_id(c, day)
    res = c.twirp("ListSummaryChangeHistory", {"summary_id": sid}) or {}
    entries = sorted(
        res.get("time_entries") or [],
        key=lambda e: (e.get("event_times") or {}).get("start_time") or "",
    )
    return period, [change_view(e, tz) for e in entries]


# ---------------------------------------------------------------- leave


def pto_summary(c: Client) -> list[dict]:
    return c.get(
        "/pto/api/data/get_pto_summary_v2/",
        role=c.creds.role_id,
        includeLongTerm="true",
        includeUnfulfilledPaidOut="false",
    )["pto_summary"]


def leave_policy(summary: list[dict], name: str = ANNUAL_POLICY) -> dict:
    """The leave policy whose display name is `name` (case and spacing ignored)."""
    want = " ".join(name.split()).casefold()
    for p in summary:
        if " ".join(p["policyDisplayName"].split()).casefold() == want:
            return p
    names = ", ".join(repr(p["policyDisplayName"].strip()) for p in summary)
    raise ValueError(f"No leave policy named {name!r}. Yours: {names}. Use --policy.")


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
    if (end - start).days >= MAX_LEAVE_DAYS:
        raise ValueError(f"That's more than {MAX_LEAVE_DAYS} days; check the dates.")
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
    start: date, end: date, schedule: dict[date, float], holidays: set[date]
) -> list[date]:
    """Days in [start, end] with scheduled hours that aren't holidays."""
    days, d = [], start
    while d <= end:
        if schedule.get(d, 0) > 0 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


def scheduled_hours(c: Client, start: date, end: date) -> dict[date, float]:
    """Scheduled working hours per date in [start, end].

    Rippling answers with the 7 days starting at the anchor date, so this walks the
    range one week at a time (schedules can change from week to week).
    """
    out: dict[date, float] = {}
    anchor = start
    while anchor <= end:
        data = c.post(
            "/pto/api/get_working_schedule/",
            {
                "roleId": c.creds.role_id,
                "allow_time_admin": False,
                "anchorDate": anchor.strftime("%m/%d/%Y"),
            },
        )
        for i, h in enumerate(data[:7]):
            if (d := anchor + timedelta(days=i)) <= end:
                out[d] = float(h)
        anchor += timedelta(days=7)
    return out


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
    """Rippling's own read-only form validation. Returns error messages (empty = ok).

    Fails closed: any response shape other than the known success forms is an error.
    """
    res = c.post("/pto/api/leave_requests/validate_request/", {"leaveRequest": body})
    return validation_errors(res)


def validation_errors(res: object) -> list[str]:
    if res is True:
        return []
    items = res.get("error") if isinstance(res, dict) else None
    if (
        not isinstance(items, list)
        or not items
        or not all(isinstance(e, dict) for e in items)
    ):
        return [f"Unexpected validation response: {str(res)[:200]}"]
    return [
        e.get("message") or e.get("error_code") or "unknown error"
        for e in items
        if e.get("error_code") != "SUCCESS"
    ]


def leave_requests(c: Client) -> list[dict]:
    return c.get("/pto/api/leave_requests/", role=c.creds.role_id)


def overlapping_leave(c: Client, start: date, end: date) -> list[dict]:
    return [
        r
        for r in leave_requests(c)
        if r.get("status") not in INACTIVE_LEAVE
        and date.fromisoformat(r["startDate"]) <= end
        and date.fromisoformat(r["endDate"]) >= start
    ]


def leave_view(r: dict) -> dict:
    return {
        "id": r["id"],
        "start": r["startDate"],
        "end": r["endDate"],
        "days": float(r.get("numDays") or 0),
        "policy": (r.get("policyDisplayName") or "").strip(),
        "status": (r.get("status") or "").lower() or None,
        "reason": r.get("reasonForLeave") or None,
    }


def find_leave(requests: list[dict], ident: str) -> dict:
    """A request by full id or unique id prefix (at least 6 characters)."""
    ident = ident.strip()
    exact = [r for r in requests if r["id"] == ident]
    if exact:
        return exact[0]
    if len(ident) < 6:
        raise ValueError("Give the full request id or at least its first 6 characters.")
    matches = [r for r in requests if r["id"].startswith(ident)]
    if not matches:
        raise ValueError(
            f"No leave request with id {ident!r}. See `rippling leave list --all`."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Id prefix {ident!r} matches {len(matches)} requests; give more of it."
        )
    return matches[0]


def cancel_path(request_id: str) -> str:
    return f"{LEAVE_REQUEST_PATH}{request_id}/cancel/"


def current_balances(c: Client) -> dict[str, dict]:
    """Current balance rows keyed by leave policy id."""
    rows = c.get(
        "/pto/api/current_balances/", company=c.creds.company_id, role=c.creds.role_id
    )
    return {r["leavePolicy"]: r for r in rows}


def forecast_days(c: Client, policy_id: str, balance: dict, on: date) -> float | None:
    """Rippling's projected balance (days) for a policy on a date, or None if unavailable."""
    us = on.strftime("%m/%d/%Y")  # this endpoint wants US dates, unlike the rest of PTO
    res = c.post(
        "/pto/api/forecast_balance/",
        {
            "role": c.creds.role_id,
            "leavePolicy": policy_id,
            "date": us,
            "endDate": us,
            "currentBalanceInMins": balance.get("balanceInMins"),
            "currentBalanceInDays": balance.get("balanceInDays"),
        },
    )
    if not isinstance(res, dict) or not res.get("forecastAvailable"):
        return None
    try:
        return float(res["balanceDays"])
    except (KeyError, TypeError, ValueError):
        return None


def leave_transactions(c: Client) -> list[dict]:
    return c.post(
        "/pto/api/transactions/get_transactions_for_role/",
        {"role": c.creds.role_id, "includeUnfulfilledPaidOut": False},
    )


def transaction_view(t: dict) -> dict:
    kind = (t.get("type") or "").removeprefix("LEAVE_").lower().replace("_", " ")
    note = t.get("explanation") or t.get("accrualExplanation") or t.get("comment")
    return {
        "date": t.get("date"),
        "type": kind or None,
        "description": (t.get("subType") or "").strip() or None,
        "change_days": _num(t.get("changeDays")),
        "balance_days": _num(t.get("balanceDays")),
        "explanation": (note or "").strip() or None,
        "expires": t.get("expirationDate") or None,
    }


def leave_history(groups: list[dict]) -> list[dict]:
    """Balance transactions per leave policy, oldest first.

    Rippling lists them newest first, including scheduled future accruals; the order
    is reversed rather than re-sorted so same-day rows keep their running balance.
    """
    out = []
    for g in groups:
        txs = g.get("transactions") or []
        if not txs:
            continue
        policy = txs[0].get("leavePolicy") or {}
        out.append(
            {
                "policy": (policy.get("displayName") or "?").strip(),
                "policy_id": policy.get("id"),
                "transactions": [transaction_view(t) for t in reversed(txs)],
            }
        )
    return out
