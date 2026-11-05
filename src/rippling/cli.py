"""`rippling` command line.

rippling login | logout | whoami | holidays
rippling hours  log | week | month
rippling leave  balance | request | list | cancel
rippling people search
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from typing import Annotated

import typer

from . import __version__, auth
from . import core as rp
from . import people as ppl
from .api import BASE, ApiError, Client, TransportError
from .login import LoginError, wait_for_login

app = typer.Typer(
    no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False
)
hours_app = typer.Typer(no_args_is_help=True, help="Log and review working hours.")
leave_app = typer.Typer(
    no_args_is_help=True, help="Vacation: balance, requests, cancelling."
)
people_app = typer.Typer(no_args_is_help=True, help="Look up colleagues.")
app.add_typer(hours_app, name="hours")
app.add_typer(leave_app, name="leave")
app.add_typer(people_app, name="people")

YES = Annotated[
    bool,
    typer.Option(
        "--yes", "-y", help="Send without asking. Writes are dry-run otherwise."
    ),
]
JSON = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"rippling {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Unofficial CLI for Rippling: log hours, request vacation, check balances, find colleagues.

    Writes (hours log, leave request, leave cancel) are dry-run by default: they print exactly what they would send, then ask in a terminal. Without a terminal they only send with --yes.
    """


# ---------------------------------------------------------------- helpers


def _err(msg: str) -> None:
    typer.secho(f"Error: {msg}", fg="red", err=True)
    raise typer.Exit(1)


def _client() -> Client:
    try:
        return Client(auth.load())
    except auth.AuthError as e:
        _err(str(e))


def _print_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _hours(h: float) -> str:
    return f"{h:g} h"


def _post_write(c: Client, path: str, body: dict) -> object:
    """Send a write once. Never retried: a retry could duplicate it."""
    try:
        return c.post(path, body)
    except ApiError as e:
        if e.status >= 500:  # a server error can come after the write was saved
            _err(
                f"Rippling answered with a server error (HTTP {e.status}). It may or "
                "may not have been saved. Check before retrying."
            )
        _err(f"Rippling rejected it: HTTP {e.status}\n{e.body[:2000]}")
    except TransportError as e:
        _err(
            f"No clear answer from Rippling ({e}). It may or may not have been saved. "
            "Check before retrying."
        )


def _interactive() -> bool:
    return sys.stdin.isatty()


def _send_or_dry_run(method: str, path: str, body: dict, yes: bool) -> bool:
    """Print exactly what will be sent; return True if the user (or --yes) approves."""
    typer.echo(f"\n{method} {BASE}{path}")
    typer.echo(json.dumps(body, indent=2, ensure_ascii=False))
    typer.echo("")
    if yes:
        return True
    if not _interactive():
        typer.echo(
            "Dry run: nothing sent. To send it, re-run with --yes "
            "(agents: only after the user explicitly approves)."
        )
        return False
    if typer.confirm("Send this to Rippling?", default=False):
        return True
    typer.echo("Not sent.")
    return False


# ---------------------------------------------------------------- top level


@app.command()
def login(
    timeout: Annotated[
        int, typer.Option(help="Seconds to wait for you to finish logging in.")
    ] = 600,
) -> None:
    """Log in through a browser window and store the token locally (lasts ~30 days)."""

    def verify(creds: auth.Credentials) -> bool:
        try:
            rp.timezone(Client(creds))
            return True
        except (auth.AuthError, ApiError, TransportError, KeyError, TypeError):
            return False

    typer.echo("Opening Rippling in a browser window...")
    try:
        creds = wait_for_login(
            verify,
            timeout=timeout,
            on_wait=lambda: typer.echo(
                "Log in there as usual (password, browser check, 2FA). Waiting..."
            ),
        )
    except LoginError as e:
        _err(str(e))
    path = auth.save(creds)
    expires = datetime.fromtimestamp(creds.expires_at).strftime("%Y-%m-%d")
    typer.echo(f"Logged in. Token saved to {path} (expires {expires}).")


@app.command()
def logout() -> None:
    """Delete the stored token and the login browser profile from this computer.

    The token itself stays valid on Rippling's side until it expires.
    """
    removed = auth.clear()
    if not removed:
        typer.echo("Nothing to remove: not logged in on this computer.")
        return
    for path in removed:
        typer.echo(f"Removed {path}")


