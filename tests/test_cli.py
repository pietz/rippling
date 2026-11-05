"""The write commands must never send without --yes (or an interactive yes)."""

import json
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from rippling import cli
from rippling import core as rp
from rippling.api import ApiError, TransportError
from rippling.auth import Credentials


class FakeClient:
    def __init__(self):
        self.creds = Credentials(
            access_token="t", company_id="c", role_id="r", expires_at=9e9
        )
        self.sent = []

    def post(self, path, body):
        if path.endswith("validate_request/"):
            return {"error": [{"error_code": "SUCCESS"}]}
        self.sent.append((path, body))
        return (
            {"event": {"id": "new"}}
            if "Timesheet" in path
            else {"id": "lr", "status": "PENDING"}
        )


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(cli, "_client", lambda: client)
    monkeypatch.setattr(rp, "timezone", lambda c: "Europe/Berlin")
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [])
    monkeypatch.setattr(rp, "unmanaged_data", lambda c, e: None)
    monkeypatch.setattr(rp, "today_in", lambda tz: date(2026, 11, 4))
    monkeypatch.setattr(
        rp, "manual_break_type", lambda c: rp.BreakType("bt", "Lunch Break", False)
    )
    monkeypatch.setattr(
        rp,
        "pto_summary",
        lambda c: [
            {
                "policyDisplayName": "Annual Leave Policy",
                "leavePolicyId": "p",
                "availableBalanceDays": "10.00",
            }
        ],
    )
    monkeypatch.setattr(rp, "holidays", lambda c: {date(2026, 12, 24): "Christmas Eve"})
    monkeypatch.setattr(
        rp,
        "scheduled_hours",
        lambda c, a, b: {
            a + timedelta(days=i): (
                8.0 if (a + timedelta(days=i)).weekday() < 5 else 0.0
            )
            for i in range((b - a).days + 1)
        },
    )
    monkeypatch.setattr(rp, "overlapping_leave", lambda c, a, b: [])
    return client


HOURS = ["hours", "log", "2026-11-04", "09:00", "18:00", "--break", "12:00-13:00"]
VACATION = ["leave", "request", "2026-12-21", "2026-12-24"]


@pytest.mark.parametrize("args", [HOURS, VACATION])
def test_dry_run_by_default(fake, args):
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "Dry run: nothing sent" in result.output
    assert fake.sent == []


@pytest.mark.parametrize("args", [HOURS, VACATION])
def test_yes_sends_exactly_once(fake, args):
    result = CliRunner().invoke(cli.app, [*args, "--yes"])
    assert result.exit_code == 0, result.output
    assert len(fake.sent) == 1


def test_vacation_dry_run_counts_working_days(fake):
    result = CliRunner().invoke(cli.app, VACATION)
    assert "about 3 working day(s)" in result.output
    assert "Christmas Eve" in result.output


def test_unknown_write_outcome_says_so(fake, monkeypatch):
    def boom(path, body):
        raise TransportError("timeout")

    monkeypatch.setattr(fake, "post", boom)
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 1
    assert "may or may not have been saved" in result.output


def test_refuses_to_replace_entry_with_job_codes(fake, monkeypatch):
    entry = {
        "id": "e1",
        "event_times": {
            "start_time": "2026-11-04T08:00:00Z",
            "end_time": "2026-11-04T17:00:00Z",
        },
        "can_mutate": True,
        "time_entry_event_details": {"job_shifts": [{"job_codes": [{"id": "j"}]}]},
    }
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [entry])
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 1 and "job codes" in result.output
    assert fake.sent == []


def _entry(eid, **extra):
    return {
        "id": eid,
        "event_times": {
            "start_time": "2026-11-04T08:00:00Z",
            "end_time": "2026-11-04T17:00:00Z",
        },
        "can_mutate": True,
        "time_entry_event_details": {"job_shifts": [{}]},
        **extra,
    }


def test_replaces_the_single_existing_entry(fake, monkeypatch):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [_entry("e1")])
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Replace time entry" in result.output
    assert fake.sent[0][1]["object_id"] == "e1"


