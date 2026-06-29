import os
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot import cli
from personal_telegram_bot.config import Config
from personal_telegram_bot.db import StateDB
from personal_telegram_bot.providers.aw_hours import (
    HourReport,
    build_hour_report,
    check_aw_freshness,
    stale_aw_reminder_transition,
    stale_aw_reminder_window_key,
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
    assert "🛠 <b>2–3pm · Deep Work</b>" in text  # glyph + bold header
    assert "55m active" in text
    assert "Claude Code 30m" in text
    assert "Neovim 10m" in text


def test_format_hour_report_shallow_glyph():
    report = HourReport(hour=9, classification="Shallow Work", active_seconds=3000, top_tools=[])
    text = format_hour_report(report)
    assert "✍️ <b>9–10am · Shallow Work</b>" in text
    assert "50m active" in text


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


def test_aw_stale_reminder_waits_for_systematic_threshold():
    result = check_aw_freshness("/missing", max_age_hours=26, now_ts=1_781_000_000)
    row = {"status": "fail", "since": "2026-06-20T00:00:00+08:00"}
    now = datetime.fromisoformat("2026-06-20T12:00:00+08:00")

    assert stale_aw_reminder_window_key(row, result, now, systematic_after_hours=24, reminder_every_hours=12) is None
    assert stale_aw_reminder_transition(row, result, now, systematic_after_hours=24) is None


def test_aw_stale_reminder_marks_systematic_failures():
    result = check_aw_freshness("/missing", max_age_hours=26, now_ts=1_781_000_000)
    row = {"status": "fail", "since": "2026-06-20T00:00:00+08:00"}
    now = datetime.fromisoformat("2026-06-21T06:00:00+08:00")

    key = stale_aw_reminder_window_key(row, result, now, systematic_after_hours=24, reminder_every_hours=12)
    transition = stale_aw_reminder_transition(row, result, now, systematic_after_hours=24)

    assert key == "aw-data/2026-06-21/0"
    assert transition is not None
    assert transition.old == "fail"
    assert transition.new == "fail"
    assert "systematic" in transition.detail


def test_send_health_periodically_alerts_systematic_aw_staleness(tmp_path, monkeypatch):
    tz = ZoneInfo("Asia/Singapore")
    db_path = tmp_path / "state.sqlite3"
    db = StateDB(db_path)
    db.set_health_status(
        "aw-data",
        "fail",
        detail="no aw-data files",
        now="2026-06-20T00:00:00+08:00",
    )

    cfg = Config(
        telegram_token="tok",
        default_chat_id=1,
        allowed_user_ids=frozenset(),
        notion_token=None,
        bread_datasource_id=None,
        tz=tz,
        db_path=db_path,
        health_units=[],
        health_urls=[],
        aw_data_dir=tmp_path / "aw-data",
        aw_max_age_hours=26.0,
        aw_systematic_after_hours=24.0,
        aw_stale_reminder_hours=12,
        life_db_path=tmp_path / "life.sqlite3",
        life_ingest_token=None,
        life_ingest_bind="127.0.0.1",
        life_ingest_port=8830,
    )
    monkeypatch.setattr(
        cli,
        "datetime",
        type(
            "FixedDateTime",
            (datetime,),
            {"now": classmethod(lambda cls, tz=None: datetime(2026, 6, 21, 6, 0, tzinfo=tz))},
        ),
    )
    sent = []
    monkeypatch.setattr(cli, "_deliver", lambda cfg, text, dry_run, parse_mode=None: sent.append(text) or 1)

    args = SimpleNamespace(force=False, dry_run=False)
    cli.send_health(cfg, args)
    cli.send_health(cfg, args)

    assert len(sent) == 1
    assert "still failing" in sent[0]
    assert "systematic" in sent[0]
