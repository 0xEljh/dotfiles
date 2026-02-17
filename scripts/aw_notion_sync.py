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
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "Asia/Singapore"))  # or "America/New_York"

# How many hours into "today" we continue to update "yesterday"
FREEZE_HOURS = int(os.getenv("FREEZE_HOURS", "2"))

# Apps considered as "coding tools" (terminals, IDEs, editors)
# Cross-platform: Linux, macOS, Windows
CODING_APPS = {
    # Terminals (Linux/macOS)
    "kitty",
    "terminal",
    "iterm2",
    "alacritty",
    "warp",
    "hyper",
    "wezterm",
    # Terminals (Windows)
    "windowsterminal",
    "powershell",
    "pwsh",
    "cmd",
    "conhost",
    # VS Code variants
    "code",
    "vscode",
    "visual studio code",
    "code - insiders",
    # AI-powered IDEs
    "windsurf",
    "cursor",
    # Other editors/IDEs
    "vim",
    "nvim",
    "neovim",
    "emacs",
    "nano",
    "xcode",
    "android studio",
    "eclipse",
    "notepad++",
    # Development tools
    "docker",
    "postman",
    "insomnia",
    "dbeaver",
    "tableplus",
    "sequel pro",
    "pgadmin",
}

# Terminal apps - need title inspection to determine actual tool
# Includes cross-platform variants (Linux/macOS/Windows)
TERMINAL_APPS = {
    # Linux/macOS
    "kitty",
    "terminal",
    "iterm2",
    "alacritty",
    "warp",
    "hyper",
    "wezterm",
    "gnome-terminal",
    "konsole",
    "xterm",
    # Windows
    "windowsterminal",
    "powershell",
    "pwsh",
    "cmd",
    "conhost",
    "windows terminal",
}

# Patterns in terminal window titles that indicate specific coding tools
# Maps pattern (lowercase) -> tool display name
# Order matters: more specific patterns should come first
TERMINAL_TOOL_PATTERNS = {
    # Claude Code TUI patterns (unicode spinners and modified indicators)
    "✳ ": "Claude Code",  # Modified indicator (asterisk-like)
    "⠐ ": "Claude Code",  # Braille dot spinner (running state)
    "⠂ ": "Claude Code",  # Braille dot spinner (alternate state)
    "claude code": "Claude Code",  # Explicit Claude Code mention
    # OpenCode patterns
    "opencode": "OpenCode",
    "oc |": "OpenCode",  # OpenCode short prefix (pipe separator)
    "oc:": "OpenCode",  # OpenCode short prefix (colon separator)
    "| opencode": "OpenCode",  # OpenCode suffix pattern (e.g., "Config | OpenCode")
    # Editors
    "nvim": "Neovim",
    "neovim": "Neovim",
    "vim": "Vim",
    "hx ": "Helix",  # helix editor
    "helix": "Helix",
    # Git/Docker TUIs
    "lazygit": "LazyGit",
    "lazydocker": "LazyDocker",
    # System monitors
    "htop": "htop",
    "btop": "btop",
    # AI coding assistants (CLI/TUI)
    "aider": "Aider",
    "gemini-cli": "Gemini CLI",
    "goose": "Goose",
    # SSH/Remote patterns
    "ssh ": "SSH",
    "kitten ssh": "SSH",
    "[ssh:": "SSH",  # VS Code/Windsurf remote indicator
}

# AI Chat websites - for tracking AI assistant usage
AI_CHAT_SITES = {
    # OpenAI
    "chatgpt.com",
    "chat.openai.com",
    # Anthropic
    "claude.ai",
    # Google
    "gemini.google.com",
    "bard.google.com",
    "aistudio.google.com",
    # xAI
    "grok.com",
    # Perplexity
    "perplexity.ai",
    "www.perplexity.ai",
    # Other AI chats
    "t3.chat",
    "poe.com",
    "you.com",
    "phind.com",
    "chat.mistral.ai",
    "huggingface.co/chat",
    "pi.ai",
    "character.ai",
    "copilot.microsoft.com",
}

# AI Chat desktop apps - for tracking AI assistant usage from native apps
AI_CHAT_APPS = {
    "claude",  # Claude desktop app
    "chatgpt",  # ChatGPT desktop app
}

# Planning/architecting apps - note-taking, knowledge management, thinking tools
PLANNING_APPS = {
    "notion",
    "logseq",
    "obsidian",
    "roam",
    "craft",
    "bear",
    "apple notes",
    "notes",
    "evernote",
    "onenote",
    "remnote",
    "anytype",
    "capacities",
    "miro",
    "whimsical",
    "excalidraw",
    "tldraw",
    "figjam",
}

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
}

# Browser-based dev tools with display names for breakdown tracking
DEV_TOOL_SITES = {
    "colab.research.google.com": "Google Colab",
}

