"""Paper Inbox provider: captured papers ("sightings") awaiting refinement into
the expedition log. A row is pending until its Status select is set to
"landed". Notion's select does_not_equal filter also matches rows with no
Status set, so the single filter covers freshly captured rows too."""

from __future__ import annotations

# The status a paper reaches once refined into a post; anything else —
# including no status yet — is an unrefined sighting.
LANDED_STATUS = "landed"

PENDING_FILTER = {"property": "Status", "select": {"does_not_equal": LANDED_STATUS}}


def parse_title(page: dict) -> str:
    # The Paper Inbox title property is named "Title" (unlike Bread's "Name").
    title_parts = page.get("properties", {}).get("Title", {}).get("title", [])
    return title_parts[0]["plain_text"] if title_parts else "Untitled"


def fetch_pending(notion_token: str, datasource_id: str) -> list[str]:
    """Titles of Paper Inbox rows whose Status is anything but "landed"."""
    from notion_client import Client

    notion = Client(auth=notion_token)
    results: list[dict] = []
    cursor = None
    while True:
        kwargs = {"data_source_id": datasource_id, "filter": PENDING_FILTER}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = notion.data_sources.query(**kwargs)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return [parse_title(page) for page in results]
