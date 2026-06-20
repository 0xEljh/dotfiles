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
import re
from datetime import datetime, timedelta, time, date
from collections import defaultdict
from urllib.parse import urlparse
from notion_client import Client
from dotenv import load_dotenv

from notion_day import Contribution, PRIORITY_BIO, PRIORITY_WORK, write_day_page

current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

# Import shared module AFTER load_dotenv so TARGET_TZ/AW_DATA_DIR see env values.
from aw_common import (  # noqa: E402
    AI_CHAT_APPS,
    AW_DATA_DIR,
    CODING_APPS,
    EXCLUDED_APPS,
    PLANNING_APPS,
    TARGET_TZ,
    TERMINAL_APPS,
    ai_chat_app_display_name,
    botctl_summary,
    build_not_afk_periods_by_host,
    coding_app_display_name,
    detect_terminal_tool,
    extract_host_from_bucket,
    fetch_phone_hours,
    filter_events_by_afk,
    get_browser_dev_tool_name,
    get_planning_site_name,
    match_ai_chat_site,
    normalize_app_name,
    parse_timestamp,
    phone_app_category,
)

NOTION_API_KEY = os.getenv("NOTION_TIME_ACCOUNTANT_SECRET")
NOTION_DATASOURCE_ID = os.getenv("NOTION_TIME_ACCOUNTING_DATASOURCE_ID")

# How many hours into "today" we continue to update "yesterday"
FREEZE_HOURS = int(os.getenv("FREEZE_HOURS", "2"))

# Sleep enrichment: the telegram bot owns the sleep reducer (life_events.sqlite3
# + pairing logic), so we shell out to its CLI per date rather than re-deriving
# sleep here. Single source of truth; best-effort so AW sync never depends on it.
SLEEP_HOURS_PROPERTY = "Sleep Hours"  # number property — add it to the database once
BIO_HOUR_VALUE = "bio"  # hourly select option for sleep (Notion auto-creates it)

# Websites considered as "coding activity"
CODING_SITES = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "stackoverflow.com",
    "stackexchange.com",
    "docs.python.org",
    "developer.mozilla.org",
    "devdocs.io",
    "npmjs.com",
    "pypi.org",
    "crates.io",
    "rubygems.org",
    "replit.com",
    "codepen.io",
    "codesandbox.io",
    "jsfiddle.net",
    "figma.com",
    "aws.amazon.com",
    "dash.cloudflare.com",
    "cronitor.io",
    "localhost:3000",
    "vercel.com",
    "netlify.com",
    "railway.app",
    "render.com",
    "huggingface.co",
    "colab.research.google.com",
    "sleeper-service.tail82ff8b.ts.net",
}

# Table header used to identify our stats table
AW_TABLE_HEADER = "Hour"

