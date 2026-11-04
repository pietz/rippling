from datetime import date, time, timedelta

import pytest

from rippling import core as rp

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


@pytest.mark.parametrize(
    "entry, blocked",
    [
        ({"time_entry_event_details": {"job_shifts": [{"start_time": "x"}]}}, False),
        ({"time_entry_event_details": {"job_shifts": [{}, {}]}}, True),
        (
            {
                "time_entry_event_details": {
                    "job_shifts": [{"job_codes": [{"id": "j"}]}]
                }
            },
            True,
        ),
        ({"can_mutate": False}, True),
    ],
)
def test_update_blocker(entry, blocked):
    assert (rp.update_blocker(entry) is not None) == blocked


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
    {"id": "6ab3f8" + "b" * 18},
    {"id": "6ab3f8" + "c" * 18},
]


def test_find_leave_by_id_or_unique_prefix():
    assert rp.find_leave(REQS, "1a2b3c")["id"] == REQS[0]["id"]
    assert rp.find_leave(REQS, REQS[2]["id"])["id"] == REQS[2]["id"]
    with pytest.raises(ValueError, match="matches 2"):
        rp.find_leave(REQS, "6ab3f8")
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
