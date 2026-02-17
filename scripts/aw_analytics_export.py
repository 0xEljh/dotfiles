# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
ActivityWatch Analytics Export

Generates aggregated analytics at three granularity levels:
- Daily (last 30 days)
- Weekly (last 4 weeks)
- Monthly (last 1 month)

Outputs a single JSON file with:
- Dev tooling usage breakdown (proportions)
- AI chat usage breakdown
- Planning vs Development ratio
- Top apps and total time

Usage:
  uv run aw_analytics_export.py
  uv run aw_analytics_export.py --output ~/digital-garden/data/aw_analytics.json
"""

import os
import sys
import json
import glob
import argparse
import re
from datetime import datetime, timedelta, time, date
from zoneinfo import ZoneInfo
from collections import defaultdict
from urllib.parse import urlparse
from pathlib import Path

current_dir = os.path.dirname(os.path.abspath(__file__))

# Configuration
AW_DATA_DIR = os.getenv("AW_DATA_DIR", os.path.join(current_dir, "aw-data"))
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "Asia/Singapore"))
DEFAULT_OUTPUT_DIR = Path.home() / "digital-garden" / "data"

# ============================================================================
# Constants (shared with aw_notion_sync.py)
# ============================================================================

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
    "hx ": "Helix",
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

AI_CHAT_SITES = {
    "chatgpt.com",
    "chat.openai.com",
    "claude.ai",
    "gemini.google.com",
    "bard.google.com",
    "aistudio.google.com",
    "grok.com",
    "perplexity.ai",
    "www.perplexity.ai",
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
    # Editors/IDEs
    "code",
    "vscode",
    "visual studio code",
    "code - insiders",
    "windsurf",
    "cursor",
    "vim",
    "nvim",
    "neovim",
    "emacs",
    "nano",
    "xcode",
    "android studio",
    "eclipse",
    "notepad++",
    # Dev tools
    "docker",
    "postman",
    "insomnia",
    "dbeaver",
    "tableplus",
    "sequel pro",
    "pgadmin",
}

# Websites considered as dev tools (browser-based development environments)
DEV_TOOL_SITES = {
    "colab.research.google.com": "Google Colab",
}

# Cross-platform: macOS, Linux, Windows
EXCLUDED_APPS = {
    # macOS
    "loginwindow",
    "screensaverengine",
    "screeninactivity",
    # Windows
    "explorer",
    "searchhost",
    "shellexperiencehost",
    "lockapp",
    "systemsettings",
}

# ============================================================================
# Data Loading
# ============================================================================


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO8601 timestamp to datetime in journal timezone."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return dt.astimezone(TARGET_TZ)


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


def normalize_app_name(app: str) -> str:
    """
    Normalize app name for cross-platform consistency.
    - Strips .exe suffix (Windows)
    - Converts to lowercase
    """
    app = app.lower()
    if app.endswith(".exe"):
        app = app[:-4]
    return app


def load_aw_data_for_date_range(start_date: date, end_date: date) -> dict[date, dict]:
    """
    Load ActivityWatch data for a date range.
    Returns: {date: {bucket_name: [events]}}
    """
    data_by_date: dict[date, dict] = defaultdict(lambda: defaultdict(list))

    # Generate list of file dates to load (with buffer for timezone issues)
    current = start_date - timedelta(days=1)
    end = end_date + timedelta(days=1)
    file_dates = set()
    while current <= end:
        file_dates.add(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    # Load all matching files
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
                            try:
                                dt = parse_timestamp(ts_str)
                                event_date = dt.date()
                                if start_date <= event_date <= end_date:
                                    data_by_date[event_date][bucket_name].append(event)
                            except Exception:
                                continue
            except Exception as e:
                print(f"Error loading {filepath}: {e}")

    return dict(data_by_date)


# ============================================================================
# Aggregation Functions
# ============================================================================


def detect_terminal_tool(title: str) -> str | None:
    """Detect coding tool from terminal window title."""
    title_lower = title.lower()
    for pattern, tool_name in TERMINAL_TOOL_PATTERNS.items():
        if pattern in title_lower:
            return tool_name
    return None


def get_ai_chat_name(domain: str) -> str | None:
    """Get friendly name for AI chat site."""
    domain_lower = domain.lower()
    for ai_site in AI_CHAT_SITES:
        if ai_site in domain_lower or domain_lower.endswith(ai_site):
            if "chatgpt" in ai_site or "openai" in ai_site:
                return "ChatGPT"
            elif "claude" in ai_site:
                return "Claude"
            elif "gemini" in ai_site or "bard" in ai_site:
                return "Gemini"
            elif "grok" in ai_site:
                return "Grok"
            elif "perplexity" in ai_site:
                return "Perplexity"
            elif "aistudio" in ai_site:
                return "AI Studio"
            elif "t3.chat" in ai_site:
                return "T3"
            elif "copilot" in ai_site:
                return "Copilot"
            elif "phind" in ai_site:
                return "Phind"
            elif "poe" in ai_site:
                return "Poe"
            return ai_site.split(".")[0].title()
    return None


def aggregate_day_data(day_data: dict) -> dict:
    """
    Aggregate a single day's ActivityWatch data.

    Returns dict with:
    - dev_tools: {tool_name: seconds}
    - ai_chats: {site_name: seconds}
    - planning_apps: {app_name: seconds}
    - top_apps: {app_name: seconds}
    - totals: {dev_time, planning_time, ai_chat_time, active_time}
    """
    dev_tools = defaultdict(float)
    ai_chats = defaultdict(float)
    planning_apps = defaultdict(float)
    all_apps = defaultdict(float)

    # Collect AFK events from all AFK buckets (per host)
    afk_events_by_host = defaultdict(list)
    for bucket_name, events in day_data.items():
        if "watcher-afk" in bucket_name:
            host = extract_host_from_bucket(bucket_name)
            if host:
                afk_events_by_host[host].extend(events)

    not_afk_periods_by_host = build_not_afk_periods_by_host(afk_events_by_host)

    # Collect and filter window events
    window_events = []
    for bucket_name, events in day_data.items():
        if "watcher-window" in bucket_name:
            window_events.extend([{**e, "_bucket": bucket_name} for e in events])

    window_events = filter_events_by_afk(window_events, not_afk_periods_by_host)

    # Process filtered window events
    for event in window_events:
        app_raw = event.get("data", {}).get("app", "")
        app = normalize_app_name(app_raw)
        title = event.get("data", {}).get("title", "")
        duration = event.get("duration", 0) or 0

        if duration <= 0 or app in EXCLUDED_APPS:
            continue

        # Track all apps
        all_apps[app] += duration

        # Dev tools detection
        if app in TERMINAL_APPS:
            detected_tool = detect_terminal_tool(title)
            if detected_tool:
                dev_tools[detected_tool] += duration
            else:
                dev_tools["Terminal/Shell"] += duration
        elif app in CODING_APPS:
            display_name = app.title()
            if app == "code":
                display_name = "VS Code"
            elif app == "nvim":
                display_name = "Neovim"
            dev_tools[display_name] += duration

        # Planning apps detection
        if app in PLANNING_APPS:
            display_name = app.title()
            planning_apps[display_name] += duration

        # AI Chat desktop apps detection
        if app in AI_CHAT_APPS:
            if app == "claude":
                display_name = "Claude"
            elif app == "chatgpt":
                display_name = "ChatGPT"
            else:
                display_name = app.title()
            ai_chats[display_name] += duration
            # AI chats also count as planning
            planning_apps[display_name] += duration

    # Collect and filter web events
    web_events = []
    for bucket_name, events in day_data.items():
        if "watcher-web" in bucket_name:
            web_events.extend([{**e, "_bucket": bucket_name} for e in events])

    web_events = filter_events_by_afk(web_events, not_afk_periods_by_host)

    # Process filtered web events for AI chat and dev tool sites
    for event in web_events:
        url = event.get("data", {}).get("url", "")
        domain = urlparse(url).netloc
        duration = event.get("duration", 0) or 0

        if duration <= 0:
            continue

        ai_name = get_ai_chat_name(domain)
        if ai_name:
            ai_chats[ai_name] += duration
            # AI chats also count as planning
            planning_apps[ai_name] += duration

        # Check for browser-based dev tools
        for site, display_name in DEV_TOOL_SITES.items():
            if site in domain or domain.endswith(site):
                dev_tools[display_name] += duration
                break

    # Calculate totals
    dev_time = sum(dev_tools.values())
    planning_time = sum(planning_apps.values())
    ai_chat_time = sum(ai_chats.values())
    active_time = sum(all_apps.values())

    return {
        "dev_tools": dict(dev_tools),
        "ai_chats": dict(ai_chats),
        "planning_apps": dict(planning_apps),
        "top_apps": dict(all_apps),
        "totals": {
            "dev_time": dev_time,
            "planning_time": planning_time,
            "ai_chat_time": ai_chat_time,
            "active_time": active_time,
        },
    }


def merge_aggregates(aggregates: list[dict]) -> dict:
    """Merge multiple day aggregates into one."""
    merged = {
        "dev_tools": defaultdict(float),
        "ai_chats": defaultdict(float),
        "planning_apps": defaultdict(float),
        "top_apps": defaultdict(float),
        "totals": {
            "dev_time": 0,
            "planning_time": 0,
            "ai_chat_time": 0,
            "active_time": 0,
        },
    }

    for agg in aggregates:
        for tool, seconds in agg.get("dev_tools", {}).items():
            merged["dev_tools"][tool] += seconds
        for chat, seconds in agg.get("ai_chats", {}).items():
            merged["ai_chats"][chat] += seconds
        for app, seconds in agg.get("planning_apps", {}).items():
            merged["planning_apps"][app] += seconds
        for app, seconds in agg.get("top_apps", {}).items():
            merged["top_apps"][app] += seconds
        for key in merged["totals"]:
            merged["totals"][key] += agg.get("totals", {}).get(key, 0)

    return {
        "dev_tools": dict(merged["dev_tools"]),
        "ai_chats": dict(merged["ai_chats"]),
        "planning_apps": dict(merged["planning_apps"]),
        "top_apps": dict(merged["top_apps"]),
        "totals": merged["totals"],
    }


# ============================================================================
# Report Generation
# ============================================================================


def calculate_proportions(time_dict: dict) -> list[dict]:
    """Calculate proportions for a time breakdown dict."""
    total = sum(time_dict.values())
    if total == 0:
        return []

    result = []
    for name, seconds in sorted(time_dict.items(), key=lambda x: -x[1]):
        result.append(
            {
                "name": name,
                "seconds": round(seconds, 1),
                "minutes": round(seconds / 60, 1),
                "hours": round(seconds / 3600, 2),
                "proportion": round(seconds / total, 4),
                "percentage": round(100 * seconds / total, 1),
            }
        )
    return result


def generate_report(
    aggregate: dict,
    period_type: str,
    period_label: str,
    start_date: date,
    end_date: date,
) -> dict:
    """Generate a structured report from an aggregate."""
    totals = aggregate["totals"]

    # Calculate planning vs dev ratio
    dev_time = totals["dev_time"]
    planning_time = totals["planning_time"]
    total_focused = dev_time + planning_time

    if total_focused > 0:
        dev_ratio = dev_time / total_focused
        planning_ratio = planning_time / total_focused
    else:
        dev_ratio = 0
        planning_ratio = 0

    return {
        "period": {
            "type": period_type,
            "label": period_label,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "summary": {
            "total_active_time": {
                "seconds": round(totals["active_time"], 1),
                "minutes": round(totals["active_time"] / 60, 1),
                "hours": round(totals["active_time"] / 3600, 2),
            },
            "dev_time": {
                "seconds": round(dev_time, 1),
                "minutes": round(dev_time / 60, 1),
                "hours": round(dev_time / 3600, 2),
            },
            "planning_time": {
                "seconds": round(planning_time, 1),
                "minutes": round(planning_time / 60, 1),
                "hours": round(planning_time / 3600, 2),
            },
            "ai_chat_time": {
                "seconds": round(totals["ai_chat_time"], 1),
                "minutes": round(totals["ai_chat_time"] / 60, 1),
                "hours": round(totals["ai_chat_time"] / 3600, 2),
            },
            "dev_vs_planning_ratio": {
                "dev": round(dev_ratio, 3),
                "planning": round(planning_ratio, 3),
            },
        },
        "dev_tools_breakdown": calculate_proportions(aggregate["dev_tools"]),
        "ai_chats_breakdown": calculate_proportions(aggregate["ai_chats"]),
        "planning_breakdown": calculate_proportions(aggregate["planning_apps"]),
        "top_apps": calculate_proportions(aggregate["top_apps"])[:10],
    }


def get_week_bounds(d: date) -> tuple[date, date]:
    """Get Monday-Sunday bounds for the week containing date d."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_month_bounds(d: date) -> tuple[date, date]:
    """Get first and last day of the month containing date d."""
    first = d.replace(day=1)
    # Last day: go to next month, subtract 1 day
    if d.month == 12:
        last = d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
    return first, last