@app.command()
def whoami(as_json: JSON = False) -> None:
    """Show who you're logged in as: name, title, team, manager, email."""
    c = _client()
    me = ppl.find(ppl.directory(c), c.creds.role_id)
    if not me:
        _err("Couldn't find your own entry in the org chart.")
    me = me | {"token_expires": datetime.fromtimestamp(c.creds.expires_at).isoformat()}
    if as_json:
        _print_json(me)
        return
    _print_person(me)
    typer.echo(f"  {'login':<11} valid until {me['token_expires'][:10]}")


@app.command()
def holidays(
    year: Annotated[
        int | None, typer.Argument(help="Year (YYYY). Default: this year.")
    ] = None,
    as_json: JSON = False,
) -> None:
    """List your company's public holidays."""
    c = _client()
    year = year or rp.today_in(rp.timezone(c)).year
    days = sorted((d, name) for d, name in rp.holidays(c).items() if d.year == year)
    if as_json:
        _print_json([{"date": d.isoformat(), "name": n} for d, n in days])
        return
    if not days:
        typer.echo(f"No holidays found for {year}.")
    for d, name in days:
        typer.echo(f"{d:%a %Y-%m-%d}  {name}")


# ---------------------------------------------------------------- hours


def _replaceable_entry(c: Client, d: date, tz: str) -> dict | None:
    """The day's single entry if it can be replaced safely, None if the day is empty.

    Raises ValueError explaining why `hours log` must not write this day.
    """
    existing = rp.entries_on(c, d, tz)
    if len(existing) > 1:
        listed = "; ".join(rp.describe_entry(e, tz) for e in existing)
        raise ValueError(
            f"{d} already has {len(existing)} entries ({listed}). Edit them in the web app."
        )
    old = existing[0] if existing else None
    if old and (why := rp.update_blocker(old, tz) or rp.unmanaged_data(c, old)):
        raise ValueError(
            f"Won't replace the entry on {d} ({rp.describe_entry(old, tz)}): {why}. "
            "Edit it in the web app."
        )
    return old


@hours_app.command("log")
def hours_log(
    day: Annotated[str, typer.Argument(help="YYYY-MM-DD, 'today' or 'yesterday'.")],
    start: Annotated[str, typer.Argument(help="Start time, HH:MM local.")],
    end: Annotated[str, typer.Argument(help="End time, HH:MM local.")],
    breaks: Annotated[
        list[str] | None,
        typer.Option("--break", "-b", help="Break as HH:MM-HH:MM. Repeatable."),
    ] = None,
    yes: YES = False,
) -> None:
    """Log working hours (and breaks) for one day. Replaces that day's entry if one exists."""
    c = _client()
    try:
        tz = rp.timezone(c)
        d = rp.parse_date(day, rp.today_in(tz))
        t0, t1 = rp.parse_time(start), rp.parse_time(end)
        spans = [rp.parse_span(b) for b in breaks or []]
        old = _replaceable_entry(c, d, tz)
        break_type = rp.manual_break_type(c) if spans else None
        body = rp.build_time_entry(
            c.creds.role_id, d, t0, t1, spans, tz, break_type, old
        )
    except ValueError as e:
        _err(str(e))

    action = "Replace" if old else "Create"
    worked = rp.worked_minutes(body, bool(break_type and break_type.paid))
    typer.echo(f"{action} time entry for {d:%a %Y-%m-%d} ({tz})")
    if old:
        typer.echo(f"  now:  {rp.describe_entry(old, tz)}")
    brk = ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in spans)
    label = f", break {brk} ({break_type.name})" if spans else ""
    typer.echo(
        f"  {'new:  ' if old else ''}{t0:%H:%M}-{t1:%H:%M}{label}, "
        f"{worked // 60}:{worked % 60:02d} h worked"
    )

    if not _send_or_dry_run("POST", rp.TIME_ENTRY_PATH, body, yes):
        return
    # The day may have changed since the preview (web app, another run): check again.
    try:
        now = _replaceable_entry(c, d, tz)
    except ValueError as e:
        _err(f"{e} Nothing sent.")
    if now != old:
        _err(
            f"The entries on {d} changed since the preview. Nothing sent; run it again."
        )
    res = _post_write(c, rp.TIME_ENTRY_PATH, body)
    event = (res if isinstance(res, dict) else {}).get("event") or {}
    entry_id = str(event.get("id", "?")).removeprefix("TimeEntry:")
    typer.echo(f"Sent. Rippling returned entry {entry_id}.")
    try:
        after = rp.entries_on(c, d, tz)
    except (ApiError, TransportError) as e:
        typer.secho(f"  Saved, but reading it back failed ({e}).", fg="yellow")
        return
    for e in after:
        typer.echo(f"  {d}: {rp.describe_entry(e, tz)}")
    if not after:
        typer.secho(
            "  Read-back found no entry for that day. Check the web app.", fg="yellow"
        )