# Apps to exclude from activity tracking (system processes, idle indicators)
# Cross-platform: macOS, Linux, Windows
EXCLUDED_APPS = {
    # macOS
    "loginwindow",  # macOS lock screen / sleep state
    "screensaverengine",  # macOS screensaver
    "screeninactivity",  # Idle state indicator
    # Windows
    "explorer",  # Task View, Task Switching, Program Manager
    "searchhost",  # Windows Search
    "shellexperiencehost",  # Windows Shell
    "lockapp",  # Lock screen
    "systemsettings",  # Settings app during idle
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


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO8601 timestamp to datetime in journal timezone."""
    # Handle various ISO formats
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return dt.astimezone(TARGET_TZ)


def normalize_app_name(app: str) -> str:
    """
    Normalize app name for cross-platform consistency.
    - Strips .exe suffix (Windows)
    - Converts to lowercase
    - Handles common app name variations
    """
    app = app.lower()
    # Strip Windows executable extension
    if app.endswith(".exe"):
        app = app[:-4]
    return app


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
        # Check if domain matches any AI chat site
        for ai_site in AI_CHAT_SITES:
            if ai_site in domain or domain.endswith(ai_site):
                # Use a friendly name for the site
                site_name = ai_site.replace("www.", "").split(".")[0]
                if "chatgpt" in ai_site or "openai" in ai_site:
                    site_name = "ChatGPT"
                elif "claude" in ai_site:
                    site_name = "Claude"
                elif "gemini" in ai_site or "bard" in ai_site:
                    site_name = "Gemini"
                elif "grok" in ai_site:
                    site_name = "Grok"
                elif "perplexity" in ai_site:
                    site_name = "Perplexity"
                elif "aistudio" in ai_site:
                    site_name = "AI Studio"
                elif "t3.chat" in ai_site:
                    site_name = "T3"
                elif "copilot" in ai_site:
                    site_name = "Copilot"
                ai_time[site_name] += duration
                break

    # Process window events (desktop AI chat apps)
    if window_events:
        for event in window_events:
            app_raw = event.get("data", {}).get("app", "")
            app = normalize_app_name(app_raw)
            duration = event.get("duration", 0) or 0
            if duration <= 0:
                continue
            if app in AI_CHAT_APPS:
                # Use friendly display name
                if app == "claude":
                    site_name = "Claude"
                elif app == "chatgpt":
                    site_name = "ChatGPT"
                else:
                    site_name = app.title()
                ai_time[site_name] += duration

    return dict(ai_time)


def detect_terminal_tool(title: str) -> str | None:
    """
    Detect which coding tool is being used in a terminal based on window title.
    Returns the tool name or None if not detected.
    """
    title_lower = title.lower()
    for pattern, tool_name in TERMINAL_TOOL_PATTERNS.items():
        if pattern in title_lower:
            return tool_name
    return None


def aggregate_coding_tools_time(
    window_events: list, web_events: list | None = None
) -> dict:
    """
    Aggregate time by coding tool with granular breakdown.
    For terminal apps, inspects window title to determine actual tool.
    Also includes browser-based dev tools (e.g., Google Colab).
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
            # Capitalize nicely for display
            display_name = app.title()
            if app == "code":
                display_name = "VS Code"
            elif app == "nvim":
                display_name = "Neovim"
            tool_time[display_name] += duration

    # Process web events for browser-based dev tools
    if web_events:
        for event in web_events:
            url = event.get("data", {}).get("url", "")
            domain = urlparse(url).netloc.lower()
            duration = event.get("duration", 0) or 0

            if duration <= 0:
                continue

            for site, display_name in DEV_TOOL_SITES.items():
                if site in domain or domain.endswith(site):
                    tool_time[display_name] += duration
                    break

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
    Includes: planning apps (Notion, Logseq, etc.) + AI chat time.
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


def get_event_time_range(event: dict) -> tuple[datetime, datetime] | tuple[None, None]:
    """Get start and end datetime for an event."""
    ts_str = event.get("timestamp", "")
    if not ts_str:
        return None, None
    start = parse_timestamp(ts_str)
    duration = event.get("duration", 0) or 0
    end = start + timedelta(seconds=duration)
    return start, end


def extract_host_from_bucket(bucket_name: str) -> str | None:
    if not bucket_name:
        return None
    match = re.match(r"^aw-watcher-(?:window|afk)_(.+)$", bucket_name)
    if match:
        return match.group(1)
    match = re.match(r"^aw-watcher-web(?:-[^_]+)?_(.+)$", bucket_name)
    if match:
        return match.group(1)
    return None


def merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def build_not_afk_periods_by_host(afk_events_by_host: dict) -> dict:
    periods_by_host = {}
    for host, events in afk_events_by_host.items():
        intervals = []
        for event in events:
            status = event.get("data", {}).get("status", "")
            if status != "not-afk":
                continue
            start, end = get_event_time_range(event)
            if not start or not end or end <= start:
                continue
            intervals.append((start, end))
        periods_by_host[host] = merge_intervals(intervals)
    return periods_by_host


def filter_events_by_afk(events: list, not_afk_periods_by_host: dict) -> list:
    """
    Filter events to only include portions that overlap with 'not-afk' periods.

    Args:
        events: List of window or web events
        not_afk_periods_by_host: Host -> merged not-afk intervals

    Returns:
        List of events with durations adjusted to exclude AFK time
    """
    if not events:
        return []
    if not not_afk_periods_by_host:
        return events

    filtered_events = []

    for event in events:
        bucket_name = event.get("_bucket", "")
        host = extract_host_from_bucket(bucket_name)
        if not host:
            filtered_events.append(event)
            continue

        host_periods = not_afk_periods_by_host.get(host)
        if host_periods is None:
            filtered_events.append(event)
            continue
        if not host_periods:
            continue

        event_start, event_end = get_event_time_range(event)
        if not event_start or not event_end:
            continue

        for active_start, active_end in host_periods:
            overlap_start = max(event_start, active_start)
            overlap_end = min(event_end, active_end)

            if overlap_start < overlap_end:
                filtered_event = event.copy()
                filtered_event["timestamp"] = overlap_start.isoformat()
                filtered_event["duration"] = (
                    overlap_end - overlap_start
                ).total_seconds()
                filtered_events.append(filtered_event)

    return filtered_events


def compute_hourly_stats(all_data: dict) -> dict:
    """
    Compute stats for each hour from merged ActivityWatch data.

    Returns dict: {hour: {stats}}
    """
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
        notion_time = app_time.get("notion", 0)
        notion_time += site_time.get("www.notion.so", 0)
        notion_time += site_time.get("notion.so", 0)

        # Time on coding tools (apps + coding-related websites)
        coding_time = sum(
            time for app, time in app_time.items() if app.lower() in CODING_APPS
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


def sync_date(journal_date: date, notion: Client) -> bool:
    """
    Sync ActivityWatch data for a specific journal date to Notion.
    Returns True on success, False on failure.
    """
    date_str = journal_date.strftime("%Y-%m-%d")
    print(f"\n{'=' * 50}")
    print(f"Syncing ActivityWatch data for: {date_str} (tz: {TARGET_TZ})")

    # Load ActivityWatch data with timezone-aware filtering
    aw_data = load_aw_data_for_journal_day(journal_date)
    if not aw_data:
        print(f"No ActivityWatch data found for {date_str}")
        return False

    print(
        f"Processing {sum(len(v) for v in aw_data.values())} events from {len(aw_data)} buckets"
    )

    # Compute hourly stats
    hourly_stats = compute_hourly_stats(aw_data)
    if not hourly_stats:
        print("No hourly stats computed")
        return False

    print(
        f"Computed stats for {len(hourly_stats)} hours: {sorted(hourly_stats.keys())}"
    )

    # Build Notion blocks
    blocks = build_notion_blocks(hourly_stats)

    try:
        # Find the page for this date
        pages = notion.data_sources.query(
            data_source_id=NOTION_DATASOURCE_ID,
            filter={"property": "Date", "date": {"equals": date_str}},
        ).get("results")

        if not pages:
            print(f"No Notion page found for {date_str}")
            return False

        page_id = pages[0]["id"]
        print(f"Found page: {page_id}")

        # Update hourly select properties based on activity rules
        page_details = notion.pages.retrieve(page_id=page_id)
        page_properties = page_details.get("properties", {})

        hourly_updates = {}
        skipped_hours = []
        updated_hours = []

        for hour, stats in hourly_stats.items():
            prop_name = get_hour_property_name(hour)
            prop_data = page_properties.get(prop_name, {})

            # Check if property exists and is empty (no select value)
            current_select = prop_data.get("select")
            if current_select and current_select.get("name"):
                skipped_hours.append(f"{prop_name} (already: {current_select['name']})")
                continue

            # Determine suggested value
            suggested_value = determine_hourly_select_value(stats)
            if suggested_value:
                hourly_updates[prop_name] = {"select": {"name": suggested_value}}
                updated_hours.append(f"{prop_name}: {suggested_value}")

        # Apply hourly property updates if any
        if hourly_updates:
            notion.pages.update(page_id=page_id, properties=hourly_updates)
            print(f"Updated {len(hourly_updates)} hourly properties:")
            for update_info in updated_hours:
                print(f"  → {update_info}")

        if skipped_hours:
            print(f"Skipped {len(skipped_hours)} already-filled hours")

        # Clear existing AW blocks
        find_and_clear_existing_blocks(notion, page_id)

        # Append new blocks
        notion.blocks.children.append(block_id=page_id, children=blocks)

        print(
            f"Success: Updated page with {len(blocks)} blocks for {len(hourly_stats)} hours"
        )
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
