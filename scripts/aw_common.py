"""
Shared constants and helpers for ActivityWatch sync/export scripts.

Used by `aw_notion_sync.py` and `aw_analytics_export.py`. Pure stdlib only;
not independently executable. Lives next to its consumers so `uv run` (which
prepends the script's directory to `sys.path[0]`) can resolve the import.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

# ============================================================================
# Module-level config
# ============================================================================

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Journal timezone: the timezone used for defining "a day".
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "Asia/Singapore"))

# Where ActivityWatch JSON dumps live (defaults to scripts/aw-data/).
AW_DATA_DIR = os.getenv("AW_DATA_DIR", os.path.join(_THIS_DIR, "aw-data"))


# ============================================================================
# Constants: app/site classification
# ============================================================================

# Apps considered as "coding tools" (terminals, IDEs, editors).
# Cross-platform: Linux, macOS, Windows.
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
    "jupyter",
    "jupyterlab",
    "jupyter lab",
    "jupyter-notebook",
    "jupyter notebook",
    "marimo",
    # Development tools
    "docker",
    "postman",
    "insomnia",
    "dbeaver",
    "tableplus",
    "sequel pro",
    "pgadmin",
}

# Terminal apps - need title inspection to determine actual tool.
# Includes cross-platform variants (Linux/macOS/Windows).
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

# Patterns in terminal window titles that indicate specific coding tools.
# Maps pattern (lowercase) -> tool display name.
# Order matters: more specific patterns should come first.
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

# AI Chat websites - for tracking AI assistant usage.
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

# AI Chat desktop apps - for tracking AI assistant usage from native apps.
AI_CHAT_APPS = {
    "claude",  # Claude desktop app
    "chatgpt",  # ChatGPT desktop app
}

# Planning/architecting apps - note-taking, knowledge management, thinking tools.
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

# Browser-based dev tools with display names for breakdown tracking.
DEV_TOOL_SITES = {
    "colab.research.google.com": "Google Colab",
}

LOCALHOST_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}

JUPYTER_LOCAL_PATH_PREFIXES = (
    "/lab",
    "/notebooks",
    "/tree",
)

# Apps to exclude from activity tracking (system processes, idle indicators).
# Cross-platform: macOS, Linux, Windows.
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


# ============================================================================
# Display-name dispatch tables
# ============================================================================

# Coding-app normalized name -> display name. Falls back to app.title().
CODING_APP_DISPLAY_NAMES: dict[str, str] = {
    "code": "VS Code",
    "vscode": "VS Code",
    "visual studio code": "VS Code",
    "code - insiders": "VS Code",
    "nvim": "Neovim",
    "neovim": "Neovim",
    "jupyter": "Jupyter",
    "jupyterlab": "Jupyter",
    "jupyter lab": "Jupyter",
    "jupyter-notebook": "Jupyter",
    "jupyter notebook": "Jupyter",
    "marimo": "Marimo",
}

# AI-chat display rules: ordered list of (substring, display_name) tuples.
# Order matters - first match wins. More specific entries should come first.
AI_CHAT_DISPLAY_RULES: list[tuple[str, str]] = [
    ("chatgpt", "ChatGPT"),
    ("openai", "ChatGPT"),
    ("claude", "Claude"),
    ("gemini", "Gemini"),
    ("bard", "Gemini"),
    ("grok", "Grok"),
    ("perplexity", "Perplexity"),
    ("aistudio", "AI Studio"),
    ("t3.chat", "T3"),
    ("copilot", "Copilot"),
    ("phind", "Phind"),
    ("poe", "Poe"),
]

# AI chat desktop app -> display name. Falls back to app.title().
AI_CHAT_APP_DISPLAY_NAMES: dict[str, str] = {
    "claude": "Claude",
    "chatgpt": "ChatGPT",
}


def coding_app_display_name(normalized_app: str) -> str:
    """Return the display name for a coding app. Falls back to title-cased app name."""
    return CODING_APP_DISPLAY_NAMES.get(normalized_app, normalized_app.title())


def ai_chat_display_name_from_site(ai_site: str) -> str:
    """Map a known AI chat site to its display name.
    Falls back to the domain head (e.g. 'you' for you.com)."""
    site_low = ai_site.lower()
    for needle, label in AI_CHAT_DISPLAY_RULES:
        if needle in site_low:
            return label
    return ai_site.replace("www.", "").split(".")[0].title()


def ai_chat_app_display_name(normalized_app: str) -> str:
    """Return the display name for an AI chat desktop app."""
    return AI_CHAT_APP_DISPLAY_NAMES.get(normalized_app, normalized_app.title())


def match_ai_chat_site(domain: str) -> str | None:
    """Return the display name if `domain` matches a known AI chat site, else None."""
    d = domain.lower()
    for ai_site in AI_CHAT_SITES:
        if ai_site in d or d.endswith(ai_site):
            return ai_chat_display_name_from_site(ai_site)
    return None


# ============================================================================
# Helpers: time, app names, AFK filtering, web heuristics
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
    """Filter events to only include portions that overlap with 'not-afk' periods."""
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
    """Normalize app name for cross-platform consistency.
    - Strips .exe suffix (Windows)
    - Converts to lowercase
    """
    app = app.lower()
    if app.endswith(".exe"):
        app = app[:-4]
    return app


def detect_terminal_tool(title: str) -> str | None:
    """Detect which coding tool is being used in a terminal based on window title."""
    title_lower = title.lower()
    for pattern, tool_name in TERMINAL_TOOL_PATTERNS.items():
        if pattern in title_lower:
            return tool_name
    return None


def is_domain_or_subdomain(hostname: str, domain: str) -> bool:
    if not hostname:
        return False
    hostname = hostname.lower()
    domain = domain.lower()
    return hostname == domain or hostname.endswith(f".{domain}")


def is_docs_subdomain(hostname: str) -> bool:
    if not hostname:
        return False
    return hostname.startswith("docs.") or ".docs." in hostname


def get_planning_site_name(url: str) -> str | None:
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return None

    if is_domain_or_subdomain(hostname, "github.com") or is_domain_or_subdomain(
        hostname, "github.io"
    ):
        return "GitHub"
    if is_domain_or_subdomain(hostname, "arxiv.org"):
        return "arXiv"
    if is_docs_subdomain(hostname):
        return "Documentation"
    return None


def get_browser_dev_tool_name(url: str, title: str = "") -> str | None:
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").lower()
    path = (parsed_url.path or "").lower()
    title_lower = title.lower()

    for site, display_name in DEV_TOOL_SITES.items():
        if is_domain_or_subdomain(hostname, site):
            return display_name

    if hostname in LOCALHOST_HOSTS:
        if "marimo" in title_lower or "marimo" in path:
            return "Marimo"
        if "jupyterlab" in title_lower or "jupyter notebook" in title_lower:
            return "Jupyter"
        for prefix in JUPYTER_LOCAL_PATH_PREFIXES:
            if path.startswith(prefix):
                return "Jupyter"

    return None