@pytest.mark.parametrize(
    "entries, message",
    [
        ([_entry("e1"), _entry("e2")], "already has 2 entries"),
        ([_entry("e1", can_mutate=False)], "editable"),
    ],
)
def test_refuses_ambiguous_or_locked_days(fake, monkeypatch, entries, message):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: entries)
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 1 and message in result.output
    assert fake.sent == []


def test_version():
    result = CliRunner().invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and result.output.startswith("rippling ")


LEAVE = {
    "id": "1a2b3caaaaaaaaaaaaaaaaaa",
    "startDate": "2026-12-21",
    "endDate": "2026-12-23",
    "numDays": "3.00",
    "policyDisplayName": "Annual Leave Policy",
    "status": "PENDING",
}


@pytest.mark.parametrize("extra, sends", [([], 0), (["--yes"], 1)])
def test_leave_cancel_is_gated(fake, monkeypatch, extra, sends):
    monkeypatch.setattr(rp, "leave_requests", lambda c: [LEAVE])
    result = CliRunner().invoke(cli.app, ["leave", "cancel", "1a2b3c", *extra])
    assert result.exit_code == 0, result.output
    assert len(fake.sent) == sends
    if sends:
        assert fake.sent[0] == (
            "/pto/api/leave_requests/1a2b3caaaaaaaaaaaaaaaaaa/cancel/",
            {},
        )


def test_leave_cancel_refuses_inactive(fake, monkeypatch):
    monkeypatch.setattr(
        rp, "leave_requests", lambda c: [LEAVE | {"status": "CANCELED"}]
    )
    result = CliRunner().invoke(cli.app, ["leave", "cancel", "1a2b3c", "--yes"])
    assert result.exit_code == 1 and "already canceled" in result.output
    assert fake.sent == []


def test_hours_week_json(fake, monkeypatch):
    entry = {
        "id": "e1",
        "date": "2026-11-02",
        "start": "09:00",
        "end": "18:00",
        "breaks": [{"start": "12:00", "end": "13:00"}],
        "worked_hours": 8.0,
        "status": "approved",
    }
    monkeypatch.setattr(rp, "today_in", lambda tz: date(2026, 11, 4))
    monkeypatch.setattr(rp, "entry_view", lambda e, tz: e)
    monkeypatch.setattr(rp, "time_entries", lambda c, a, b, tz: [entry])
    monkeypatch.setattr(rp, "leave_requests", lambda c: [])
    result = CliRunner().invoke(cli.app, ["hours", "week", "2026-11-04", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["start"] == "2026-11-02" and data["end"] == "2026-11-08"
    assert data["worked_hours"] == 8.0 and data["scheduled_hours"] == 40.0
    missing = [d["date"] for d in data["days"] if d["missing"]]
    assert missing == ["2026-11-03"]  # past workday without entry; 04.11. is today


def test_logout_removes_local_login():
    from rippling import auth

    auth.save(
        Credentials(access_token="t", company_id="c", role_id="r", expires_at=9e9)
    )
    out = CliRunner().invoke(cli.app, ["logout"])
    assert out.exit_code == 0 and "Removed" in out.output
    out = CliRunner().invoke(cli.app, ["logout"])
    assert "Nothing to remove" in out.output


def test_people_search_needs_a_query(fake):
    assert CliRunner().invoke(cli.app, ["people", "search"]).exit_code != 0
    out = CliRunner().invoke(cli.app, ["people", "search", "  "])
    assert out.exit_code != 0 and "at least one word" in out.output


@pytest.mark.parametrize("args", [HOURS, VACATION])
@pytest.mark.parametrize("answer, sends", [("n\n", 0), ("y\n", 1), ("", 0)])
def test_interactive_confirm(fake, monkeypatch, args, answer, sends):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    result = CliRunner().invoke(cli.app, args, input=answer)
    assert len(fake.sent) == sends, result.output


@pytest.mark.parametrize(
    "status, message", [(500, "may or may not have been saved"), (400, "rejected")]
)
def test_server_errors_are_an_unknown_outcome(fake, monkeypatch, status, message):
    from rippling.api import ApiError

    def fail(path, body):
        raise ApiError(status, "{}", path)

    monkeypatch.setattr(fake, "post", fail)
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 1 and message in result.output


def test_hours_log_rechecks_the_day_before_sending(fake, monkeypatch):
    days = iter([[], [_entry("web")]])  # an entry appears after the preview
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: next(days))
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 1 and "changed since the preview" in result.output
    assert fake.sent == []


def test_hours_log_refuses_entries_with_unmanaged_data(fake, monkeypatch):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [_entry("e1")])
    monkeypatch.setattr(rp, "unmanaged_data", lambda c, e: "it has attachments")
    result = CliRunner().invoke(cli.app, [*HOURS, "--yes"])
    assert result.exit_code == 1 and "attachments" in result.output
    assert fake.sent == []


