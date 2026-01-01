# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "notion-client",
#     "python-dotenv",
# ]
# ///

"""
ActivityWatch -> Notion sync script.
Aggregates hourly activity data and pushes it to Notion as toggle blocks.

Supports:
- Timezone-aware journal days (TARGET_TZ env var)
- Sliding window: updates today + yesterday (if within FREEZE_HOURS)
- CLI flags: --yesterday, --date YYYY-MM-DD for manual backfills
- Deduplication of events per source to prevent double-counting
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timedelta, time, date
from zoneinfo import ZoneInfo
from collections import defaultdict
from urllib.parse import urlparse
import re
from notion_client import Client
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

NOTION_API_KEY = os.getenv("NOTION_TIME_ACCOUNTANT_SECRET")
NOTION_DATASOURCE_ID = os.getenv("NOTION_TIME_ACCOUNTING_DATASOURCE_ID")
AW_DATA_DIR = os.getenv("AW_DATA_DIR", os.path.join(current_dir, "aw-data"))

# Journal timezone: the timezone used for defining "a day" in Notion
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "Asia/Singapore")) # or "America/New_York"

# How many hours into "today" we continue to update "yesterday"
FREEZE_HOURS = int(os.getenv("FREEZE_HOURS", "2"))

# Apps considered as "coding tools" (terminals, IDEs, editors)
CODING_APPS = {
    # Terminals
    "kitty", "terminal", "iterm2", "alacritty", "warp", "hyper", "wezterm",
    # VS Code variants
    "code", "vscode", "visual studio code", "code - insiders",
    # AI-powered IDEs
    "windsurf", "cursor",
    # Other editors/IDEs
    "vim", "nvim", "neovim", "emacs", "nano",
    "xcode", "android studio", "eclipse", "notepad++",
    # Development tools
    "docker", "postman", "insomnia", "dbeaver", "tableplus", "sequel pro", "pgadmin",
}

# Websites considered as "coding activity"
CODING_SITES = {
    "github.com", "gitlab.com", "bitbucket.org",
    "stackoverflow.com", "stackexchange.com",
    "docs.python.org", "developer.mozilla.org", "devdocs.io",
    "npmjs.com", "pypi.org", "crates.io", "rubygems.org",
    "replit.com", "codepen.io", "codesandbox.io", "jsfiddle.net",
    "figma.com", 
    "aws.amazon.com", "dash.cloudflare.com", "aistudio.google.com",
    "cronitor.io",
    "localhost:3000",
}

# Apps to exclude from activity tracking (system processes, idle indicators)
EXCLUDED_APPS = {
    "loginwindow",      # macOS lock screen / sleep state
    "screensaverengine", # macOS screensaver
    "screeninactivity",  # Idle state indicator
}

# Table header used to identify our stats table
AW_TABLE_HEADER = "Hour"


def load_aw_data_for_journal_day(journal_date: date) -> dict:
    """
    Load ActivityWatch data for a journal day, accounting for timezone differences.
    
    Loads JSON files for both the journal date AND the day before (in file-date terms)
    to ensure we capture events that might be recorded under a different date due to
    timezone differences between the recording machine and TARGET_TZ.
    
    Returns a dict with bucket names as keys and deduplicated, filtered event lists.
    """
    # Define the exact boundaries of this journal day in TARGET_TZ
    day_start = datetime.combine(journal_date, time(0, 0), tzinfo=TARGET_TZ)
    day_end = day_start + timedelta(days=1)
    
    # File dates to load: journal_date and the day before
    # This ensures we capture events even if file naming uses a different timezone
    file_dates = sorted({
        journal_date.strftime("%Y-%m-%d"),
        (journal_date - timedelta(days=1)).strftime("%Y-%m-%d"),
        (journal_date + timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    
    # Track seen events per bucket to deduplicate
    # Key: (bucket_name, timestamp_str) -> event
    seen_events: dict[tuple[str, str], dict] = {}
    
    for file_date in file_dates:
        pattern = os.path.join(AW_DATA_DIR, f"aw_*_{file_date}.json")
        files = glob.glob(pattern)
        
        for filepath in files:
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    for bucket_name, events in data.items():
                        for event in events:
                            ts_str = event.get('timestamp', '')
                            if not ts_str:
                                continue
                            
                            data_fields = event.get('data') or {}
                            key = (
                                bucket_name,
                                ts_str,
                                data_fields.get('app'),
                                data_fields.get('title'),
                                data_fields.get('url'),
                            )
                            if key in seen_events:
                                continue
                            
                            # Parse and filter to journal day boundaries
                            try:
                                dt = parse_timestamp(ts_str)
                                if day_start <= dt < day_end:
                                    seen_events[key] = event
                            except Exception:
                                continue
                                
            except Exception as e:
                print(f"Error loading {filepath}: {e}")
    
    # Group by bucket name
    merged = defaultdict(list)
    for key, event in seen_events.items():
        bucket_name = key[0]
        merged[bucket_name].append(event)
    
    print(f"Loaded {len(seen_events)} unique events for {journal_date} from files: {file_dates}")
    return dict(merged)


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO8601 timestamp to datetime in journal timezone."""
    # Handle various ISO formats
    dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    return dt.astimezone(TARGET_TZ)


