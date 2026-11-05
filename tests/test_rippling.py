from datetime import date, time, timedelta

import pytest

from rippling import core as rp
from rippling.auth import Credentials

TZ = "Europe/Berlin"
LUNCH = rp.BreakType(id="bt", name="Lunch Break", paid=False)


def test_parse_date_keywords():
    today = date(2026, 11, 4)
    assert rp.parse_date("today", today) == today
    assert rp.parse_date("yesterday", today) == date(2026, 11, 3)
    assert rp.parse_date("2026-12-01") == date(2026, 12, 1)
    with pytest.raises(ValueError):
        rp.parse_date("04.11.2026")


def test_parse_time_and_span():
    assert rp.parse_time("9") == time(9, 0)
    assert rp.parse_time("09:30") == time(9, 30)
    assert rp.parse_span("12:00-12:45") == (time(12), time(12, 45))
    with pytest.raises(ValueError):
        rp.parse_span("12:00")


@pytest.mark.parametrize(
    "day, expected",
    [
        (date(2026, 11, 4), "2026-11-04T08:00:00.000Z"),  # CET, UTC+1
        (date(2026, 7, 1), "2026-07-01T07:00:00.000Z"),  # CEST, UTC+2
        (date(2026, 3, 29), "2026-03-29T07:00:00.000Z"),  # DST starts 02:00 that day
        (date(2026, 10, 25), "2026-10-25T08:00:00.000Z"),  # DST ends 03:00 that day
    ],
)
def test_utc_iso_respects_dst(day, expected):
    assert rp.utc_iso(day, time(9), TZ) == expected


def test_build_time_entry_create():
    body = rp.build_time_entry(
        "role", date(2026, 11, 4), time(9), time(18), [(time(12), time(13))], TZ, LUNCH
    )
    assert "object_id" not in body
    assert body["start_time"] == "2026-11-04T08:00:00.000Z"
    assert body["end_time"] == "2026-11-04T17:00:00.000Z"
    assert body["job_shifts"] == [
        {"start_time": body["start_time"], "end_time": body["end_time"]}
    ]
    assert body["breaks"] == [
        {
            "start_time": "2026-11-04T11:00:00.000Z",
            "end_time": "2026-11-04T12:00:00.000Z",
            "company_break_type_id": "bt",
            "duration_minutes": 60,
        }
    ]


def test_build_time_entry_update_sets_object_id_and_keeps_note():
    old = {
        "id": "e1",
        "time_entry_event_details": {"note": "Offsite", "schedule_id": None},
    }
    body = rp.build_time_entry(
        "role", date(2026, 11, 4), time(9), time(17), [], TZ, None, existing=old
    )
    assert next(iter(body)) == "object_id" and body["object_id"] == "e1"
    assert body["note"] == "Offsite" and "schedule_id" not in body
    assert body["breaks"] == []


@pytest.mark.parametrize(
    "t, kind",
    [(time(2, 30), "does not exist"), (time(2, 0), "does not exist")],
)
def test_spring_forward_gap_is_rejected(t, kind):
    with pytest.raises(ValueError, match=kind):
        rp.utc_iso(date(2026, 3, 29), t, TZ)


def test_fall_back_repeated_hour_is_rejected():
    with pytest.raises(ValueError, match="ambiguous"):
        rp.utc_iso(date(2026, 10, 25), time(2, 30), TZ)


def test_worked_minutes_uses_elapsed_time_across_dst():
    # 01:00-04:00 on the spring-forward day is 2 real hours, not 3.
    body = rp.build_time_entry("r", date(2026, 3, 29), time(1), time(4), [], TZ, None)
    assert rp.worked_minutes(body, paid_breaks=False) == 120


def _ev(start="2026-11-04T08:00:00Z", end="2026-11-04T17:00:00Z", **kw):
    shifts = kw.pop("shifts", [{"start_time": "x"}])
    return {
        "id": "e1",
        "can_mutate": True,
        "event_times": {"start_time": start, "end_time": end},
        "time_entry_event_details": {"job_shifts": shifts},
    } | kw


