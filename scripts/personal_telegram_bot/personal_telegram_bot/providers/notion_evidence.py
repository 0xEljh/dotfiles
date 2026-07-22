from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..tpot.evidence import EvidenceItem, normalize_evidence_text
from .notion_todos import TERMINAL_STATUSES


def build_notion_evidence_filters(target_date: date, tz: ZoneInfo) -> list[tuple[str, dict]]:
    day = target_date.isoformat()
    start = datetime.combine(target_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime.combine(target_date, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(
        timezone.utc
    )
    non_terminal = [
        {"property": "Status", "status": {"does_not_equal": status}}
        for status in TERMINAL_STATUSES
    ]
    return [
        (
            "task_done_on_date",
            {
                "and": [
                    {"property": "Date", "date": {"on_or_before": day}},
                    {"property": "Date", "date": {"on_or_after": day}},
                    {"property": "Status", "status": {"equals": "Done"}},
                ]
            },
        ),
        (
            "task_due",
            {
                "and": [
                    {"property": "Date", "date": {"on_or_before": day}},
                    *non_terminal,
                ]
            },
        ),
        (
            "task_edited",
            {
                "and": [
                    {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": start.isoformat()}},
                    {"timestamp": "last_edited_time", "last_edited_time": {"before": end.isoformat()}},
                    *non_terminal,
                ]
            },
        ),
    ]


def _parse_page(page: dict, kind: str, target_date: date, tz: ZoneInfo) -> EvidenceItem:
    props = page.get("properties") or {}
    title_parts = (props.get("Name") or {}).get("title") or []
    title = title_parts[0].get("plain_text") if title_parts else "Untitled"
    title = normalize_evidence_text(str(title), limit=300)[0]
    status = ((props.get("Status") or {}).get("status") or {}).get("name", "Unknown")
    date_value = (props.get("Date") or {}).get("date") or {}
    due = date_value.get("end") or date_value.get("start")
    edited = page.get("last_edited_time")
    occurred_at = (
        datetime.fromisoformat(str(edited).replace("Z", "+00:00"))
        if edited
        else datetime.combine(target_date, time(12), tzinfo=tz)
    )
    detail_parts = [f"status: {status}"]
    if due:
        detail_parts.append(f"date: {str(due)[:10]}")
    return EvidenceItem(
        key=f"notion:page:{page.get('id', 'unknown')}",
        source="notion",
        kind=kind,
        occurred_at=occurred_at,
        title=title,
        detail=", ".join(detail_parts),
        url=page.get("url"),
        private=True,
    )


def fetch_notion_evidence(
    notion_token: str, datasource_id: str, target_date: date, tz: ZoneInfo
) -> list[EvidenceItem]:
    from notion_client import Client

    notion = Client(auth=notion_token)
    by_id: dict[str, EvidenceItem] = {}
    for kind, filter_value in build_notion_evidence_filters(target_date, tz):
        cursor = None
        while True:
            kwargs = {
                "data_source_id": datasource_id,
                "filter": filter_value,
                "page_size": 100,
                "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            response = notion.data_sources.query(**kwargs)
            for page in response.get("results", []):
                key = str(page.get("id") or "unknown")
                candidate = _parse_page(page, kind, target_date, tz)
                current = by_id.get(key)
                if current is None or current.kind == "task_edited":
                    by_id[key] = candidate
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
    return sorted(by_id.values(), key=lambda item: (item.occurred_at, item.key))
