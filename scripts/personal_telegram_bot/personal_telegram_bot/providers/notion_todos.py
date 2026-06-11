from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Bread statuses that mean a task no longer needs attention.
TERMINAL_STATUSES = ["Done", "DNF", "Delegated", "Cancelled"]


@dataclass(frozen=True)
class Task:
    title: str
    status: str
    due_start: date | None
    due_end: date | None
    url: str | None

    @property
    def effective_due(self) -> date | None:
        return self.due_end or self.due_start


def build_open_tasks_filter(today: date) -> dict:
    """Open (non-terminal) Bread tasks due today or earlier.

    Notion date filters skip pages with an empty Date, so dateless tasks are
    naturally excluded from the digest.
    """
    return {
        "and": [
            {"property": "Date", "date": {"on_or_before": today.isoformat()}},
            *[
                {"property": "Status", "status": {"does_not_equal": status}}
                for status in TERMINAL_STATUSES
            ],
        ]
    }


def _parse_notion_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw[:10])


def parse_task(page: dict) -> Task:
    props = page.get("properties", {})
    title_parts = props.get("Name", {}).get("title", [])
    title = title_parts[0]["plain_text"] if title_parts else "Untitled"
    status = (props.get("Status", {}).get("status") or {}).get("name", "Unknown")
    date_prop = props.get("Date", {}).get("date") or {}
    return Task(
        title=title,
        status=status,
        due_start=_parse_notion_date(date_prop.get("start")),
        due_end=_parse_notion_date(date_prop.get("end")),
        url=page.get("url"),
    )


def split_by_due(tasks: list[Task], today: date) -> tuple[list[Task], list[Task]]:
    """Split into (overdue, due_today). A range that reaches today counts as due today."""
    overdue = [t for t in tasks if t.effective_due and t.effective_due < today]
    due_today = [t for t in tasks if t.effective_due and t.effective_due >= today]
    return overdue, due_today


def fetch_due_tasks(
    notion_token: str, datasource_id: str, today: date
) -> tuple[list[Task], list[Task]]:
    from notion_client import Client

    notion = Client(auth=notion_token)
    results: list[dict] = []
    cursor = None
    while True:
        kwargs = {"data_source_id": datasource_id, "filter": build_open_tasks_filter(today)}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = notion.data_sources.query(**kwargs)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    tasks = [parse_task(page) for page in results]
    return split_by_due(tasks, today)