def test_leave_today_is_the_rippling_timezone_date(fake, monkeypatch):
    monkeypatch.setattr(rp, "today_in", lambda tz: date(2026, 12, 21))
    result = CliRunner().invoke(
        cli.app, ["leave", "request", "today", "today", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert fake.sent[0][1]["startDate"] == "2026-12-21"


# ---------------------------------------------------------------- hours delete

DELETE = ["hours", "delete", "2026-11-04"]
DELETE_PATH = (
    "/time_products/twirp/time_products.shared.v1.TimesheetService/DeleteTimeEntry"
)


def _days(monkeypatch, *states):
    """entries_on answers: preview, recheck, read-back."""
    answers = iter(states)
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: next(answers))


def test_hours_delete_dry_run_by_default(fake, monkeypatch):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [_entry("e1")])
    result = CliRunner().invoke(cli.app, [*DELETE, "--comment", "wrong day"])
    assert result.exit_code == 0, result.output
    assert "Delete time entry for Wed 2026-11-04" in result.output
    assert "09:00-18:00" in result.output and DELETE_PATH in result.output
    assert '"comment": "wrong day"' in result.output
    assert "Dry run: nothing sent" in result.output
    assert fake.sent == []


def test_hours_delete_yes_sends_exactly_once(fake, monkeypatch):
    _days(monkeypatch, [_entry("e1")], [_entry("e1")], [])
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 0, result.output
    assert fake.sent == [(DELETE_PATH, {"id": "e1", "comment": ""})]
    assert "now has no entries" in result.output


def test_hours_delete_warns_when_read_back_still_finds_it(fake, monkeypatch):
    _days(monkeypatch, [_entry("e1")], [_entry("e1")], [_entry("e1")])
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 0 and "Still there" in result.output
    assert len(fake.sent) == 1


@pytest.mark.parametrize("answer, sends", [("n\n", 0), ("y\n", 1), ("", 0)])
def test_hours_delete_interactive_confirm(fake, monkeypatch, answer, sends):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [_entry("e1")])
    result = CliRunner().invoke(cli.app, DELETE, input=answer)
    assert len(fake.sent) == sends, result.output


@pytest.mark.parametrize(
    "entries, message",
    [
        ([], "has no time entry"),
        ([_entry("e1"), _entry("e2")], "already has 2 entries"),
        ([_entry("e1", can_mutate=False)], "editable"),
        (
            [
                _entry(
                    "e1",
                    event_times={
                        "start_time": "2026-11-04T21:00:00Z",
                        "end_time": "2026-11-05T05:00:00Z",
                    },
                )
            ],
            "past midnight",
        ),
        (
            [_entry("e1", time_entry_event_details={"job_shifts": [{}, {}]})],
            "several job shifts",
        ),
    ],
)
def test_hours_delete_refuses(fake, monkeypatch, entries, message):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: entries)
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 1 and message in result.output
    assert fake.sent == []


def test_hours_delete_refuses_unmanaged_data(fake, monkeypatch):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [_entry("e1")])
    monkeypatch.setattr(rp, "unmanaged_data", lambda c, e: "it has attachments")
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 1 and "attachments" in result.output
    assert "Won't delete the entry on 2026-11-04" in result.output
    assert fake.sent == []


def test_hours_delete_refuses_days_without_pay_period(fake, monkeypatch):
    def no_period(c, d, tz):
        raise ValueError(f"Rippling has no pay period covering {d} yet.")

    monkeypatch.setattr(rp, "entries_on", no_period)
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 1 and "no pay period" in result.output
    assert fake.sent == []


