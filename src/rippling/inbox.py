"""Inbox items waiting for the user. Read only: nothing is marked viewed or snoozed."""

from __future__ import annotations

from .api import Client
from .core import display_text, local_date

LIST_PATH = "/hub/twirp/inbox.v1.InboxReadService/ListInboxItems"
PAGE_SIZE = 100
MAX_PAGES = 10


def need_my_attention(c: Client) -> list[dict]:
    """Raw items in the "Needs my attention" view, following the page cursor."""
    found, cursor = [], ""
    for _ in range(MAX_PAGES):
        res = c.post(
            LIST_PATH,
            {
                "inbox_view_group": "INBOX_VIEW_GROUP_NEED_MY_ATTENTION",
                "group_by": [],
                "sorts": [{"field": "due_date", "descending": False}],
                "filters": [],
                "include_attributes": False,
                "page_query": {"size": PAGE_SIZE, "cursor": cursor},
            },
        )
        res = res if isinstance(res, dict) else {}
        found += res.get("data") or []
        cursor = (res.get("pagination") or {}).get("next") or ""
        if not cursor:
            break
    return found


def _day(iso: object, tz: str) -> str | None:
    return local_date(iso, tz).isoformat() if isinstance(iso, str) and iso else None


def item_view(item: dict, tz: str) -> dict:
    render = item.get("task_render_info") or {}
    details = (item.get("details") or "").strip()
    sub_type = (item.get("sub_type") or "").replace("_", " ").strip().capitalize()
    title = display_text(render.get("task_title")) or details or sub_type or "Untitled"
    kind = (item.get("type") or "").removeprefix("INBOX_ITEM_TYPE_").lower()
    return {
        "id": item.get("id"),
        "title": title,
        "details": details if details and details != title else None,
        "from": (item.get("requested_by") or {}).get("name") or None,
        "requested": _day(item.get("requested_on"), tz),
        "due": _day(item.get("due_date"), tz),
        "type": kind or None,
    }
