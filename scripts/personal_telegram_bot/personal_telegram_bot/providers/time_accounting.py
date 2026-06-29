"""Resolve the Notion Time-Accounting page URL for a given date.

The evening standdown deep-links the day's page — and a working link is itself a
signal that the day's ActivityWatch/sleep sync ran. The bot's own Notion
integration only sees the Bread board, so this uses the Time-Accountant
integration that owns the Time-Accounting database (the same secret + data source
`aw_notion_sync.py` uses). Best-effort: any miss — unconfigured, page not yet
created, or an API error — returns None so the caller falls back to the static
database URL.
"""

from __future__ import annotations

from datetime import date


def day_page_url(
    secret: str | None, datasource_id: str | None, day: date
) -> str | None:
    """The Notion page URL for the Time-Accounting row whose Date == `day`, or
    None if the integration isn't configured / the page doesn't exist / the query
    fails. Never raises — the standdown must send regardless."""
    if not secret or not datasource_id:
        return None
    try:
        from notion_client import Client

        notion = Client(auth=secret)
        pages = notion.data_sources.query(
            data_source_id=datasource_id,
            filter={"property": "Date", "date": {"equals": day.isoformat()}},
        ).get("results", [])
    except Exception:
        return None
    if not pages:
        return None
    return pages[0].get("url")
