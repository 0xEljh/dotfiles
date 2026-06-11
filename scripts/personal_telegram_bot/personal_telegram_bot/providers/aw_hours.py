from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .health import CheckResult

# Repo scripts/ directory, where aw_notion_sync.py and aw-data/ live.
SCRIPTS_DIR = Path(os.environ.get("AW_SCRIPTS_DIR", Path(__file__).resolve().parents[3]))
DEFAULT_AW_DATA_DIR = SCRIPTS_DIR / "aw-data"
DEFAULT_MAX_AGE_HOURS = 26  # > 1 day: laptops sleeping overnight is not an incident
TOP_TOOLS_SHOWN = 3


@dataclass(frozen=True)
class HourReport:
    hour: int
    classification: str
    active_seconds: float
    top_tools: list[tuple[str, float]]


def previous_hour(now: datetime) -> datetime:
    return (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def check_aw_freshness(
    data_dir: Path, max_age_hours: float, now_ts: float | None = None
) -> CheckResult:
    """Health check: the newest aw-data dump must be younger than max_age_hours."""
    now_ts = now_ts if now_ts is not None else datetime.now().timestamp()
    files = list(Path(data_dir).glob("aw_*.json"))
    if not files:
        return CheckResult(name="aw-data", ok=False, detail=f"no aw-data files in {data_dir}")
    newest = max(files, key=lambda f: f.stat().st_mtime)
    age_hours = (now_ts - newest.stat().st_mtime) / 3600
    return CheckResult(
        name="aw-data",
        ok=age_hours <= max_age_hours,
        detail=f"newest push {newest.name}, {age_hours:.1f}h ago",
    )


def build_hour_report(stats: dict, classification: str | None, hour: int) -> HourReport | None:
    if classification is None:
        return None
    tools = stats.get("coding_tools") or stats.get("planning_tools") or {}
    top_tools = sorted(tools.items(), key=lambda x: -x[1])[:TOP_TOOLS_SHOWN]
    return HourReport(
        hour=hour,
        classification=classification,
        active_seconds=stats.get("active_time", 0),
        top_tools=top_tools,
    )


def _aw_module():
    """Import aw_notion_sync from scripts/ to reuse its loading + classification."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import aw_notion_sync

    return aw_notion_sync


def classify_previous_hour(now: datetime) -> HourReport | None:
    """Classify the hour that just ended, or None if unclassified / no data."""
    aw = _aw_module()
    target = previous_hour(now)
    all_data = aw.load_aw_data_for_journal_day(target.date())
    hourly_stats = aw.compute_hourly_stats(all_data)
    stats = hourly_stats.get(target.hour)
    if not stats:
        return None
    classification = aw.determine_hourly_select_value(stats)
    return build_hour_report(stats, classification, target.hour)
