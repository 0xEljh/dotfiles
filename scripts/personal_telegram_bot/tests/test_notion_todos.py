from datetime import date
from zoneinfo import ZoneInfo

from personal_telegram_bot.providers.notion_evidence import build_notion_evidence_filters
from personal_telegram_bot.providers.notion_todos import (
    TERMINAL_STATUSES,
    Task,
    build_open_tasks_filter,
    parse_task,
    split_by_due,
)

TODAY = date(2026, 6, 11)


def page(title="Do the thing", status="Not started", start="2026-06-11", end=None):
    return {
        "id": "abc-123",
        "url": "https://www.notion.so/Do-the-thing-abc123",
        "properties": {
            "Name": {"title": [{"plain_text": title}]},
            "Status": {"status": {"name": status}},
            "Date": {"date": {"start": start, "end": end} if start else None},
        },
    }


def test_terminal_statuses_match_bread_schema():
    assert set(TERMINAL_STATUSES) == {"Done", "DNF", "Delegated", "Cancelled"}


def test_filter_excludes_terminal_statuses_and_bounds_date():
    f = build_open_tasks_filter(TODAY)
    clauses = f["and"]
    date_clauses = [c for c in clauses if c.get("property") == "Date"]
    assert date_clauses == [{"property": "Date", "date": {"on_or_before": "2026-06-11"}}]
    excluded = {
        c["status"]["does_not_equal"]
        for c in clauses
        if c.get("property") == "Status"
    }
    assert excluded == set(TERMINAL_STATUSES)


def test_parse_task_basic():
    t = parse_task(page())
    assert t.title == "Do the thing"
    assert t.status == "Not started"
    assert t.due_start == date(2026, 6, 11)
    assert t.due_end is None
    assert t.url == "https://www.notion.so/Do-the-thing-abc123"


def test_parse_task_handles_datetime_start():
    t = parse_task(page(start="2026-06-11T14:00:00.000+08:00"))
    assert t.due_start == date(2026, 6, 11)


def test_parse_task_untitled_and_dateless():
    p = page(start=None)
    p["properties"]["Name"]["title"] = []
    t = parse_task(p)
    assert t.title == "Untitled"
    assert t.due_start is None


def test_split_by_due():
    overdue_task = Task("old", "Not started", date(2026, 6, 9), None, None)
    today_task = Task("now", "In progress", TODAY, None, None)
    spanning_task = Task("span", "In progress", date(2026, 6, 9), TODAY, None)
    overdue, due_today = split_by_due([overdue_task, today_task, spanning_task], TODAY)
    assert [t.title for t in overdue] == ["old"]
    assert {t.title for t in due_today} == {"now", "span"}


def test_evidence_filters_cover_done_due_and_edited_cohorts():
    filters = build_notion_evidence_filters(TODAY, ZoneInfo("Asia/Singapore"))

    assert [kind for kind, _ in filters] == ["task_done_on_date", "task_due", "task_edited"]
    assert "last_edited_time" in str(filters[2][1])
    assert all(status in str(filters[1][1]) for status in TERMINAL_STATUSES)
