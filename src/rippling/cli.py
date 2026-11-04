"""`rippling` command line: login, balance, hours, vacation."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Annotated

import typer

from . import auth
from . import core as rp
from .api import BASE, ApiError, Client, TransportError
from .login import LoginError, wait_for_login

app = typer.Typer(
    help="Log hours and vacation in Rippling and check your balance, via Rippling's web API.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

YES = Annotated[
    bool,
    typer.Option(
        "--yes", "-y", help="Send without asking. Writes are dry-run otherwise."
    ),
]


def _err(msg: str) -> None:
    typer.secho(f"Error: {msg}", fg="red", err=True)
    raise typer.Exit(1)


def _client() -> Client:
    try:
        return Client(auth.load())
    except auth.AuthError as e:
        _err(str(e))


def _post_write(c: Client, path: str, body: dict) -> object:
    """Send a write once. Never retried: a retry could duplicate it."""
    try:
        return c.post(path, body)
    except ApiError as e:
        _err(f"Rippling rejected it: HTTP {e.status}\n{e.body[:2000]}")
    except TransportError as e:
        _err(
            f"No clear answer from Rippling ({e}). It may or may not have been saved. "
            "Check before retrying."
        )


def _send_or_dry_run(method: str, path: str, body: dict, yes: bool) -> bool:
    """Print exactly what will be sent; return True if the user (or --yes) approves."""
    typer.echo(f"\n{method} {BASE}{path}")
    typer.echo(json.dumps(body, indent=2, ensure_ascii=False))
    typer.echo("")
    if yes:
        return True
    if not sys.stdin.isatty():
        typer.echo("Dry run: nothing sent. Re-run with --yes to send.")
        return False
    if typer.confirm("Send this to Rippling?", default=False):
        return True
    typer.echo("Not sent.")
    return False


@app.command()
def login(
    timeout: Annotated[
        int, typer.Option(help="Seconds to wait for you to finish logging in.")
    ] = 600,
) -> None:
    """Log in through a browser window and store the token locally."""

    def verify(creds: auth.Credentials) -> bool:
        try:
            Client(creds).get(
                "/time_tracking/api/time_entry_policies/get_default_timezone_country_for_roles",
                roles=creds.role_id,
            )
            return True
        except (auth.AuthError, ApiError, TransportError):
            return False

    typer.echo("Opening Rippling in a browser window...")
    try:
        creds = wait_for_login(
            verify,
            timeout=timeout,
            on_wait=lambda: typer.echo(
                "Log in there (password, Cloudflare check, 2FA). Waiting..."
            ),
        )
    except LoginError as e:
        _err(str(e))
    path = auth.save(creds)
    expires = datetime.fromtimestamp(creds.expires_at).strftime("%Y-%m-%d")
    typer.echo(f"Logged in. Token saved to {path} (expires {expires}).")


@app.command()
def balance(
    as_json: Annotated[
        bool, typer.Option("--json", help="Machine-readable output.")
    ] = False,
) -> None:
    """Show leave balances (policies with no balance or usage are hidden)."""
    c = _client()
    fields = ("availableBalanceDays", "daysUsed", "daysPending", "daysScheduled")
    rows = [
        {
            "policy": p["policyDisplayName"].strip(),
            **{k: float(p.get(k) or 0) for k in fields},
        }
        for p in rp.pto_summary(c)
    ]
    rows = [r for r in rows if any(r[k] for k in fields)]
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    width = max([len(r["policy"]) for r in rows] + [6])
    typer.echo(
        f"{'Policy':<{width}}  {'Available':>9}  {'Used':>6}  {'Pending':>7}  {'Scheduled':>9}"
    )
    for r in rows:
        typer.echo(
            f"{r['policy']:<{width}}  {r['availableBalanceDays']:>9g}  {r['daysUsed']:>6g}"
            f"  {r['daysPending']:>7g}  {r['daysScheduled']:>9g}"
        )


@app.command()
def hours(
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
        existing = rp.entries_on(c, d, tz)
        if len(existing) > 1:
            listed = "; ".join(rp.describe_entry(e, tz) for e in existing)
            raise ValueError(
                f"{d} already has {len(existing)} entries ({listed}). Edit them in the web app."
            )
        old = existing[0] if existing else None
        if old and (why := rp.update_blocker(old)):
            raise ValueError(
                f"Won't replace the entry on {d} ({rp.describe_entry(old, tz)}): {why}. "
                "Edit it in the web app."
            )
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
        f"  {'new:  ' if old else ''}{t0:%H:%M}-{t1:%H:%M}{label}, {worked // 60}:{worked % 60:02d} h worked"
    )

    if not _send_or_dry_run("POST", rp.TIME_ENTRY_PATH, body, yes):
        return
    res = _post_write(c, rp.TIME_ENTRY_PATH, body)
    event = (res if isinstance(res, dict) else {}).get("event") or {}
    typer.echo(f"Sent. Rippling returned entry {event.get('id', '?')}.")
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


@app.command()
def vacation(
    start: Annotated[str, typer.Argument(help="First day off, YYYY-MM-DD.")],
    end: Annotated[str, typer.Argument(help="Last day off (inclusive), YYYY-MM-DD.")],
    reason: Annotated[
        str | None, typer.Option(help="Optional note for your approver.")
    ] = None,
    yes: YES = False,
) -> None:
    """Request vacation (Annual Leave Policy). Sending notifies your approver."""
    c = _client()
    try:
        d0, d1 = rp.parse_date(start), rp.parse_date(end)
        policy = rp.annual_policy(rp.pto_summary(c))
        body = rp.build_leave_request(
            c.creds.role_id, c.creds.company_id, policy["leavePolicyId"], d0, d1, reason
        )
    except ValueError as e:
        _err(str(e))

    hol = rp.holidays(c)
    days = rp.working_days(d0, d1, rp.schedule_hours(c, d0), set(hol))
    skipped = [f"{d:%a %d.%m.} {hol[d]}" for d in sorted(hol) if d0 <= d <= d1]
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
            f"  Overlaps existing request {r['startDate']} to {r['endDate']} ({r['status']}).",
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
        f"Sent. Request {res.get('id', '?')}: {res.get('startDate', d0)} to {res.get('endDate', d1)}, "
        f"{res.get('numDays', '?')} day(s), status {res.get('status', '?')}."
    )


def main() -> None:
    try:
        app()
    except auth.AuthError as e:
        typer.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
    except (ApiError, TransportError) as e:
        typer.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
