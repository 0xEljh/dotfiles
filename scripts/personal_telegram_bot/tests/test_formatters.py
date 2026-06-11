from datetime import date

from personal_telegram_bot.formatters import (
    MAX_TASKS_PER_SECTION,
    format_health_alert,
    format_morning_digest,
    format_unit_failure,
)
from personal_telegram_bot.providers.health import Transition
from personal_telegram_bot.providers.notion_todos import Task


def task(title, status="Not started", start=None, end=None):
    return Task(title=title, status=status, due_start=start, due_end=end, url=None)


TODAY = date(2026, 6, 11)


def test_morning_digest_empty():
    text = format_morning_digest([], [], TODAY)
    assert "no tasks" in text.lower()
    assert "overdue" not in text.lower()


def test_morning_digest_sections():
    overdue = [task("Old thing", status="Postponed", start=date(2026, 6, 9))]
    due_today = [task("Ship bot", status="In progress", start=TODAY)]
    text = format_morning_digest(overdue, due_today, TODAY)
    assert "Ship bot" in text
    assert "Old thing" in text
    assert "Overdue" in text
    assert "Due today" in text
    # overdue items show their due date
    assert "2026-06-09" in text


def test_morning_digest_counts_total():
    overdue = [task(f"o{i}") for i in range(2)]
    due_today = [task(f"d{i}") for i in range(3)]
    text = format_morning_digest(overdue, due_today, TODAY)
    assert "5" in text


def test_morning_digest_truncates_long_sections():
    due_today = [task(f"task {i}") for i in range(MAX_TASKS_PER_SECTION + 4)]
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


def test_unit_failure_mentions_unit_and_journalctl():
    text = format_unit_failure("kodo-api.service", journal_tail=None)
    assert "kodo-api.service" in text
    assert "journalctl" in text


def test_unit_failure_includes_journal_tail():
    text = format_unit_failure("kodo-api.service", journal_tail="boom: exit 1")
    assert "boom: exit 1" in text