@pytest.mark.parametrize(
    "recheck",
    [
        [],  # deleted elsewhere
        [_entry("e2")],  # replaced elsewhere
        [
            _entry(
                "e1",
                event_times={
                    "start_time": "2026-11-04T07:00:00Z",
                    "end_time": "2026-11-04T17:00:00Z",
                },
            )
        ],
    ],
)
def test_hours_delete_rechecks_before_sending(fake, monkeypatch, recheck):
    _days(monkeypatch, [_entry("e1")], recheck)
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 1
    assert "changed since the preview. Nothing sent" in result.output
    assert fake.sent == []


def test_hours_delete_recheck_refusal_sends_nothing(fake, monkeypatch):
    locked = _entry("e1", can_mutate=False)
    _days(monkeypatch, [_entry("e1")], [locked])
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 1 and "Nothing sent" in result.output
    assert fake.sent == []


@pytest.mark.parametrize(
    "error, message",
    [
        (lambda path: ApiError(502, "{}", path), "may or may not have been saved"),
        (lambda path: TransportError("timeout"), "may or may not have been saved"),
        (lambda path: ApiError(400, "bad id", path), "rejected"),
    ],
)
def test_hours_delete_unknown_outcome(fake, monkeypatch, error, message):
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [_entry("e1")])

    def fail(path, body):
        raise error(path)

    monkeypatch.setattr(fake, "post", fail)
    result = CliRunner().invoke(cli.app, [*DELETE, "--yes"])
    assert result.exit_code == 1 and message in result.output


# ---------------------------------------------------------------- reads


def test_hours_period_omits_zero_lines(fake, monkeypatch):
    view = {
        "start": "2026-11-02",
        "end": "2026-11-08",
        "hours": {"total": 38.5, "approved": 30.0, "unapproved": 8.5, "paid": 0.0},
        "earnings": [{"name": "Regular", "hours": 38.5}],
        "alerts": ["Missing break on Tue"],
    }
    monkeypatch.setattr(rp, "period_summary", lambda c, d: view)
    result = CliRunner().invoke(cli.app, ["hours", "period", "2026-11-04"])
    assert result.exit_code == 0, result.output
    assert "Pay period 2026-11-02 to 2026-11-08" in result.output
    assert "unapproved" in result.output and "paid" not in result.output
    assert "Regular" in result.output and "Missing break on Tue" in result.output
    result = CliRunner().invoke(cli.app, ["hours", "period", "--json"])
    assert json.loads(result.output) == view


def test_hours_period_without_pay_period(fake, monkeypatch):
    monkeypatch.setattr(rp, "pay_periods", lambda c: [])
    result = CliRunner().invoke(cli.app, ["hours", "period", "2026-11-04"])
    assert result.exit_code == 1
    assert "no pay period covering 2026-11-04" in result.output