def _show_timesheet(
    title: str, days: list[rp.Day], tz: str, as_json: bool, skip_empty: bool
) -> None:
    today = rp.today_in(tz)
    worked = round(sum(d.worked for d in days), 2)
    scheduled = round(sum(d.scheduled for d in days), 2)
    if as_json:
        _print_json(
            {
                "timezone": tz,
                "start": days[0].date,
                "end": days[-1].date,
                "worked_hours": worked,
                "scheduled_hours": scheduled,
                "days": [
                    {
                        "date": d.date,
                        "weekday": f"{d.date:%a}",
                        "scheduled_hours": d.scheduled,
                        "worked_hours": d.worked,
                        "holiday": d.holiday,
                        "leave": d.leave,
                        "missing": d.missing(today),
                        "entries": d.entries,
                    }
                    for d in days
                ],
            }
        )
        return
    typer.echo(f"{title} ({tz})")
    for d in days:
        label = f"{d.date:%a %Y-%m-%d}"
        notes = [n for n in (d.holiday, d.leave) if n]
        if not d.entries and not notes and not d.missing(today):
            if not skip_empty or (d.scheduled and d.date <= today):
                typer.echo(f"  {label}  -")
            continue
        for i, v in enumerate(d.entries):
            status = f"  {v['status']}" if v["status"] else ""
            hours = _hours(v["worked_hours"]) if v["worked_hours"] is not None else "?"
            typer.echo(
                f"  {label if i == 0 else ' ' * len(label)}  {rp.span_text(v):<30} {hours:>6}{status}"
            )
        if d.missing(today):
            typer.secho(
                f"  {label}  missing ({_hours(d.scheduled)} scheduled)", fg="yellow"
            )
        for n in notes:
            typer.echo(f"  {label if not d.entries else ' ' * len(label)}  {n}")
    typer.echo(f"Total: {_hours(worked)} worked of {_hours(scheduled)} scheduled")


@hours_app.command("week")
def hours_week(
    day: Annotated[
        str,
        typer.Argument(help="Any day in the week: YYYY-MM-DD, 'today' or 'yesterday'."),
    ] = "today",
    as_json: JSON = False,
) -> None:
    """Show a week (Mon-Sun): entries, breaks, hours worked vs. scheduled, approval."""
    c = _client()
    tz = rp.timezone(c)
    try:
        d = rp.parse_date(day, rp.today_in(tz))
    except ValueError as e:
        _err(str(e))
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    days = rp.timesheet(c, monday, sunday, tz)
    _show_timesheet(f"Week {monday} to {sunday}", days, tz, as_json, False)


@hours_app.command("month")
def hours_month(
    month: Annotated[
        str | None, typer.Argument(help="YYYY-MM. Default: this month.")
    ] = None,
    as_json: JSON = False,
) -> None:
    """Show a month: entries per day, missing days, hours worked vs. scheduled."""
    c = _client()
    tz = rp.timezone(c)
    try:
        first = (
            date.fromisoformat(f"{month}-01")
            if month
            else rp.today_in(tz).replace(day=1)
        )
    except ValueError:
        _err(f"Invalid month {month!r}; use YYYY-MM.")
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
        days=1
    )
    days = rp.timesheet(c, first, last, tz)
    _show_timesheet(f"{first:%B %Y}", days, tz, as_json, True)


# ---------------------------------------------------------------- leave


