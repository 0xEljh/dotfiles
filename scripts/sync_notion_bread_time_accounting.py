# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "notion-client",
#     "python-dotenv",
# ]
# ///

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from notion_client import Client
from dotenv import load_dotenv


current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

NOTION_API_KEY = os.getenv("NOTION_TIME_ACCOUNTANT_SECRET")
NOTION_DATASOURCE_ID = os.getenv("NOTION_TIME_ACCOUNTING_DATASOURCE_ID")
NOTION_BREAD_DATASOURCE_ID = os.getenv("NOTION_BREAD_DATASOURCE_ID")
TARGET_TIMEZONE = ZoneInfo(os.getenv("CURRENT_TIMEZONE", "America/New_York"))

def main():
    today_str = datetime.now(TARGET_TIMEZONE).strftime("%Y-%m-%d")
    print(f"Retrieving tasks for {today_str}")
    
    notion = Client(auth=NOTION_API_KEY)

    try:
        time_accounting_page = notion.data_sources.query(
            data_source_id=NOTION_DATASOURCE_ID,
            filter={"property": "Date", "date": {"equals": today_str}},
        ).get("results")

        if not time_accounting_page:
            print(f"No Time accounting page found for today, {today_str}")
            return

        # get the bread pages which fit the filter condition of containing today in the date range
        # and that are DONE.

        bread_pages = notion.data_sources.query(
            data_source_id=NOTION_BREAD_DATASOURCE_ID,
            filter={
                "and": [
                    {
                        "property": "Date",
                        "date": {"on_or_before": today_str}
                    },
                    {
                        "property": "Date",
                        "date": {"on_or_after": today_str}
                    },
                    {
                        "or": [
                            {
                                "property": "Status",
                                "status": {
                                "equals": "Done"
                                }
                            },
                            {
                                "property": "Status",
                                "status": {
                                "equals": "Delegated"
                                }
                            },
                            {
                                "property": "Status",
                                "status": {
                                "equals": "DNF"
                                }
                            }
                        ]
                    }
                ]
            }
        ).get("results")

        if not bread_pages:
            print(f"No completed tasks in the tasks data source on {today_str}")
            return

        # Extract page IDs from bread pages to create relation
        bread_page_ids = [{"id": page["id"]} for page in bread_pages]

        # Get the Time Accounting page ID
        time_accounting_page_id = time_accounting_page[0]["id"]

        # Update the Time Accounting page's "Tasks" relation property
        notion.pages.update(
            page_id=time_accounting_page_id,
            properties={
                "Tasks": {
                    "relation": bread_page_ids
                }
            }
        )

        print(f"Successfully linked {len(bread_pages)} task(s) to Time Accounting page for {today_str}")
        for page in bread_pages:
            title = page.get("properties", {}).get("Name", {}).get("title", [])
            name = title[0]["plain_text"] if title else "Untitled"
            print(f"  - {name}")

    except Exception as e:
        print(f"Error syncing Notion: {e}")
        raise


if __name__ == "__main__":
    main()
