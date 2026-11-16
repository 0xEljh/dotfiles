import os
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

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


def test_build_hour_report_shallow_ignores_incidental_coding_tools():
    s = stats(coding=800, planning=2520)
    s["coding_tools"] = {"Claude Code": 780, "Terminal/Shell": 20}
    s["planning_tools"] = {"Notion": 2520}

    report = build_hour_report(s, "Shallow Work", hour=13)

    assert report.top_tools == [("Notion", 2520)]


def test_format_hour_report():
    report = HourReport(
        hour=14,
        classification="Deep Work",
        active_seconds=3300,
        top_tools=[("Claude Code", 1800), ("Neovim", 600)],
    )
    text = format_hour_report(report)
    assert "<b>2–3pm · Deep Work</b>" in text
    assert "55m active" in text
    assert "Claude Code 30m" in text
    assert "Neovim 10m" in text


def test_format_hour_report_shallow_header():
    report = HourReport(hour=9, classification="Shallow Work", active_seconds=3000, top_tools=[])
    text = format_hour_report(report)
    assert "<b>9–10am · Shallow Work</b>" in text
    assert "50m active" in text


def test_deep_work_report_keeps_ai_usage_outside_the_top_tool_cutoff():
    s = stats(tools={"OpenCode": 1200, "Neovim": 900, "GitHub": 300})
    s["ai_chat_time"] = {"ChatGPT": 720, "Claude": 120}
    s["planning_tools"] = dict(s["ai_chat_time"])

    text = format_hour_report(build_hour_report(s, "Deep Work", hour=16))

    assert "4–5pm · Deep Work" in text
    assert "OpenCode 20m" in text
    assert "Neovim 15m" in text
    assert "GitHub 5m" in text
    assert "AI chat: ChatGPT 12m · Claude 2m" in text
    assert "55m active" in text


def test_shallow_report_lists_ai_once_even_below_three_planning_tools():
    s = stats(coding=0, planning=3000)
    s["planning_tools"] = {
        "Notion": 1000, "Logseq": 900, "Obsidian": 800, "ChatGPT": 300,
    }
    s["ai_chat_time"] = {"ChatGPT": 300}

    text = format_hour_report(build_hour_report(s, "Shallow Work", hour=9))

    assert "Shallow Work" in text
    assert "AI chat: ChatGPT 5m" in text
    assert text.count("ChatGPT") == 1
    assert "Notion" in text and "Logseq" in text and "Obsidian" in text

    s["planning_tools"] = {"ChatGPT": 2400, "Notion": 600, "Logseq": 500, "Obsidian": 400}
    s["ai_chat_time"] = {"ChatGPT": 2400}
    text = format_hour_report(build_hour_report(s, "Shallow Work", hour=9))
    assert text.count("ChatGPT") == 1
    assert "AI chat: ChatGPT 40m" in text
    assert "Notion 10m" in text
    assert "Logseq" in text and "Obsidian" in text


def test_unclassified_ai_usage_is_reported_without_claiming_work():
    s = stats(active=2400, coding=0, planning=720)
    s["ai_chat_time"] = {"ChatGPT": 720}

    text = format_hour_report(build_hour_report(s, None, hour=15))

    assert "3–4pm · Activity" in text
    assert "40m active" in text
    assert "AI chat: ChatGPT 12m" in text
    assert "Deep Work" not in text and "Shallow Work" not in text


def test_brief_ai_usage_is_visible_and_html_escaped():
    s = stats(active=15, coding=0, planning=15)
    s["ai_chat_time"] = {"ChatGPT <test> & co": 15, "Zero": 0, "Negative": -10}

    text = format_hour_report(build_hour_report(s, None, hour=23, ai_report_min_seconds=0))

    assert "<b>11pm–12am · Activity</b>" in text
    assert "&lt;1m active" in text
    assert "AI chat: ChatGPT &lt;test&gt; &amp; co &lt;1m" in text
    assert "0m" not in text
    assert "Zero" not in text and "Negative" not in text


def test_unclassified_without_positive_ai_usage_stays_silent():
    s = stats(active=600)
    s["ai_chat_time"] = {"ChatGPT": 0, "Claude": -1}
    assert build_hour_report(s, None, hour=14) is None


def test_ai_only_reports_require_one_minute_by_default():
    s = stats(active=60, coding=0, planning=60)
    s["ai_chat_time"] = {"ChatGPT": 59.999999}
    assert build_hour_report(s, None, hour=14) is None

    s["ai_chat_time"] = {"ChatGPT": 40, "Claude": 20}
    text = format_hour_report(build_hour_report(s, None, hour=14))
    assert "Activity" in text
    assert "ChatGPT &lt;1m" in text and "Claude &lt;1m" in text


@pytest.mark.parametrize("durations,minimum", [([5.9] * 10 + [1], 60), ([6.01] * 10, 60.1)])
def test_fractional_ai_durations_meeting_minimum_are_reported(durations, minimum):
    s = stats()
    s["ai_chat_time"] = {"ChatGPT": 0.0}
    for duration in durations:
        s["ai_chat_time"]["ChatGPT"] += duration

    report = build_hour_report(s, None, hour=14, ai_report_min_seconds=minimum)

    assert report is not None
    assert "AI chat: ChatGPT 1m" in format_hour_report(report)


def test_classified_hours_always_show_brief_ai_usage():
    s = stats()
    s["ai_chat_time"] = {"ChatGPT": 15}
    text = format_hour_report(build_hour_report(s, "Deep Work", hour=14))
    assert "Deep Work" in text and "AI chat: ChatGPT &lt;1m" in text


def test_ai_report_minimum_is_configurable():
    env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_DEFAULT_CHAT_ID": "1"}
    assert Config.from_env(env).aw_ai_report_min_seconds == 60
    assert Config.from_env({**env, "AW_AI_REPORT_MIN_SECONDS": "0"}).aw_ai_report_min_seconds == 0
    s = stats()
    s["ai_chat_time"] = {"ChatGPT": 120}
    assert build_hour_report(s, None, hour=14, ai_report_min_seconds=180) is None


@pytest.mark.parametrize("minimum", ["-1", "nan", "inf"])
def test_ai_report_minimum_rejects_invalid_configuration(minimum):
    with pytest.raises(ValueError, match="AW_AI_REPORT_MIN_SECONDS"):
        Config.from_env({
            "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_DEFAULT_CHAT_ID": "1",
            "AW_AI_REPORT_MIN_SECONDS": minimum,
        })


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