@leave_app.command("balance")
def leave_balance(
    on: Annotated[
        str | None,
        typer.Option(
            help="Also show Rippling's projected balance on this date (YYYY-MM-DD)."
        ),
    ] = None,
    as_json: JSON = False,
) -> None:
    """Show leave balances in days (policies with no balance or usage are hidden)."""
    c = _client()
    try:
        on_date = rp.parse_date(on, rp.today_in(rp.timezone(c))) if on else None
    except ValueError as e:
        _err(str(e))
    fields = {
        "available_days": "availableBalanceDays",
        "used_days": "daysUsed",
        "pending_days": "daysPending",
        "scheduled_days": "daysScheduled",
    }
    rows = [
        {
            "policy": p["policyDisplayName"].strip(),
            "policy_id": p["leavePolicyId"],
            **{k: float(p.get(src) or 0) for k, src in fields.items()},
        }
        for p in rp.pto_summary(c)
    ]
    if on_date:
        balances = rp.current_balances(c)
        for r in rows:
            b = balances.get(r["policy_id"])
            r["forecast_days"] = (
                rp.forecast_days(c, r["policy_id"], b, on_date) if b else None
            )
    rows = [r for r in rows if any(r[k] for k in fields) or r.get("forecast_days")]
    if as_json:
        _print_json([r | ({"forecast_date": on_date} if on_date else {}) for r in rows])
        return
    width = max([len(r["policy"]) for r in rows] + [6])
    extra = f"  {'On ' + str(on_date):>13}" if on_date else ""
    typer.echo(
        f"{'Policy':<{width}}  {'Available':>9}  {'Used':>6}  {'Pending':>7}  {'Scheduled':>9}{extra}"
    )
    for r in rows:
        line = (
            f"{r['policy']:<{width}}  {r['available_days']:>9g}  {r['used_days']:>6g}"
            f"  {r['pending_days']:>7g}  {r['scheduled_days']:>9g}"
        )
        if on_date:
            projected = "-" if r["forecast_days"] is None else f"{r['forecast_days']:g}"
            line += f"  {projected:>13}"
        typer.echo(line)


@leave_app.command("request")
def leave_request(
    start: Annotated[
        str, typer.Argument(help="First day off: YYYY-MM-DD, 'today' or 'yesterday'.")
    ],
    end: Annotated[
        str,
        typer.Argument(
            help="Last day off, inclusive: YYYY-MM-DD, 'today' or 'yesterday'."
        ),
    ],
    reason: Annotated[
        str | None, typer.Option(help="Optional note for your approver.")
    ] = None,
    policy_name: Annotated[
        str,
        typer.Option(
            "--policy", help="Leave policy name, as `leave balance` shows it."
        ),
    ] = rp.ANNUAL_POLICY,
    yes: YES = False,
) -> None:
    """Request time off (vacation by default). Sending notifies your approver."""
    c = _client()
    try:
        today = rp.today_in(rp.timezone(c))
        d0, d1 = rp.parse_date(start, today), rp.parse_date(end, today)
        policy = rp.leave_policy(rp.pto_summary(c), policy_name)
        body = rp.build_leave_request(
            c.creds.role_id, c.creds.company_id, policy["leavePolicyId"], d0, d1, reason
        )
    except ValueError as e:
        _err(str(e))

    hol = rp.holidays(c)
    days = rp.working_days(d0, d1, rp.scheduled_hours(c, d0, d1), set(hol))
    skipped = [f"{d:%a %Y-%m-%d} {hol[d]}" for d in sorted(hol) if d0 <= d <= d1]
    available = float(policy.get("availableBalanceDays") or 0)
    typer.echo(
        f"Vacation request {d0:%a %Y-%m-%d} to {d1:%a %Y-%m-%d} ({policy['policyDisplayName']})"
    )
    typer.echo(f"  about {len(days)} working day(s); available now: {available:g}")
    if skipped:
        typer.echo(f"  holidays in range: {', '.join(skipped)}")
    if not days:
        typer.secho("  This range has no working days.", fg="yellow")
    for r in rp.overlapping_leave(c, d0, d1):
        typer.secho(
            f"  Overlaps existing request {r['startDate']} to {r['endDate']} ({r['status'].lower()}).",
            fg="yellow",
        )
    errors = rp.validate_leave(c, body)
    if errors:
        _err("Rippling's validation rejected this request:\n  " + "\n  ".join(errors))
    typer.echo("  Rippling's validation: ok")

    if not _send_or_dry_run("POST", rp.LEAVE_REQUEST_PATH, body, yes):
        return
    res = _post_write(c, rp.LEAVE_REQUEST_PATH, body)
    res = res if isinstance(res, dict) else {}
    typer.echo(
        f"Sent. Request {res.get('id', '?')}: {res.get('startDate', d0)} to "
        f"{res.get('endDate', d1)}, {res.get('numDays', '?')} day(s), "
        f"status {res.get('status', '?')}."
    )


