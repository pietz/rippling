"""The write commands must never send without --yes (or an interactive yes)."""

import json
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from rippling import cli
from rippling import core as rp
from rippling.api import TransportError
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
        ([_entry("e1", can_mutate=False)], "not editable"),
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
    assert result.exit_code == 1 and "already CANCELED" in result.output
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