# Hourly select thresholds (in seconds)
DEEP_WORK_DEV_TOOLS_THRESHOLD = 30 * 60  # 30 minutes
DEEP_WORK_ACTIVE_TIME_THRESHOLD = 50 * 60  # 50 minutes
SHALLOW_WORK_PLANNING_THRESHOLD = 30 * 60  # 30 minutes
SHALLOW_WORK_ACTIVE_TIME_THRESHOLD = 50 * 60  # 50 minutes


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
    file_dates = sorted(
        {
            journal_date.strftime("%Y-%m-%d"),
            (journal_date - timedelta(days=1)).strftime("%Y-%m-%d"),
            (journal_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
    )

    # Track seen events per bucket to deduplicate
    # Key: (bucket_name, timestamp_str) -> event
    seen_events: dict[tuple[str, str], dict] = {}

    for file_date in file_dates:
        pattern = os.path.join(AW_DATA_DIR, f"aw_*_{file_date}.json")
        files = glob.glob(pattern)

        for filepath in files:
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    for bucket_name, events in data.items():
                        for event in events:
                            ts_str = event.get("timestamp", "")
                            if not ts_str:
                                continue

                            data_fields = event.get("data") or {}
                            key = (
                                bucket_name,
                                ts_str,
                                data_fields.get("app"),
                                data_fields.get("title"),
                                data_fields.get("url"),
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

    print(
        f"Loaded {len(seen_events)} unique events for {journal_date} from files: {file_dates}"
    )
    return dict(merged)


def bucket_events_by_hour(events: list) -> dict:
    """
    Group events by hour (0-23), splitting events that span multiple hours.

    Events with duration exceeding their start hour are split proportionally
    across each hour they span. Each hour's duration is capped at 3600 seconds.
    """
    hourly = defaultdict(list)
    for event in events:
        try:
            dt = parse_timestamp(event["timestamp"])
            duration = event.get("duration", 0) or 0

            if duration <= 0:
                # Zero-duration events go to their start hour
                hourly[dt.hour].append(event)
                continue

            # Calculate how much time remains in the start hour
            seconds_into_hour = dt.minute * 60 + dt.second + dt.microsecond / 1_000_000
            remaining_in_hour = 3600 - seconds_into_hour

            if duration <= remaining_in_hour:
                # Event fits entirely within its start hour
                hourly[dt.hour].append(event)
            else:
                # Event spans multiple hours - split it
                current_hour = dt.hour
                remaining_duration = duration
                first_chunk = min(remaining_in_hour, remaining_duration)

                # First partial hour
                if first_chunk > 0:
                    split_event = {**event, "duration": first_chunk}
                    hourly[current_hour].append(split_event)
                    remaining_duration -= first_chunk

                # Full hours in between
                current_hour = (current_hour + 1) % 24
                while remaining_duration > 3600:
                    split_event = {**event, "duration": 3600}
                    hourly[current_hour].append(split_event)
                    remaining_duration -= 3600
                    current_hour = (current_hour + 1) % 24

                # Final partial hour
                if remaining_duration > 0:
                    split_event = {**event, "duration": remaining_duration}
                    hourly[current_hour].append(split_event)

        except Exception:
            continue
    return dict(hourly)


def aggregate_app_time(events: list) -> dict:
    """Aggregate time by app name from window watcher events, excluding system processes."""
    app_time = defaultdict(float)
    for event in events:
        app_raw = event.get("data", {}).get("app", "Unknown")
        app = normalize_app_name(app_raw)
        # Skip excluded apps (loginwindow, screensaver, etc.)
        if app in EXCLUDED_APPS:
            continue
        duration = event.get("duration", 0)
        app_time[app] += duration
    return dict(app_time)


def aggregate_site_time(events: list) -> dict:
    """Aggregate time by domain from web watcher events."""
    site_time = defaultdict(float)
    for event in events:
        url = event.get("data", {}).get("url", "")
        domain = urlparse(url).netloc
        if domain:
            duration = event.get("duration", 0)
            site_time[domain] += duration
    return dict(site_time)


def aggregate_web_app_time(events: list) -> dict:
    app_time = defaultdict(float)
    for event in events:
        bucket = event.get("_bucket", "")
        match = re.search(r"watcher-web-([^_]+)", bucket)
        app = match.group(1) if match else "browser"
        duration = event.get("duration", 0) or 0
        app_time[app.lower()] += duration
    return dict(app_time)


def aggregate_ai_chat_time(web_events: list, window_events: list | None = None) -> dict:
    """
    Aggregate time spent on AI chat from web events and desktop apps.
    Returns dict: {site_name: seconds} for AI chat with non-zero time.
    """
    ai_time = defaultdict(float)

    # Process web events (browser-based AI chat)
    for event in web_events:
        url = event.get("data", {}).get("url", "")
        domain = urlparse(url).netloc.lower()
        duration = event.get("duration", 0) or 0
        if duration <= 0:
            continue
        site_name = match_ai_chat_site(domain)
        if site_name:
            ai_time[site_name] += duration

    # Process window events (desktop AI chat apps)
    if window_events:
        for event in window_events:
            app_raw = event.get("data", {}).get("app", "")
            app = normalize_app_name(app_raw)
            duration = event.get("duration", 0) or 0
            if duration <= 0:
                continue
            if app in AI_CHAT_APPS:
                ai_time[ai_chat_app_display_name(app)] += duration

    return dict(ai_time)


def aggregate_coding_tools_time(
    window_events: list, web_events: list | None = None
) -> dict:
    """
    Aggregate time by coding tool with granular breakdown.
    For terminal apps, inspects window title to determine actual tool.
    Also includes browser-based dev tools (e.g., Google Colab, local notebooks).
    Returns dict: {tool_name: seconds}
    """
    tool_time = defaultdict(float)

    for event in window_events:
        app_raw = event.get("data", {}).get("app", "")
        app = normalize_app_name(app_raw)
        title = event.get("data", {}).get("title", "")
        duration = event.get("duration", 0) or 0

        if duration <= 0:
            continue

        # Skip excluded apps
        if app in EXCLUDED_APPS:
            continue

        # Check if this is a terminal app - inspect title for specific tool
        if app in TERMINAL_APPS:
            detected_tool = detect_terminal_tool(title)
            if detected_tool:
                tool_time[detected_tool] += duration
            else:
                # Generic terminal usage (shell, etc.)
                tool_time["Terminal/Shell"] += duration
        # Check if this is a known coding app (IDE, editor)
        elif app in CODING_APPS:
            tool_time[coding_app_display_name(app)] += duration

    # Process web events for browser-based dev tools
    if web_events:
        for event in web_events:
            url = event.get("data", {}).get("url", "")
            title = event.get("data", {}).get("title", "")
            duration = event.get("duration", 0) or 0

            if duration <= 0:
                continue

            dev_tool_name = get_browser_dev_tool_name(url, title)
            if dev_tool_name:
                tool_time[dev_tool_name] += duration

    return dict(tool_time)


def count_ai_chat_minutes(ai_time: dict) -> int:
    """
    Count number of distinct minutes with AI chat activity.
    Returns count of minutes where total AI chat time >= 30 seconds.
    """
    total_seconds = sum(ai_time.values())
    return max(0, round(total_seconds / 60))


def aggregate_planning_time(
    window_events: list, web_events: list, ai_chat_time: dict
) -> dict:
    """
    Aggregate time spent on planning/architecting tools.
    Includes: planning apps + planning/design web activity + AI chat time.
    Returns dict: {tool_name: seconds}
    """
    planning_time = defaultdict(float)

    # Add planning apps from window events
    for event in window_events:
        app_raw = event.get("data", {}).get("app", "")
        app = normalize_app_name(app_raw)
        duration = event.get("duration", 0) or 0
        if duration <= 0:
            continue
        if app in PLANNING_APPS:
            display_name = app.title()
            planning_time[display_name] += duration

    # Add planning/design websites from web events
    for event in web_events:
        url = event.get("data", {}).get("url", "")
        duration = event.get("duration", 0) or 0
        if duration <= 0:
            continue

        planning_site_name = get_planning_site_name(url)
        if planning_site_name:
            planning_time[planning_site_name] += duration

    # Add AI chat time (already aggregated by site)
    for site, seconds in ai_chat_time.items():
        planning_time[site] += seconds

    return dict(planning_time)


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


def compute_hourly_stats(all_data: dict, phone_hours: dict | None = None) -> dict:
    """
    Compute stats for each hour from merged ActivityWatch data.

    phone_hours (optional): {hour: {app: seconds}} of phone foreground use, merged
    in as activity so a phone-only hour is no longer empty.

    Returns dict: {hour: {stats}}
    """
    phone_hours = phone_hours or {}
    # Separate buckets by type
    window_events = []
    web_events = []
    afk_events_by_host = defaultdict(list)

    for bucket_name, events in all_data.items():
        if "watcher-window" in bucket_name:
            window_events.extend([{**e, "_bucket": bucket_name} for e in events])
        elif "watcher-web" in bucket_name:
            web_events.extend([{**e, "_bucket": bucket_name} for e in events])
        elif "watcher-afk" in bucket_name:
            host = extract_host_from_bucket(bucket_name)
            if host:
                afk_events_by_host[host].extend(events)

    not_afk_periods_by_host = build_not_afk_periods_by_host(afk_events_by_host)

    # Filter events to only include non-AFK time
    window_events = filter_events_by_afk(window_events, not_afk_periods_by_host)
    web_events = filter_events_by_afk(web_events, not_afk_periods_by_host)

    # Warn if window watcher has no events (likely not running)
    if not window_events and web_events:
        print(
            "⚠️  WARNING: No window watcher events found! Only browser data available."
        )
        print("   - Terminal/IDE activity will NOT be tracked")
        print("   - Check if aw-watcher-window is running on your system")

    # Bucket by hour
    window_by_hour = bucket_events_by_hour(window_events)
    web_by_hour = bucket_events_by_hour(web_events)

    # Get all hours with activity (phone-only hours count too)
    all_hours = set(window_by_hour.keys()) | set(web_by_hour.keys()) | set(phone_hours.keys())

    hourly_stats = {}
    for hour in sorted(all_hours):
        hour_window = window_by_hour.get(hour, [])
        hour_web = web_by_hour.get(hour, [])

        # Aggregate app time
        app_time = aggregate_app_time(hour_window)

        # Merge phone foreground time as activity. Like multi-device desktop
        # time, this can push an hour's active_time past 60 minutes — active_time
        # is device-time, not wall-clock.
        for app, seconds in phone_hours.get(hour, {}).items():
            app_time[app] = app_time.get(app, 0) + seconds

        # Aggregate site time
        site_time = aggregate_site_time(hour_web)
        web_app_time = aggregate_web_app_time(hour_web)

        # Top 3 sites
        top_sites = sorted(site_time.items(), key=lambda x: -x[1])[:3]

        # Time on Notion (app or web)
        notion_time = app_time.get("notion", 0)
        notion_time += site_time.get("www.notion.so", 0)
        notion_time += site_time.get("notion.so", 0)

        # Time on coding tools (apps + coding-related websites).
        # `app_time` keys come from `aggregate_app_time` which uses
        # `normalize_app_name`, so they are already lowercase.
        coding_time = sum(
            time for app, time in app_time.items() if app in CODING_APPS
        )
        # Add time on coding-related websites
        coding_time += sum(
            time
            for site, time in site_time.items()
            if any(coding_site in site.lower() for coding_site in CODING_SITES)
        )

        # AI Chat time - aggregate by site (includes desktop AI chat apps)
        ai_chat_time = aggregate_ai_chat_time(hour_web, hour_window)
        ai_chat_total = sum(ai_chat_time.values())

        # Granular coding tools breakdown (with terminal tool detection + web dev tools)
        coding_tools = aggregate_coding_tools_time(hour_window, hour_web)
        coding_tools_total = sum(coding_tools.values())

        # Planning time (Notion, Logseq, etc. + AI chats)
        planning_tools = aggregate_planning_time(hour_window, hour_web, ai_chat_time)
        planning_total = sum(planning_tools.values())

        # Fold categorised phone apps into the SAME work buckets as desktop tools,
        # so phone dev/planning time counts toward Deep/Shallow Work identically
        # (uncategorised phone apps stay as activity only). Recompute the totals.
        for app, seconds in phone_hours.get(hour, {}).items():
            category = phone_app_category(app)
            if category is None:
                continue
            bucket = coding_tools if category == "coding" else planning_tools
            bucket[app] = bucket.get(app, 0) + seconds
        coding_tools_total = sum(coding_tools.values())
        planning_total = sum(planning_tools.values())

        # Top 5 apps
        top_apps = sorted(app_time.items(), key=lambda x: -x[1])[:5]
        if (not top_apps) and web_app_time:
            top_apps = sorted(web_app_time.items(), key=lambda x: -x[1])[:5]

        # Total active time this hour
        total_app_time = sum(app_time.values())
        total_web_time = sum(site_time.values())
        active_time = total_app_time if total_app_time > 0 else total_web_time

        hourly_stats[hour] = {
            "top_sites": top_sites,
            "notion_time": notion_time,
            "coding_time": coding_time,
            "top_apps": top_apps,
            "active_time": active_time,
            "total_app_time": total_app_time,
            "total_web_time": total_web_time,
            "ai_chat_time": ai_chat_time,
            "ai_chat_total": ai_chat_total,
            "coding_tools": coding_tools,
            "coding_tools_total": coding_tools_total,
            "planning_tools": planning_tools,
            "planning_total": planning_total,
        }

    return hourly_stats


def get_hour_property_name(hour: int) -> str:
    """Format hour as HH:00 for Notion property name (00:00 to 23:00)."""
    return f"{hour:02d}:00"


def determine_hourly_select_value(hour_stats: dict) -> str | None:
    """
    Determine the suggested select value for an hour based on activity data.

    Returns:
    - "Deep Work" if dev tooling > 30min and active time > 50min
    - "Shallow Work" if planning tools > 30min and active time > 50min
    - None if no rules match or active time is insufficient

    Deep Work takes precedence if both conditions are met.
    """
    active_time = hour_stats.get("active_time", 0)

    # Check if we meet the active time threshold
    if active_time < DEEP_WORK_ACTIVE_TIME_THRESHOLD:
        return None

    dev_tools_time = hour_stats.get("coding_tools_total", 0)
    planning_time = hour_stats.get("planning_total", 0)

    # Check Deep Work first (takes precedence)
    if dev_tools_time >= DEEP_WORK_DEV_TOOLS_THRESHOLD:
        return "Deep Work"

    # Check Shallow Work
    if planning_time >= SHALLOW_WORK_PLANNING_THRESHOLD:
        return "Shallow Work"

    return None


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
    total_ai_chat_time = 0
    total_coding_tools_time = 0
    total_planning_time = 0
    all_apps = defaultdict(float)
    all_sites = defaultdict(float)
    all_ai_chats = defaultdict(float)
    all_coding_tools = defaultdict(float)
    all_planning_tools = defaultdict(float)

    for stats in hourly_stats.values():
        total_active_time += stats.get("active_time", stats.get("total_app_time", 0))
        total_coding_time += stats["coding_time"]
        total_notion_time += stats["notion_time"]
        total_ai_chat_time += stats.get("ai_chat_total", 0)
        total_coding_tools_time += stats.get("coding_tools_total", 0)
        total_planning_time += stats.get("planning_total", 0)
        for app, time in stats["top_apps"]:
            all_apps[app] += time
        for site, time in stats["top_sites"]:
            all_sites[site] += time
        for ai_site, time in stats.get("ai_chat_time", {}).items():
            all_ai_chats[ai_site] += time
        for tool, time in stats.get("coding_tools", {}).items():
            all_coding_tools[tool] += time
        for tool, time in stats.get("planning_tools", {}).items():
            all_planning_tools[tool] += time

    return {
        "total_active_time": total_active_time,
        "total_coding_time": total_coding_time,
        "total_notion_time": total_notion_time,
        "total_ai_chat_time": total_ai_chat_time,
        "total_coding_tools_time": total_coding_tools_time,
        "total_planning_time": total_planning_time,
        "top_apps": sorted(all_apps.items(), key=lambda x: -x[1])[:5],
        "top_sites": sorted(all_sites.items(), key=lambda x: -x[1])[:5],
        "ai_chats": sorted(all_ai_chats.items(), key=lambda x: -x[1]),
        "coding_tools": sorted(all_coding_tools.items(), key=lambda x: -x[1]),
        "planning_tools": sorted(all_planning_tools.items(), key=lambda x: -x[1]),
    }


def make_cell(text: str) -> list:
    """Create a table cell with rich text."""
    return [{"type": "text", "text": {"content": text}}]


def make_table_row(cells: list) -> dict:
    """Create a table_row block."""
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {"cells": [make_cell(c) for c in cells]},
    }


def format_tools_with_total(
    tools: dict, total_seconds: float, max_items: int = 3
) -> str:
    """
    Format tools time with total in brackets: [45m] Tool1: 15m, Tool2: 10m
    """
    total_mins = round(total_seconds / 60)
    if total_mins == 0:
        return "-"

    # Sort by time descending
    sorted_tools = sorted(tools.items(), key=lambda x: -x[1])[:max_items]
    parts = []
    for tool, seconds in sorted_tools:
        mins = round(seconds / 60)
        if mins > 0:
            parts.append(f"{tool}: {mins}m")

    breakdown = ", ".join(parts) if parts else ""
    if breakdown:
        return f"[{total_mins}m] {breakdown}"
    return f"[{total_mins}m]"


def build_notion_blocks(hourly_stats: dict) -> list:
    """Build Notion blocks wrapped in a single parent toggle."""

    # Compute daily summary
    summary = compute_daily_summary(hourly_stats)

    # Build table rows for hourly data
    # Columns: Hour | Active | Dev Tools | Planning | Top Apps | Top Sites
    table_rows = [
        # Header row
        make_table_row(
            ["Hour", "Active", "Dev Tools", "Planning", "Top Apps", "Top Sites"]
        )
    ]

    for hour in sorted(hourly_stats.keys()):
        stats = hourly_stats[hour]
        hour_label = format_hour_label(hour)

        # Format dev tools with total: [45m] Windsurf: 15m, OpenCode: 10m
        dev_tools_str = format_tools_with_total(
            stats.get("coding_tools", {}), stats.get("coding_tools_total", 0)
        )

        # Format planning tools with total: [30m] Notion: 20m, ChatGPT: 10m
        planning_str = format_tools_with_total(
            stats.get("planning_tools", {}), stats.get("planning_total", 0)
        )

        # Format top apps (limit to top 3 for table)
        top_apps_str = (
            ", ".join(
                f"{app} ({format_duration(t)})" for app, t in stats["top_apps"][:3]
            )
            if stats["top_apps"]
            else "-"
        )

        # Format top sites (limit to top 3 for table)
        top_sites_str = (
            ", ".join(
                f"{site} ({format_duration(t)})" for site, t in stats["top_sites"][:3]
            )
            if stats["top_sites"]
            else "-"
        )

        table_rows.append(
            make_table_row(
                [
                    hour_label,
                    format_duration(stats.get("active_time", 0))
                    if stats.get("active_time", 0) > 0
                    else "-",
                    dev_tools_str,
                    planning_str,
                    top_apps_str,
                    top_sites_str,
                ]
            )
        )

    # Add totals row
    dev_tools_total_str = format_tools_with_total(
        dict(summary.get("coding_tools", [])), summary.get("total_coding_tools_time", 0)
    )
    planning_total_str = format_tools_with_total(
        dict(summary.get("planning_tools", [])), summary.get("total_planning_time", 0)
    )

    table_rows.append(
        make_table_row(
            [
                "TOTAL",
                format_duration(summary["total_active_time"]),
                dev_tools_total_str,
                planning_total_str,
                ", ".join(
                    f"{a} ({format_duration(t)})" for a, t in summary["top_apps"][:3]
                )
                or "-",
                ", ".join(
                    f"{s} ({format_duration(t)})" for s, t in summary["top_sites"][:3]
                )
                or "-",
            ]
        )
    )

    # Create table block
    table_block = {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": 6,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        },
    }

    return [table_block]


def find_and_clear_existing_blocks(notion: Client, page_id: str) -> None:
    """Find and delete existing AW stats table from the page by checking header row."""
    try:
        children = notion.blocks.children.list(block_id=page_id)

        for block in children.get("results", []):
            if block.get("type") == "table":
                # Get the table's children (rows) to check the header
                table_children = notion.blocks.children.list(block_id=block["id"])
                rows = table_children.get("results", [])

                if rows:
                    # Check first cell of first row
                    first_row = rows[0]
                    cells = first_row.get("table_row", {}).get("cells", [])
                    if cells:
                        first_cell_text = "".join(
                            t.get("text", {}).get("content", "") for t in cells[0]
                        )
                        if first_cell_text == AW_TABLE_HEADER:
                            notion.blocks.delete(block_id=block["id"])
                            print(f"Deleted existing table: {block['id']}")
                            return

    except Exception as e:
        print(f"Error finding existing blocks: {e}")


def find_or_create_time_accounting_page(notion: Client, date_str: str) -> str:
    """Return the Time Accounting page ID for date_str, creating it when missing."""
    pages = notion.data_sources.query(
        data_source_id=NOTION_DATASOURCE_ID,
        filter={"property": "Date", "date": {"equals": date_str}},
    ).get("results")

    if pages:
        page_id = pages[0]["id"]
        print(f"Found page: {page_id}")
        return page_id

    created_page = notion.pages.create(
        parent={"data_source_id": NOTION_DATASOURCE_ID},
        properties={
            # Key by the stable property ID "title" so renames of the title
            # column in Notion (currently named "") cannot break creation.
            "title": {"title": [{"text": {"content": date_str}}]},
            "Date": {"date": {"start": date_str}},
        },
    )
    page_id = created_page["id"]
    print(f"Created Time Accounting page for {date_str}: {page_id}")
    return page_id


def fetch_sleep_summary(date_str: str) -> dict | None:
    """Sleep summary for date_str. Shape: {"sleep": {...,"duration_hours":7.55}
    | None, "sleeping_hours": [0,1,...]}."""
    return botctl_summary("sleep-summary", date_str)


def build_activity_contribution(date_str: str, existing_props: dict) -> Contribution:
    """Desktop ActivityWatch: hourly Deep/Shallow-Work classification + the stats
    table. On a day with no AW data this returns an EMPTY contribution rather than
    aborting the whole page — other signals (sleep, …) still write."""
    journal_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    aw_data = load_aw_data_for_journal_day(journal_date)
    phone_hours = fetch_phone_hours(date_str)
    if not aw_data and not phone_hours:
        print(f"No desktop or phone activity for {date_str}")
        return Contribution.empty()

    if aw_data:
        print(
            f"Processing {sum(len(v) for v in aw_data.values())} events from {len(aw_data)} buckets"
        )
    hourly_stats = compute_hourly_stats(aw_data, phone_hours=phone_hours)
    if not hourly_stats:
        print("No hourly stats computed")
        return Contribution.empty()

    print(f"Computed stats for {len(hourly_stats)} hours: {sorted(hourly_stats.keys())}")
    hour_tags: dict[int, tuple[str, int]] = {}
    for hour, stats in hourly_stats.items():
        value = determine_hourly_select_value(stats)
        if value:
            hour_tags[hour] = (value, PRIORITY_WORK)
    return Contribution(hour_tags=hour_tags, blocks=build_notion_blocks(hourly_stats))


def build_sleep_contribution(date_str: str, existing_props: dict) -> Contribution:
    """Sleep overlay: tag the asleep clock-hours "bio" and write the headline
    `Sleep Hours` number. Sourced from the bot CLI (single source of the sleep
    reducer); best-effort, so a missing/failed summary just yields nothing."""
    summary = fetch_sleep_summary(date_str)
    if not summary:
        return Contribution.empty()
    hour_tags = {
        hour: (BIO_HOUR_VALUE, PRIORITY_BIO)
        for hour in summary.get("sleeping_hours", [])
    }
    number_props: dict[str, float] = {}
    sleep_record = summary.get("sleep")
    if sleep_record and sleep_record.get("duration_hours") is not None:
        number_props[SLEEP_HOURS_PROPERTY] = sleep_record["duration_hours"]
    return Contribution(hour_tags=hour_tags, number_props=number_props)


def _replace_aw_blocks(notion: Client, page_id: str, blocks: list) -> None:
    """Swap the page's existing AW stats table for freshly rendered blocks."""
    find_and_clear_existing_blocks(notion, page_id)
    notion.blocks.children.append(block_id=page_id, children=blocks)


def sync_date(journal_date: date, notion: Client) -> bool:
    """Sync the Notion day page for a journal date from all available signals.
    Returns True if the page was synced (even with no AW data), False only if
    the page itself could not be ensured."""
    date_str = journal_date.strftime("%Y-%m-%d")
    print(f"\n{'=' * 50}")
    print(f"Syncing day page for: {date_str} (tz: {TARGET_TZ})")

    try:
        write_day_page(
            notion,
            date_str,
            [build_activity_contribution, build_sleep_contribution],
            ensure_page=find_or_create_time_accounting_page,
            replace_blocks=_replace_aw_blocks,
        )
    except Exception as e:
        print(f"Notion Error: {e}")
        import traceback

        traceback.print_exc()
        return False

    print(f"Success: synced day page for {date_str}")
    return True


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
        print(
            f"Within freeze window ({now.hour}h < {FREEZE_HOURS}h), including yesterday"
        )
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
        """,
    )
    parser.add_argument(
        "--yesterday",
        "-y",
        action="store_true",
        help="Sync yesterday only (ignores freeze rule)",
    )
    parser.add_argument(
        "--date",
        "-d",
        type=str,
        metavar="YYYY-MM-DD",
        help="Sync a specific date (manual backfill)",
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
    print(f"\n{'=' * 50}")
    print("Sync complete:")
    for d, success in results:
        status = "✓" if success else "✗"
        print(f"  {status} {d}")


if __name__ == "__main__":
    main()
