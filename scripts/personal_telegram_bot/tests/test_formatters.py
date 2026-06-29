from datetime import date, datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.formatters import (
    MAX_TASKS_PER_SECTION,
    format_health_alert,
    format_morning_digest,
    format_unit_failure,
)
from personal_telegram_bot.providers.health import Transition
from personal_telegram_bot.providers.notion_todos import Task
from personal_telegram_bot.providers.sleep import SleepSummary


def task(title, status="Not started", start=None, end=None):
    return Task(title=title, status=status, due_start=start, due_end=end, url=None)


TODAY = date(2026, 6, 11)


def test_morning_digest_empty():
    text = format_morning_digest([], [], TODAY)
    assert "clear runway" in text.lower()


def test_triage_puts_today_before_overdue():
    overdue = [task("Old thing", status="Postponed", start=date(2026, 6, 9))]
    due_today = [task("Ship bot", status="In progress", start=TODAY)]
    text = format_morning_digest(overdue, due_today, TODAY)
    assert "Ship bot" in text and "Old thing" in text
    assert text.index("Ship bot") < text.index("Old thing")  # today comes first
    assert "Today" in text
    assert "Also overdue" in text  # demoted when today tasks exist


def test_overdue_only_uses_plain_header():
    text = format_morning_digest([task("Old thing", start=date(2026, 6, 9))], [], TODAY)
    assert "Overdue" in text
    assert "Also overdue" not in text


def test_overdue_shows_relative_lateness():
    text = format_morning_digest([task("Old", start=date(2026, 6, 9))], [], TODAY)
    assert "2 days late" in text  # TODAY = 6/11
    assert "(9 Jun)" in text


def test_task_links_to_its_notion_page():
    linked = Task(
        title="Linked", status="Not started", due_start=TODAY, due_end=None,
        url="https://notion.so/abc",
    )
    text = format_morning_digest([], [linked], TODAY)
    assert '<a href="https://notion.so/abc">Linked</a>' in text
    assert "↗" not in text  # link styling is enough; no redundant arrow


def test_task_without_url_renders_plain():
    text = format_morning_digest([], [task("NoLink", start=TODAY)], TODAY)
    assert "NoLink" in text
    assert "<a href" not in text


def test_html_special_chars_are_escaped():
    nasty = Task(
        title="Fix <x> & <y>", status="Not started", due_start=TODAY, due_end=None, url=None
    )
    text = format_morning_digest([], [nasty], TODAY)
    assert "&lt;x&gt; &amp; &lt;y&gt;" in text


def test_sleep_appears_in_footer():
    tz = ZoneInfo("Asia/Singapore")
    sleep = SleepSummary(
        start=datetime(2026, 6, 10, 23, 48, tzinfo=tz),
        end=datetime(2026, 6, 11, 7, 21, tzinfo=tz),
    )
    text = format_morning_digest([], [], TODAY, sleep=sleep)
    assert "😴 7h33m" in text


def test_board_link_only_when_configured():
    with_board = format_morning_digest(
        [], [task("x", start=TODAY)], TODAY, board_url="https://notion.so/board"
    )
    assert '<a href="https://notion.so/board">' in with_board
    assert "Bread board" in with_board
    without = format_morning_digest([], [task("x", start=TODAY)], TODAY)
    assert "Bread board" not in without


def test_morning_digest_counts_total_in_footer():
    overdue = [task(f"o{i}", start=date(2026, 6, 9)) for i in range(2)]
    due_today = [task(f"d{i}", start=TODAY) for i in range(3)]
    text = format_morning_digest(overdue, due_today, TODAY)
    assert "5 open" in text


def test_morning_digest_truncates_long_sections():
    due_today = [task(f"task {i}", start=TODAY) for i in range(MAX_TASKS_PER_SECTION + 4)]
    text = format_morning_digest([], due_today, TODAY)
    assert f"task {MAX_TASKS_PER_SECTION - 1}" in text
    assert f"task {MAX_TASKS_PER_SECTION}" not in text
    assert "4 more" in text


def test_health_alert_failure_and_recovery():
    transitions = [
        Transition(name="kodo-api.service", old="ok", new="fail", detail="failed"),
        Transition(name="https://0xeljh.com", old="fail", new="ok", detail="200"),
    ]
    text = format_health_alert(transitions)
    assert "kodo-api.service" in text
    assert "https://0xeljh.com" in text
    assert "recovered" in text.lower()


def test_health_alert_systematic_stale_aw_reminder():
    transitions = [
        Transition(
            name="aw-data",
            old="fail",
            new="fail",
            detail="systematic stale for 30.0h: newest push aw_Mac.json, 40.0h ago",
        )
    ]

    text = format_health_alert(transitions)

    assert "aw-data" in text
    assert "still failing" in text
    assert "systematic" in text


def test_unit_failure_mentions_unit_and_journalctl():
    text = format_unit_failure("kodo-api.service", journal_tail=None)
    assert "kodo-api.service" in text
    assert "journalctl" in text


def test_unit_failure_includes_journal_tail():
    text = format_unit_failure("kodo-api.service", journal_tail="boom: exit 1")
    assert "boom: exit 1" in text