@pytest.mark.parametrize(
    "entry, reason",
    [
        (_ev(), None),
        (_ev(shifts=[{}, {}]), "several job shifts"),
        (_ev(shifts=[{"job_codes": [{"id": "j"}]}]), "job codes"),
        (_ev(can_mutate=False), "editable"),
        ({k: v for k, v in _ev().items() if k != "can_mutate"}, "editable"),
        (_ev(end=None), "no end time"),
        # 22:00-06:00 Berlin time: replacing it with a same-day entry would cut it.
        (_ev("2026-11-04T21:00:00Z", "2026-11-05T05:00:00Z"), "past midnight"),
        # Ending exactly at local midnight is still the same day.
        (_ev("2026-11-04T17:00:00Z", "2026-11-04T23:00:00Z"), None),
    ],
)
def test_update_blocker(entry, reason):
    why = rp.update_blocker(entry, TZ)
    assert why is None if reason is None else reason in why


class RecordClient:
    def __init__(self, record):
        self.record = record

    def get(self, path, **params):
        assert path == "/time_tracking/api/time_entries/e1/"
        return self.record


CLEAN = {k: [] for k in rp.UNMANAGED_FIELDS} | {"shiftInputGroupValues": {}}


@pytest.mark.parametrize(
    "record, reason",
    [
        (CLEAN, None),
        (CLEAN | {"attachments": [{"id": "a"}]}, "attachments"),
        (CLEAN | {"comments": [{"text": "hi"}]}, "comments"),
        (CLEAN | {"shiftInputGroupValues": {"g": 1}}, "custom shift inputs"),
        ({"attachments": []}, "couldn't be checked"),  # unknown shape: fail closed
        (None, "couldn't be checked"),
    ],
)
def test_unmanaged_data_fails_closed(record, reason):
    why = rp.unmanaged_data(RecordClient(record), {"id": "TimeEntry:e1"})
    assert why is None if reason is None else reason in why


class PeriodClient:
    """Pay periods plus a fixed event list for every summary."""

    creds = Credentials(access_token="t", company_id="c", role_id="r", expires_at=9e9)

    def __init__(self, periods, events):
        self.periods, self.events = periods, events

    def get(self, path, **params):
        if path.endswith("get_pay_periods_for_role"):
            return {"periods": self.periods}
        return {"ids": ["s1"]}

    def twirp(self, method, body):
        return {"events": self.events}


def test_entries_on_needs_a_covering_pay_period():
    c = PeriodClient([["2026-11-02", "2026-11-08"]], [])
    assert rp.entries_on(c, date(2026, 11, 4), TZ) == []
    with pytest.raises(ValueError, match="no pay period"):
        rp.entries_on(c, date(2026, 11, 20), TZ)


def test_entries_on_uses_local_dates_and_refuses_overnight_spill():
    early = _ev("2026-11-03T23:30:00Z", "2026-11-04T08:00:00Z")  # 00:30 local, on 4.11.
    c = PeriodClient([["2026-11-02", "2026-11-08"]], [early])
    assert rp.entries_on(c, date(2026, 11, 4), TZ) == [early]
    assert rp.entries_on(c, date(2026, 11, 3), TZ) == []
    spill = _ev("2026-11-03T21:00:00Z", "2026-11-04T05:00:00Z")  # 22:00-06:00 local
    c = PeriodClient([["2026-11-02", "2026-11-08"]], [spill])
    with pytest.raises(ValueError, match="runs into 2026-11-04"):
        rp.entries_on(c, date(2026, 11, 4), TZ)


def test_leave_policy_matches_loosely_and_lists_alternatives():
    summary = [
        {"policyDisplayName": " Annual  Leave Policy "},
        {"policyDisplayName": "Sick"},
    ]
    assert rp.leave_policy(summary) is summary[0]
    assert rp.leave_policy(summary, "sick") is summary[1]
    with pytest.raises(ValueError, match="'Sick'.*--policy"):
        rp.leave_policy(summary, "Vacation")


