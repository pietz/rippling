"""iCalendar (RFC 5545) file of leave requests and holidays, built locally."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .core import INACTIVE_LEAVE, leave_view

PRODID = "-//rippling CLI//leave and holidays//EN"
UID_DOMAIN = "rippling-cli"


def escape(text: str) -> str:
    """TEXT value escaping: backslash, semicolon, comma, newline."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Fold a content line at 75 octets, never inside a UTF-8 character."""
    parts, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        if size + n > 75:
            parts.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n".join(parts)


def _event(
    uid: str,
    first: date,
    last: date,
    summary: str,
    description: str | None,
    stamp: datetime,
) -> list[str]:
    """An all-day event from `first` to `last` inclusive (DTEND is exclusive)."""
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@{UID_DOMAIN}",
        f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
        f"DTSTART;VALUE=DATE:{first:%Y%m%d}",
        f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{escape(description)}")
    return [*lines, "END:VEVENT"]


def holiday_spans(holidays: dict[date, str]) -> list[tuple[date, date, str]]:
    """Consecutive days with the same holiday name merged into one span."""
    spans: list[tuple[date, date, str]] = []
    for d in sorted(holidays):
        name = holidays[d]
        if spans and spans[-1][2] == name and spans[-1][1] + timedelta(days=1) == d:
            spans[-1] = (spans[-1][0], d, name)
        else:
            spans.append((d, d, name))
    return spans


def calendar(
    requests: list[dict], holidays: dict[date, str], stamp: datetime
) -> tuple[str, int]:
    """The .ics text (CRLF line endings) and its number of events.

    Active leave requests (not cancelled or rejected) and holidays; anything not yet
    approved says its status in the title. `stamp` is the UTC creation time.
    """
    events: list[tuple[date, list[str]]] = []
    for r in requests:
        if r.get("status") in INACTIVE_LEAVE:
            continue
        v = leave_view(r)
        first, last = date.fromisoformat(v["start"]), date.fromisoformat(v["end"])
        summary = v["policy"] or "Leave"
        if v["status"] != "approved":
            summary += f" ({v['status'] or 'unknown status'})"
        description = "; ".join(x for x in (f"{v['days']:g} day(s)", v["reason"]) if x)
        uid = f"leave-{v['id']}"
        events.append((first, _event(uid, first, last, summary, description, stamp)))
    for first, last, name in holiday_spans(holidays):
        uid = f"holiday-{first:%Y%m%d}"
        events.append((first, _event(uid, first, last, name, "Holiday", stamp)))
    events.sort(key=lambda e: e[0])
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Rippling leave and holidays",
    ]
    for _, ev in events:
        lines += ev
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines), len(events)
