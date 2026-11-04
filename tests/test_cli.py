"""The write commands must never send without --yes (or an interactive yes)."""

from datetime import date

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
    monkeypatch.setattr(rp, "schedule_hours", lambda c, d: [8, 8, 8, 8, 8, 0, 0])
    monkeypatch.setattr(rp, "overlapping_leave", lambda c, a, b: [])
    return client


HOURS = ["hours", "2026-11-04", "09:00", "18:00", "--break", "12:00-13:00"]
VACATION = ["vacation", "2026-12-21", "2026-12-24"]


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
