# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "notion-client",
#     "python-dotenv",
# ]
# ///

"""
WakaTime -> Notion sync script.
Fetches coding time from WakaTime and updates Notion page property.

Supports:
- Timezone-aware journal days (TARGET_TZ env var)
- Sliding window: updates today + yesterday (if within FREEZE_HOURS)
- CLI flags: --yesterday, --date YYYY-MM-DD for manual backfills
"""

import os
import sys
import argparse
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from notion_client import Client
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

WAKATIME_API_KEY = os.getenv("WAKATIME_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_TIME_ACCOUNTANT_SECRET")
NOTION_DATASOURCE_ID = os.getenv("NOTION_TIME_ACCOUNTING_DATASOURCE_ID")

# Journal timezone: the timezone used for defining "a day" in Notion
# Should match your WakaTime account timezone setting
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "America/New_York"))

# How many hours into "today" we continue to update "yesterday"
FREEZE_HOURS = int(os.getenv("FREEZE_HOURS", "2"))


def sync_date(target_date: date, notion: Client) -> bool:
    """
    Sync WakaTime data for a specific date to Notion.
    Returns True on success, False on failure.
    """
    date_str = target_date.strftime("%Y-%m-%d")
    print(f"\nSyncing WakaTime for: {date_str}")

    # WakaTime Fetch
    wt_url = "https://wakatime.com/api/v1/users/current/summaries"
    try:
        resp = requests.get(wt_url, params={
            "start": date_str, "end": date_str, "api_key": WAKATIME_API_KEY
        })
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            print(f"No WakaTime data for {date_str}")
            return False
        minutes = int(data[0]["grand_total"]["total_seconds"] / 60)
    except Exception as e:
        print(f"WakaTime Error: {e}")
        return False

    # Notion Update
    try:
        pages = notion.data_sources.query(
            data_source_id=NOTION_DATASOURCE_ID,
            filter={"property": "Date", "date": {"equals": date_str}}
        ).get("results")

        if not pages:
            print(f"No Notion page found for {date_str}")
            return False

        notion.pages.update(
            page_id=pages[0]["id"],
            properties={"Minutes Coding": {"number": minutes}}
        )
        print(f"Success: {minutes} mins logged for {date_str}")
        return True
    except Exception as e:
        print(f"Notion Error: {e}")
        return False


def determine_dates_to_sync(args) -> list[date]:
    """
    Determine which dates to sync based on CLI args and freeze rule.
    
    Priority:
    1. --date: sync exactly that date
    2. --yesterday: sync yesterday only
    3. Default: sync today, and yesterday if within FREEZE_HOURS
    """
    now = datetime.now(TARGET_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)
    
    if args.date:
        # Explicit date backfill
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
            print(f"Manual backfill requested for: {target}")
            return [target]
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
            sys.exit(1)
    
    if args.yesterday:
        print(f"Yesterday-only mode requested")
        return [yesterday]
    
    # Default: sliding window with freeze rule
    dates = [today]
    
    if now.hour < FREEZE_HOURS:
        print(f"Within freeze window ({now.hour}h < {FREEZE_HOURS}h), including yesterday")
        dates.append(yesterday)
    else:
        print(f"Past freeze window ({now.hour}h >= {FREEZE_HOURS}h), today only")
    
    return dates


def main():
    parser = argparse.ArgumentParser(
        description="Sync WakaTime data to Notion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run waka_notion_sync.py              # Sync today (+ yesterday if <2am)
  uv run waka_notion_sync.py --yesterday  # Sync yesterday only
  uv run waka_notion_sync.py --date 2025-12-04  # Backfill specific date
        """
    )
    parser.add_argument(
        "--yesterday", "-y",
        action="store_true",
        help="Sync yesterday only (ignores freeze rule)"
    )
    parser.add_argument(
        "--date", "-d",
        type=str,
        metavar="YYYY-MM-DD",
        help="Sync a specific date (manual backfill)"
    )
    args = parser.parse_args()
    
    print(f"WakaTime -> Notion Sync")
    print(f"Journal timezone: {TARGET_TZ}")
    print(f"Current time: {datetime.now(TARGET_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Determine which dates to sync
    dates_to_sync = determine_dates_to_sync(args)
    
    # Connect to Notion once
    notion = Client(auth=NOTION_API_KEY)
    
    # Sync each date
    results = []
    for target_date in dates_to_sync:
        success = sync_date(target_date, notion)
        results.append((target_date, success))
    
    # Summary
    print(f"\nSync complete:")
    for d, success in results:
        status = "✓" if success else "✗"
        print(f"  {status} {d}")


if __name__ == "__main__":
    main()