def test_hours_changes(fake, monkeypatch):
    entries = [
        {
            "id": "te1",
            "date": "2026-11-03",
            "start": "09:00",
            "end": "17:30",
            "edits": [
                {
                    "at": "2026-11-04 10:00",
                    "action": "changed",
                    "by": "Alex Example",
                    "source": "web timesheet",
                    "details": ["end 17:30"],
                },
                {
                    "at": "2026-11-04 10:00",
                    "action": "sent for approval",
                    "by": "Rippling",
                    "source": "system",
                    "details": [],
                },
            ],
        },
        {"id": "te2", "date": "2026-11-04", "start": "09:00", "end": None, "edits": []},
    ]
    period = (date(2026, 11, 2), date(2026, 11, 8))
    monkeypatch.setattr(rp, "change_history", lambda c, d, tz: (period, entries))
    result = CliRunner().invoke(cli.app, ["hours", "changes"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[1] == "  Tue 2026-11-03  09:00-17:30"
    assert lines[2] == (
        "    2026-11-04 10:00  changed by Alex Example (web timesheet): end 17:30"
    )
    assert lines[3] == "    2026-11-04 10:00  sent for approval by Rippling"
    assert "2026-11-04  09:00" not in result.output  # no edits, not listed
    monkeypatch.setattr(rp, "change_history", lambda c, d, tz: (period, []))
    result = CliRunner().invoke(cli.app, ["hours", "changes"])
    assert "No recorded changes" in result.output


def test_todo(fake, monkeypatch):
    from rippling import inbox

    monkeypatch.setattr(inbox, "need_my_attention", lambda c: [])
    result = CliRunner().invoke(cli.app, ["todo"])
    assert result.exit_code == 0 and "Nothing needs your attention" in result.output
    item = {
        "id": "i1",
        "details": "",
        "requested_on": "2026-11-01T10:00:00Z",
        "requested_by": {"name": "Documents"},
        "task_render_info": {"task_title": "Sign handbook"},
    }
    monkeypatch.setattr(inbox, "need_my_attention", lambda c: [item])
    result = CliRunner().invoke(cli.app, ["todo"])
    assert "1 item(s) need your attention" in result.output
    assert "Sign handbook" in result.output
    assert "from Documents, requested 2026-11-01" in result.output
    assert "due" not in result.output
    assert fake.sent == []


HISTORY = [
    {
        "leaveTypeId": "CUSTOM",
        "transactions": [
            {
                "leavePolicy": {"id": "p2", "displayName": "Sick"},
                "date": "2026-02-01",
                "type": "LEAVE_REQUEST",
                "subType": "Sick day",
                "changeDays": "-1",
                "balanceDays": "4",
            }
        ],
    },
    {
        "leaveTypeId": "CUSTOM",
        "transactions": [
            {
                "leavePolicy": {"id": "p1", "displayName": "Annual Leave Policy"},
                "date": "2027-01-01",
                "type": "LEAVE_ACCRUAL",
                "subType": "Accrual",
                "changeDays": "25",
                "balanceDays": "30",
                "expirationDate": "2028-03-31",
            },
            {
                "leavePolicy": {"id": "p1", "displayName": "Annual Leave Policy"},
                "date": "2026-06-01",
                "type": "LEAVE_REQUEST",
                "subType": "Vacation",
                "changeDays": "-2.5",
                "balanceDays": "5",
                "explanation": "Approved",
            },
        ],
    },
]


def test_leave_history(fake, monkeypatch):
    monkeypatch.setattr(rp, "leave_transactions", lambda c: HISTORY)
    result = CliRunner().invoke(cli.app, ["leave", "history"])
    assert result.exit_code == 0, result.output
    assert "Sick" in result.output and "Annual Leave Policy" in result.output
    result = CliRunner().invoke(
        cli.app, ["leave", "history", "--policy", "annual leave policy"]
    )
    out = result.output
    assert "Sick" not in out
    assert "2026-06-01  request      -2.5        5  Vacation" in out
    assert "Approved" in out
    assert out.index("upcoming:") < out.index("2027-01-01")
    assert "+25" in out and "(expires 2028-03-31)" in out
    result = CliRunner().invoke(cli.app, ["leave", "history", "--policy", "Vacation"])
    assert result.exit_code == 1 and "No leave policy named 'Vacation'" in result.output
    result = CliRunner().invoke(cli.app, ["leave", "history", "--json"])
    assert [h["policy"] for h in json.loads(result.output)] == [
        "Sick",
        "Annual Leave Policy",
    ]


def test_leave_ics(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(rp, "leave_requests", lambda c: [LEAVE])
    result = CliRunner().invoke(cli.app, ["leave", "ics"])
    assert result.exit_code == 0, result.output
    raw = result.stdout_bytes
    assert raw.startswith(b"BEGIN:VCALENDAR\r\n") and raw.endswith(b"END:VCALENDAR\r\n")
    assert b"SUMMARY:Annual Leave Policy (pending)\r\n" in raw
    assert b"SUMMARY:Christmas Eve\r\n" in raw
    out = tmp_path / "leave.ics"
    result = CliRunner().invoke(cli.app, ["leave", "ics", "-o", str(out)])
    assert result.exit_code == 0 and "Wrote 2 events" in result.output
    assert out.read_bytes().count(b"BEGIN:VEVENT") == 2
    assert fake.sent == []


def test_hours_delete_previews_approved_entry(fake, monkeypatch):
    approved = _entry("e1", approval_status="TIME_ENTRY_APPROVAL_STATUS_APPROVED")
    monkeypatch.setattr(rp, "entries_on", lambda c, d, tz: [approved])
    result = CliRunner().invoke(cli.app, DELETE)
    assert result.exit_code == 0, result.output
    assert "already approved" in result.output and not fake.sent