def test_leave_request_range_is_bounded():
    with pytest.raises(ValueError, match="more than 366 days"):
        rp.build_leave_request("r", "c", "p", date(2026, 12, 21), date(2062, 12, 23))


@pytest.mark.parametrize(
    "res, ok",
    [
        (True, True),
        ({"error": [{"error_code": "SUCCESS", "success_message": "fine"}]}, True),
        ({"error": [{"error_code": "PTO_ENDDATE", "message": "bad"}]}, False),
        (False, False),
        (None, False),
        ({}, False),
        ({"error": []}, False),
        ({"detail": "something"}, False),
    ],
)
def test_validation_fails_closed(res, ok):
    assert (rp.validation_errors(res) == []) == ok


@pytest.mark.parametrize(
    "start, end, breaks",
    [
        (time(18), time(9), []),  # end before start
        (time(9), time(18), [(time(19), time(19, 30))]),  # break outside entry
        (time(9), time(18), [(time(13), time(12))]),  # break backwards
        (
            time(9),
            time(18),
            [(time(12), time(13)), (time(12, 30), time(14))],
        ),  # overlap
    ],
)
def test_build_time_entry_rejects_bad_input(start, end, breaks):
    with pytest.raises(ValueError):
        rp.build_time_entry("role", date(2026, 11, 4), start, end, breaks, TZ, LUNCH)


def test_worked_minutes_unpaid_vs_paid_break():
    body = rp.build_time_entry(
        "r", date(2026, 11, 4), time(9), time(18), [(time(12), time(13))], TZ, LUNCH
    )
    assert rp.worked_minutes(body, paid_breaks=False) == 480
    assert rp.worked_minutes(body, paid_breaks=True) == 540


def test_working_days_skips_weekends_and_holidays():
    start, end = date(2026, 12, 21), date(2027, 1, 1)
    schedule = {
        start + timedelta(days=i): (
            8.0 if (start + timedelta(days=i)).weekday() < 5 else 0.0
        )
        for i in range(12)
    }
    holidays = {
        date(2026, 12, 24),
        date(2026, 12, 25),
        date(2026, 12, 31),
        date(2027, 1, 1),
    }
    days = rp.working_days(start, end, schedule, holidays)
    assert days == [
        date(2026, 12, 21),
        date(2026, 12, 22),
        date(2026, 12, 23),
        date(2026, 12, 28),
        date(2026, 12, 29),
        date(2026, 12, 30),
    ]


def test_build_leave_request():
    body = rp.build_leave_request(
        "r", "c", "p", date(2026, 12, 21), date(2026, 12, 23), "Urlaub"
    )
    assert body["startDate"] == "2026-12-21" and body["endDate"] == "2026-12-23"
    assert body["reasonForLeave"] == "Urlaub"
    assert "reasonForLeave" not in rp.build_leave_request(
        "r", "c", "p", date(2026, 12, 21), date(2026, 12, 21)
    )
    with pytest.raises(ValueError):
        rp.build_leave_request("r", "c", "p", date(2026, 12, 23), date(2026, 12, 21))


REQS = [
    {"id": "1a2b3c" + "a" * 18},
    {"id": "1a2b3d" + "b" * 18},
    {"id": "1a2b3d" + "c" * 18},
]


def test_find_leave_by_id_or_unique_prefix():
    assert rp.find_leave(REQS, "1a2b3c")["id"] == REQS[0]["id"]
    assert rp.find_leave(REQS, REQS[2]["id"])["id"] == REQS[2]["id"]
    with pytest.raises(ValueError, match="matches 2"):
        rp.find_leave(REQS, "1a2b3d")
    with pytest.raises(ValueError, match="at least"):
        rp.find_leave(REQS, "6ab")
    with pytest.raises(ValueError, match="No leave request"):
        rp.find_leave(REQS, "ffffff")


