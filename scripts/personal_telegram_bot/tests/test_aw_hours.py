import os
from datetime import datetime

from personal_telegram_bot.providers.aw_hours import (
    HourReport,
    build_hour_report,
    check_aw_freshness,
    previous_hour,
)
from personal_telegram_bot.formatters import format_hour_report


def test_previous_hour_same_day():
    assert previous_hour(datetime(2026, 6, 11, 15, 10)) == datetime(2026, 6, 11, 14, 0)


def test_previous_hour_crosses_midnight():
    assert previous_hour(datetime(2026, 6, 11, 0, 10)) == datetime(2026, 6, 10, 23, 0)


def stats(active=3300, coding=2400, planning=0, tools=None):
    return {
        "active_time": active,
        "coding_tools_total": coding,
        "planning_total": planning,
        "coding_tools": tools or {"Claude Code": 1800, "Neovim": 600},
        "planning_tools": {},
    }


def test_build_hour_report_classified():
    report = build_hour_report(stats(), "Deep Work", hour=14)
    assert report is not None
    assert report.classification == "Deep Work"
    assert report.hour == 14
    assert report.active_seconds == 3300
    assert report.top_tools[0] == ("Claude Code", 1800)


def test_build_hour_report_unclassified_is_none():
    assert build_hour_report(stats(active=600), None, hour=14) is None


def test_build_hour_report_shallow_uses_planning_tools():
    s = stats(coding=0, planning=2100, tools=None)
    s["coding_tools"] = {}
    s["planning_tools"] = {"Notion": 2100}
    report = build_hour_report(s, "Shallow Work", hour=9)
    assert report.top_tools == [("Notion", 2100)]


def test_format_hour_report():
    report = HourReport(
        hour=14,
        classification="Deep Work",
        active_seconds=3300,
        top_tools=[("Claude Code", 1800), ("Neovim", 600)],
    )
    text = format_hour_report(report)
    assert "Deep Work" in text
    assert "2pm-3pm" in text
    assert "55m" in text
    assert "Claude Code" in text


def touch(path, age_hours, now_ts):
    path.write_text("{}")
    ts = now_ts - age_hours * 3600
    os.utime(path, (ts, ts))


def test_aw_freshness_ok(tmp_path):
    now_ts = 1_781_000_000
    touch(tmp_path / "aw_Mac_2026-06-11.json", age_hours=3, now_ts=now_ts)
    result = check_aw_freshness(tmp_path, max_age_hours=26, now_ts=now_ts)
    assert result.ok
    assert "aw_Mac_2026-06-11.json" in result.detail


def test_aw_freshness_stale(tmp_path):
    now_ts = 1_781_000_000
    touch(tmp_path / "aw_Mac_2026-06-09.json", age_hours=40, now_ts=now_ts)
    result = check_aw_freshness(tmp_path, max_age_hours=26, now_ts=now_ts)
    assert not result.ok


def test_aw_freshness_no_data(tmp_path):
    result = check_aw_freshness(tmp_path, max_age_hours=26, now_ts=1_781_000_000)
    assert not result.ok
    assert result.name == "aw-data"