def bucket_events_by_hour(events: list) -> dict:
    """Group events by hour (0-23)."""
    hourly = defaultdict(list)
    for event in events:
        try:
            dt = parse_timestamp(event['timestamp'])
            hour = dt.hour
            hourly[hour].append(event)
        except Exception:
            continue
    return dict(hourly)


def aggregate_app_time(events: list) -> dict:
    """Aggregate time by app name from window watcher events, excluding system processes."""
    app_time = defaultdict(float)
    for event in events:
        app = event.get('data', {}).get('app', 'Unknown')
        app_lower = app.lower()
        # Skip excluded apps (loginwindow, screensaver, etc.)
        if app_lower in EXCLUDED_APPS:
            continue
        duration = event.get('duration', 0)
        app_time[app_lower] += duration
    return dict(app_time)


def aggregate_site_time(events: list) -> dict:
    """Aggregate time by domain from web watcher events."""
    site_time = defaultdict(float)
    for event in events:
        url = event.get('data', {}).get('url', '')
        domain = urlparse(url).netloc
        if domain:
            duration = event.get('duration', 0)
            site_time[domain] += duration
    return dict(site_time)


def aggregate_web_app_time(events: list) -> dict:
    app_time = defaultdict(float)
    for event in events:
        bucket = event.get('_bucket', '')
        match = re.search(r"watcher-web-([^_]+)", bucket)
        app = match.group(1) if match else "browser"
        duration = event.get('duration', 0) or 0
        app_time[app.lower()] += duration
    return dict(app_time)


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration, rounded to nearest minute."""
    minutes = round(seconds / 60)
    if minutes == 0:
        return "<1m"
    elif minutes < 60:
        return f"{minutes}m"
    else:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m" if mins else f"{hours}h"


def compute_hourly_stats(all_data: dict) -> dict:
    """
    Compute stats for each hour from merged ActivityWatch data.
    
    Returns dict: {hour: {stats}}
    """
    # Separate buckets by type
    window_events = []
    web_events = []
    
    for bucket_name, events in all_data.items():
        if 'watcher-window' in bucket_name:
            window_events.extend([{**e, '_bucket': bucket_name} for e in events])
        elif 'watcher-web' in bucket_name:
            web_events.extend([{**e, '_bucket': bucket_name} for e in events])
    
    # Bucket by hour
    window_by_hour = bucket_events_by_hour(window_events)
    web_by_hour = bucket_events_by_hour(web_events)
    
    # Get all hours with activity
    all_hours = set(window_by_hour.keys()) | set(web_by_hour.keys())
    
    hourly_stats = {}
    for hour in sorted(all_hours):
        hour_window = window_by_hour.get(hour, [])
        hour_web = web_by_hour.get(hour, [])
        
        # Aggregate app time
        app_time = aggregate_app_time(hour_window)
        
        # Aggregate site time
        site_time = aggregate_site_time(hour_web)
        web_app_time = aggregate_web_app_time(hour_web)
        
        # Top 3 sites
        top_sites = sorted(site_time.items(), key=lambda x: -x[1])[:3]
        
        # Time on Notion (app or web)
        notion_time = app_time.get('notion', 0)
        notion_time += site_time.get('www.notion.so', 0)
        notion_time += site_time.get('notion.so', 0)
        
        # Time on coding tools (apps + coding-related websites)
        coding_time = sum(
            time for app, time in app_time.items()
            if app.lower() in CODING_APPS
        )
        # Add time on coding-related websites
        coding_time += sum(
            time for site, time in site_time.items()
            if any(coding_site in site.lower() for coding_site in CODING_SITES)
        )
        
        # Top 5 apps
        top_apps = sorted(app_time.items(), key=lambda x: -x[1])[:5]
        if (not top_apps) and web_app_time:
            top_apps = sorted(web_app_time.items(), key=lambda x: -x[1])[:5]
        
        # Total active time this hour
        total_app_time = sum(app_time.values())
        total_web_time = sum(site_time.values())
        active_time = total_app_time if total_app_time > 0 else total_web_time
        
        hourly_stats[hour] = {
            'top_sites': top_sites,
            'notion_time': notion_time,
            'coding_time': coding_time,
            'top_apps': top_apps,
            'active_time': active_time,
            'total_app_time': total_app_time,
            'total_web_time': total_web_time,
        }
    
    return hourly_stats


def format_hour_label(hour: int) -> str:
    """Format hour as 12-hour time range."""
    start = datetime(2000, 1, 1, hour)
    end = start + timedelta(hours=1)
    return f"{start.strftime('%I%p').lstrip('0').lower()}-{end.strftime('%I%p').lstrip('0').lower()}"


def compute_daily_summary(hourly_stats: dict) -> dict:
    """Compute daily totals from hourly stats."""
    total_active_time = 0
    total_coding_time = 0
    total_notion_time = 0
    all_apps = defaultdict(float)
    all_sites = defaultdict(float)
    
    for stats in hourly_stats.values():
        total_active_time += stats.get('active_time', stats.get('total_app_time', 0))
        total_coding_time += stats['coding_time']
        total_notion_time += stats['notion_time']
        for app, time in stats['top_apps']:
            all_apps[app] += time
        for site, time in stats['top_sites']:
            all_sites[site] += time
    
    return {
        'total_active_time': total_active_time,
        'total_coding_time': total_coding_time,
        'total_notion_time': total_notion_time,
        'top_apps': sorted(all_apps.items(), key=lambda x: -x[1])[:5],
        'top_sites': sorted(all_sites.items(), key=lambda x: -x[1])[:5],
    }


def make_cell(text: str) -> list:
    """Create a table cell with rich text."""
    return [{"type": "text", "text": {"content": text}}]


def make_table_row(cells: list) -> dict:
    """Create a table_row block."""
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {
            "cells": [make_cell(c) for c in cells]
        }
    }


def build_notion_blocks(hourly_stats: dict) -> list:
    """Build Notion blocks wrapped in a single parent toggle."""
    
    # Compute daily summary
    summary = compute_daily_summary(hourly_stats)
    
    # Build table rows for hourly data
    # Columns: Hour | Active | Coding | Notion | Top Apps | Top Sites
    table_rows = [
        # Header row
        make_table_row(["Hour", "Active", "Coding", "Notion", "Top Apps", "Top Sites"])
    ]
    
    for hour in sorted(hourly_stats.keys()):
        stats = hourly_stats[hour]
        hour_label = format_hour_label(hour)
        
        # Format top apps (limit to top 3 for table)
        top_apps_str = ", ".join(
            f"{app} ({format_duration(t)})" 
            for app, t in stats['top_apps'][:3]
        ) if stats['top_apps'] else "-"
        
        # Format top sites (limit to top 3 for table)
        top_sites_str = ", ".join(
            f"{site} ({format_duration(t)})" 
            for site, t in stats['top_sites'][:3]
        ) if stats['top_sites'] else "-"
        
        table_rows.append(make_table_row([
            hour_label,
            format_duration(stats.get('active_time', 0)) if stats.get('active_time', 0) > 0 else "-",
            format_duration(stats['coding_time']) if stats['coding_time'] > 0 else "-",
            format_duration(stats['notion_time']) if stats['notion_time'] > 0 else "-",
            top_apps_str,
            top_sites_str
        ]))
    
    # Add totals row
    table_rows.append(make_table_row([
        "TOTAL",
        format_duration(summary['total_active_time']),
        format_duration(summary['total_coding_time']),
        format_duration(summary['total_notion_time']),
        ", ".join(f"{a} ({format_duration(t)})" for a, t in summary['top_apps'][:3]) or "-",
        ", ".join(f"{s} ({format_duration(t)})" for s, t in summary['top_sites'][:3]) or "-"
    ]))
    
    # Create table block
    table_block = {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": 6,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows
        }
    }
    
    return [table_block]


def find_and_clear_existing_blocks(notion: Client, page_id: str) -> None:
    """Find and delete existing AW stats table from the page by checking header row."""
    try:
        children = notion.blocks.children.list(block_id=page_id)
        
        for block in children.get('results', []):
            if block.get('type') == 'table':
                # Get the table's children (rows) to check the header
                table_children = notion.blocks.children.list(block_id=block['id'])
                rows = table_children.get('results', [])
                
                if rows:
                    # Check first cell of first row
                    first_row = rows[0]
                    cells = first_row.get('table_row', {}).get('cells', [])
                    if cells:
                        first_cell_text = ''.join(
                            t.get('text', {}).get('content', '') 
                            for t in cells[0]
                        )
                        if first_cell_text == AW_TABLE_HEADER:
                            notion.blocks.delete(block_id=block['id'])
                            print(f"Deleted existing table: {block['id']}")
                            return
                
    except Exception as e:
        print(f"Error finding existing blocks: {e}")


def sync_date(journal_date: date, notion: Client) -> bool:
    """
    Sync ActivityWatch data for a specific journal date to Notion.
    Returns True on success, False on failure.
    """
    date_str = journal_date.strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"Syncing ActivityWatch data for: {date_str} (tz: {TARGET_TZ})")
    
    # Load ActivityWatch data with timezone-aware filtering
    aw_data = load_aw_data_for_journal_day(journal_date)
    if not aw_data:
        print(f"No ActivityWatch data found for {date_str}")
        return False
    
    print(f"Processing {sum(len(v) for v in aw_data.values())} events from {len(aw_data)} buckets")
    
    # Compute hourly stats
    hourly_stats = compute_hourly_stats(aw_data)
    if not hourly_stats:
        print("No hourly stats computed")
        return False
    
    print(f"Computed stats for {len(hourly_stats)} hours: {sorted(hourly_stats.keys())}")
    
    # Build Notion blocks
    blocks = build_notion_blocks(hourly_stats)
    
    try:
        # Find the page for this date
        pages = notion.data_sources.query(
            data_source_id=NOTION_DATASOURCE_ID,
            filter={"property": "Date", "date": {"equals": date_str}}
        ).get("results")
        
        if not pages:
            print(f"No Notion page found for {date_str}")
            return False
        
        page_id = pages[0]["id"]
        print(f"Found page: {page_id}")
        
        # Clear existing AW blocks
        find_and_clear_existing_blocks(notion, page_id)
        
        # Append new blocks
        notion.blocks.children.append(block_id=page_id, children=blocks)
        
        print(f"Success: Updated page with {len(blocks)} blocks for {len(hourly_stats)} hours")
        return True
        
    except Exception as e:
        print(f"Notion Error: {e}")
        import traceback
        traceback.print_exc()
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
        description="Sync ActivityWatch data to Notion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run aw_notion_sync.py              # Sync today (+ yesterday if <2am)
  uv run aw_notion_sync.py --yesterday  # Sync yesterday only
  uv run aw_notion_sync.py --date 2025-12-04  # Backfill specific date
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
    
    print(f"ActivityWatch -> Notion Sync")
    print(f"Journal timezone: {TARGET_TZ}")
    print(f"Current time: {datetime.now(TARGET_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Determine which dates to sync
    dates_to_sync = determine_dates_to_sync(args)
    
    # Connect to Notion once
    notion = Client(auth=NOTION_API_KEY)
    
    # Sync each date
    results = []
    for journal_date in dates_to_sync:
        success = sync_date(journal_date, notion)
        results.append((journal_date, success))
    
    # Summary
    print(f"\n{'='*50}")
    print("Sync complete:")
    for d, success in results:
        status = "✓" if success else "✗"
        print(f"  {status} {d}")


if __name__ == "__main__":
    main()