def test_entry_view_computes_worked_hours_when_missing():
    e = {
        "id": "x",
        "event_times": {
            "start_time": "2026-11-04T08:00:00Z",
            "end_time": "2026-11-04T17:00:00Z",
        },
        "time_entry_event_details": {
            "breaks": [
                {
                    "start_time": "2026-11-04T11:00:00Z",
                    "end_time": "2026-11-04T12:00:00Z",
                    "duration_minutes": 60,
                }
            ]
        },
        "approval_status": "TIME_ENTRY_APPROVAL_STATUS_APPROVED",
    }
    v = rp.entry_view(e, TZ)
    assert v["start"] == "09:00" and v["end"] == "18:00" and v["date"] == "2026-11-04"
    assert v["worked_hours"] == 8.0 and v["status"] == "approved"
    assert v["breaks"] == [{"start": "12:00", "end": "13:00"}]


class ScheduleClient:
    """Answers get_working_schedule like Rippling: the 7 days starting at the anchor."""

    def __init__(self, hours_for):
        self.hours_for, self.anchors = hours_for, []
        self.creds = type("C", (), {"role_id": "r"})()

    def post(self, path, body):
        m, d, y = map(int, body["anchorDate"].split("/"))
        anchor = date(y, m, d)
        self.anchors.append(anchor)
        return [str(self.hours_for(anchor + timedelta(days=i))) for i in range(7)]


def test_scheduled_hours_maps_anchor_relative_weeks_to_dates():
    # Mon-Fri 8 h until 2026-10-18, then Mon-Thu 6 h: the change must show mid-month.
    def hours_for(d):
        if d.weekday() >= 5 or (d >= date(2026, 10, 19) and d.weekday() == 4):
            return 0
        return 8 if d < date(2026, 10, 19) else 6

    c = ScheduleClient(hours_for)
    got = rp.scheduled_hours(
        c, date(2026, 10, 1), date(2026, 10, 31)
    )  # starts on a Thursday
    assert len(got) == 31 and c.anchors[0] == date(2026, 10, 1) and len(c.anchors) == 5
    assert got[date(2026, 10, 1)] == 8 and got[date(2026, 10, 3)] == 0
    assert got[date(2026, 10, 20)] == 6 and got[date(2026, 10, 23)] == 0
    assert all(d.month == 10 for d in got)


LEAVE_HALF = {
    "startDate": "2026-05-19",
    "endDate": "2026-05-19",
    "startDateCustomHours": "4.00",
    "endDateCustomHours": "0.00",
}


@pytest.mark.parametrize(
    "req, day, expected",
    [
        (LEAVE_HALF, date(2026, 5, 19), 4.0),  # custom hours on a single day
        (LEAVE_HALF, date(2026, 5, 20), 0.0),  # outside the request
        ({"startDate": "2026-05-18", "endDate": "2026-05-20"}, date(2026, 5, 19), 8.0),
        (
            {
                "startDate": "2026-05-18",
                "endDate": "2026-05-20",
                "endDateHalfDay": True,
            },
            date(2026, 5, 20),
            4.0,
        ),
    ],
)
def test_leave_hours_on(req, day, expected):
    assert rp.leave_hours_on(req, day, 8.0) == expected


class ForecastClient:
    creds = type("C", (), {"role_id": "r"})()

    def __init__(self, res):
        self.res = res

    def post(self, path, body):
        return self.res


@pytest.mark.parametrize(
    "res, expected",
    [
        ({"forecastAvailable": True, "balanceDays": "22.00"}, 22.0),
        ({"forecastAvailable": True, "balanceDays": None}, None),
        ({"forecastAvailable": True}, None),
        ({"forecastAvailable": False, "balanceDays": "5"}, None),
        (None, None),
    ],
)
def test_forecast_days_tolerates_partial_answers(res, expected):
    got = rp.forecast_days(ForecastClient(res), "p", {}, date(2027, 4, 1))
    assert got == expected