def generate_all_reports(lookback_days: int = 30) -> dict:
    """
    Generate all reports for the specified lookback period.

    Returns:
    {
        'generated_at': ISO timestamp,
        'timezone': timezone name,
        'daily': [list of daily reports],
        'weekly': [list of weekly reports],
        'monthly': [list of monthly reports],
    }
    """
    today = datetime.now(TARGET_TZ).date()
    start_date = today - timedelta(days=lookback_days - 1)

    print(f"Loading data from {start_date} to {today}...")

    # Load all data for the period
    all_data = load_aw_data_for_date_range(start_date, today)
    print(f"Loaded data for {len(all_data)} days")

    # Generate daily aggregates
    daily_aggregates = {}
    for d in sorted(all_data.keys()):
        daily_aggregates[d] = aggregate_day_data(all_data[d])

    # Generate daily reports
    daily_reports = []
    current = start_date
    while current <= today:
        if current in daily_aggregates:
            agg = daily_aggregates[current]
        else:
            agg = aggregate_day_data({})  # Empty day

        report = generate_report(
            agg,
            period_type="daily",
            period_label=current.strftime("%Y-%m-%d"),
            start_date=current,
            end_date=current,
        )
        daily_reports.append(report)
        current += timedelta(days=1)

    # Generate weekly reports (last 4 complete weeks + current partial week)
    weekly_reports = []
    seen_weeks = set()
    current = today
    weeks_generated = 0

    while weeks_generated < 5 and current >= start_date:
        week_start, week_end = get_week_bounds(current)
        week_key = week_start.isoformat()

        if week_key not in seen_weeks:
            seen_weeks.add(week_key)

            # Clamp to our data range
            effective_start = max(week_start, start_date)
            effective_end = min(week_end, today)

            # Gather daily aggregates for this week
            week_aggs = []
            d = effective_start
            while d <= effective_end:
                if d in daily_aggregates:
                    week_aggs.append(daily_aggregates[d])
                d += timedelta(days=1)

            if week_aggs:
                merged = merge_aggregates(week_aggs)
                report = generate_report(
                    merged,
                    period_type="weekly",
                    period_label=f"Week of {week_start.strftime('%Y-%m-%d')}",
                    start_date=effective_start,
                    end_date=effective_end,
                )
                weekly_reports.append(report)

            weeks_generated += 1

        current -= timedelta(days=7)

    # Sort weekly reports by start date
    weekly_reports.sort(key=lambda r: r["period"]["start_date"])

    # Generate monthly report (current month with available data)
    month_start, month_end = get_month_bounds(today)
    effective_start = max(month_start, start_date)
    effective_end = min(month_end, today)

    month_aggs = []
    d = effective_start
    while d <= effective_end:
        if d in daily_aggregates:
            month_aggs.append(daily_aggregates[d])
        d += timedelta(days=1)

    monthly_reports = []
    if month_aggs:
        merged = merge_aggregates(month_aggs)
        report = generate_report(
            merged,
            period_type="monthly",
            period_label=today.strftime("%Y-%m"),
            start_date=effective_start,
            end_date=effective_end,
        )
        monthly_reports.append(report)

    return {
        "generated_at": datetime.now(TARGET_TZ).isoformat(),
        "timezone": str(TARGET_TZ),
        "lookback_days": lookback_days,
        "daily": daily_reports,
        "weekly": weekly_reports,
        "monthly": monthly_reports,
    }