@leave_app.command("list")
def leave_list(
    show_all: Annotated[
        bool, typer.Option("--all", help="Include requests from previous years.")
    ] = False,
    as_json: JSON = False,
) -> None:
    """List your leave requests, newest first (this year and later by default)."""
    c = _client()
    since = date(rp.today_in(rp.timezone(c)).year, 1, 1)
    rows = [rp.leave_view(r) for r in rp.leave_requests(c)]
    if not show_all:
        rows = [r for r in rows if date.fromisoformat(r["end"]) >= since]
    rows.sort(key=lambda r: r["start"], reverse=True)
    if as_json:
        _print_json(rows)
        return
    if not rows:
        typer.echo(
            "No leave requests" + ("." if show_all else " this year. Try --all.")
        )
        return
    width = max(len(r["policy"]) for r in rows)
    typer.echo(
        f"{'Start':<10}  {'End':<10}  {'Days':>4}  {'Policy':<{width}}  {'Status':<9}  Id"
    )
    for r in rows:
        typer.echo(
            f"{r['start']:<10}  {r['end']:<10}  {r['days']:>4g}  {r['policy']:<{width}}"
            f"  {r['status']:<9}  {r['id']}"
        )


@leave_app.command("cancel")
def leave_cancel(
    request_id: Annotated[
        str,
        typer.Argument(
            help="Request id from `rippling leave list` (or its first 6+ chars)."
        ),
    ],
    yes: YES = False,
) -> None:
    """Cancel one of your leave requests. Your approver may be notified."""
    c = _client()
    try:
        r = rp.find_leave(rp.leave_requests(c), request_id)
    except ValueError as e:
        _err(str(e))
    v = rp.leave_view(r)
    if r.get("status") in rp.INACTIVE_LEAVE:
        _err(f"Request {v['id']} is already {v['status']}.")
    typer.echo(
        f"Cancel {v['policy']} {v['start']} to {v['end']} ({v['days']:g} day(s), {v['status']})"
    )
    path = rp.cancel_path(v["id"])
    if not _send_or_dry_run("POST", path, {}, yes):
        return
    res = _post_write(c, path, {})
    typer.echo(f"Sent. Rippling answered: {json.dumps(res, ensure_ascii=False)[:500]}")
    try:
        after = rp.find_leave(rp.leave_requests(c), v["id"])
        typer.echo(f"Status now: {after.get('status')}. Check the web app to be sure.")
    except (ApiError, TransportError, ValueError) as e:
        typer.secho(f"Sent, but reading it back failed ({e}).", fg="yellow")


# ---------------------------------------------------------------- people


def _print_person(p: dict) -> None:
    typer.echo(p["name"] or "?")
    rows = [
        ("title", p["title"]),
        ("department", p["department"]),
        ("manager", p["manager"]),
        ("email", p["email"]),
        ("location", ", ".join(x for x in (p.get("location"), p.get("country")) if x)),
        ("since", p.get("start_date")),
    ]
    for k, v in rows:
        if v:
            typer.echo(f"  {k:<11} {v}")


@people_app.command("search")
def people_search(
    query: Annotated[
        str,
        typer.Argument(help="Words to match in name, title, department or email."),
    ],
    as_json: JSON = False,
) -> None:
    """Find colleagues: title, department, manager and work email."""
    if not query.split():
        _err("Give at least one word to search for.")
    c = _client()
    found = ppl.search(ppl.directory(c), query)
    if as_json:
        _print_json(found)
        return
    if not found:
        typer.echo("Nobody matches.")
        return
    for i, p in enumerate(found):
        if i:
            typer.echo("")
        _print_person(p)


def main() -> None:
    # Names and JSON can be non-ASCII; Windows pipes default to a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding not in ("utf-8", "utf8") and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        app()
    except auth.AuthError as e:
        typer.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
    except (ApiError, TransportError) as e:
        typer.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
