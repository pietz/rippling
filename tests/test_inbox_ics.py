from datetime import UTC, date, datetime

from rippling import ics, inbox

TZ = "Europe/Berlin"


class InboxClient:
    def __init__(self, pages):
        self.pages, self.bodies = pages, []

    def post(self, path, body):
        assert path == inbox.LIST_PATH
        self.bodies.append(body)
        return self.pages[len(self.bodies) - 1]


def test_inbox_follows_the_cursor():
    c = InboxClient(
        [
            {"data": [{"id": "a"}], "pagination": {"next": "c2", "total_count": 2}},
            {"data": [{"id": "b"}], "pagination": {"next": "", "total_count": 2}},
        ]
    )
    assert [i["id"] for i in inbox.need_my_attention(c)] == ["a", "b"]
    assert [b["page_query"]["cursor"] for b in c.bodies] == ["", "c2"]
    assert c.bodies[0]["inbox_view_group"] == "INBOX_VIEW_GROUP_NEED_MY_ATTENTION"


def test_inbox_page_limit():
    page = {"data": [{"id": "x"}], "pagination": {"next": "same"}}
    c = InboxClient([page] * 20)
    assert len(inbox.need_my_attention(c)) == inbox.MAX_PAGES


def test_inbox_item_view():
    item = {
        "id": "i1",
        "type": "INBOX_ITEM_TYPE_TASK",
        "details": "Please sign the updated handbook.",
        "requested_on": "2026-11-01T23:30:00Z",  # 2 Nov in Berlin
        "due_date": "2026-11-10T12:00:00Z",
        "requested_by": {"type": "INBOX_ACTOR_TYPE_APP", "name": "Documents"},
        "task_render_info": {"task_title": {"text": "Sign handbook"}, "buttons": []},
    }
    assert inbox.item_view(item, TZ) == {
        "id": "i1",
        "title": "Sign handbook",
        "details": "Please sign the updated handbook.",
        "from": "Documents",
        "requested": "2026-11-02",
        "due": "2026-11-10",
        "type": "task",
    }
    bare = inbox.item_view({"id": "i2", "sub_type": "REVIEW_TIMECARD"}, TZ)
    assert bare["title"] == "Review timecard" and bare["due"] is None


def test_escape_and_fold():
    assert ics.escape("a,b;c\\d\ne") == r"a\,b\;c\\d\ne"
    line = "SUMMARY:" + "Überstunden-Ausgleich " * 8
    folded = ics.fold(line)
    parts = folded.split("\r\n")
    assert len(parts) > 1 and all(len(p.encode()) <= 75 for p in parts)
    assert all(p.startswith(" ") for p in parts[1:])
    assert "".join([parts[0], *(p[1:] for p in parts[1:])]) == line
    assert ics.fold("short") == "short"


STAMP = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
REQUESTS = [
    {
        "id": "req1",
        "startDate": "2026-12-21",
        "endDate": "2026-12-23",
        "numDays": "3.00",
        "policyDisplayName": "Annual Leave Policy",
        "status": "APPROVED",
        "reasonForLeave": "Winter, family",
    },
    {
        "id": "req2",
        "startDate": "2027-02-01",
        "endDate": "2027-02-01",
        "numDays": "0.50",
        "policyDisplayName": "Annual Leave Policy",
        "status": "PENDING",
    },
    {
        "id": "req3",
        "startDate": "2027-03-01",
        "endDate": "2027-03-02",
        "policyDisplayName": "Annual Leave Policy",
        "status": "CANCELED",
    },
]
HOLIDAYS = {
    date(2026, 12, 25): "Christmas",
    date(2026, 12, 26): "Christmas",
    date(2027, 1, 1): "New Year",
}


def _events(text):
    return [
        dict(line.split(":", 1) for line in block.strip("\r\n").split("\r\n"))
        for block in text.split("BEGIN:VEVENT")[1:]
    ]


def test_calendar():
    text, count = ics.calendar(REQUESTS, HOLIDAYS, STAMP)
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    events = _events(text.replace("END:VCALENDAR\r\n", ""))
    assert count == len(events) == 4  # cancelled request left out, Christmas merged
    leave, xmas, ny, pending = events
    assert leave["UID"] == "leave-req1@rippling-cli"
    assert leave["DTSTART;VALUE=DATE"] == "20261221"
    assert leave["DTEND;VALUE=DATE"] == "20261224"  # exclusive
    assert leave["SUMMARY"] == "Annual Leave Policy"
    assert leave["DESCRIPTION"] == r"3 day(s)\; Winter\, family"
    assert leave["DTSTAMP"] == "20261105T120000Z"
    assert xmas["DTSTART;VALUE=DATE"] == "20261225"
    assert xmas["DTEND;VALUE=DATE"] == "20261227"
    assert xmas["UID"] == "holiday-20261225@rippling-cli"
    assert ny["SUMMARY"] == "New Year"
    assert pending["SUMMARY"] == "Annual Leave Policy (pending)"
    assert pending["DTEND;VALUE=DATE"] == "20270202"


def test_calendar_uids_are_stable():
    a, _ = ics.calendar(REQUESTS, HOLIDAYS, STAMP)
    b, _ = ics.calendar(REQUESTS, HOLIDAYS, datetime(2027, 1, 1, tzinfo=UTC))
    uids = [line for line in a.split("\r\n") if line.startswith("UID:")]
    assert uids == [line for line in b.split("\r\n") if line.startswith("UID:")]