# ============================================================================
# Main
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Export ActivityWatch analytics as JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run aw_analytics_export.py
  uv run aw_analytics_export.py --output ~/digital-garden/data/aw_analytics.json
  uv run aw_analytics_export.py --days 14
        """,
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help=f"Output JSON file path (default: {DEFAULT_OUTPUT_DIR}/aw_analytics.json)",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=30,
        help="Number of days to look back (default: 30)",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output"
    )
    args = parser.parse_args()

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = DEFAULT_OUTPUT_DIR / "aw_analytics.json"

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"ActivityWatch Analytics Export")
    print(f"Timezone: {TARGET_TZ}")
    print(f"Lookback: {args.days} days")
    print(f"Output: {output_path}")
    print()

    # Generate reports
    reports = generate_all_reports(lookback_days=args.days)

    # Write JSON
    indent = 2 if args.pretty else None
    with open(output_path, "w") as f:
        json.dump(reports, f, indent=indent)

    # Summary
    print()
    print(f"Generated reports:")
    print(f"  Daily:   {len(reports['daily'])} reports")
    print(f"  Weekly:  {len(reports['weekly'])} reports")
    print(f"  Monthly: {len(reports['monthly'])} reports")
    print(f"\nSaved to: {output_path}")

    # Quick stats from monthly report
    if reports["monthly"]:
        m = reports["monthly"][0]
        print(f"\n--- This Month Summary ({m['period']['label']}) ---")
        print(f"Total active time: {m['summary']['total_active_time']['hours']:.1f}h")
        print(
            f"Dev time: {m['summary']['dev_time']['hours']:.1f}h ({m['summary']['dev_vs_planning_ratio']['dev'] * 100:.0f}%)"
        )
        print(
            f"Planning time: {m['summary']['planning_time']['hours']:.1f}h ({m['summary']['dev_vs_planning_ratio']['planning'] * 100:.0f}%)"
        )
        print(f"AI chat time: {m['summary']['ai_chat_time']['hours']:.1f}h")

        if m["dev_tools_breakdown"]:
            print(f"\nTop dev tools:")
            for t in m["dev_tools_breakdown"][:5]:
                print(f"  {t['name']}: {t['hours']:.1f}h ({t['percentage']:.0f}%)")


if __name__ == "__main__":
    main()
