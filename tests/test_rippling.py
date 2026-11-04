from datetime import date, time

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


def test_build_time_entry_update_sets_object_id_first():
    body = rp.build_time_entry(
        "role", date(2026, 11, 4), time(9), time(17), [], TZ, None, object_id="e1"
    )
    assert next(iter(body)) == "object_id" and body["object_id"] == "e1"
    assert body["breaks"] == []


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
    spans = [(time(12), time(13))]
    assert rp.worked_minutes(time(9), time(18), spans, LUNCH) == 480
    paid = rp.BreakType(id="p", name="Paid", paid=True)
    assert rp.worked_minutes(time(9), time(18), spans, paid) == 540


def test_working_days_skips_weekends_and_holidays():
    schedule = [8, 8, 8, 8, 8, 0, 0]
    holidays = {
        date(2026, 12, 24),
        date(2026, 12, 25),
        date(2026, 12, 31),
        date(2027, 1, 1),
    }
    days = rp.working_days(date(2026, 12, 21), date(2027, 1, 1), schedule, holidays)
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
