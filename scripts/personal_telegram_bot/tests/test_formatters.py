from datetime import date, datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.formatters import (
    MAX_TASKS_PER_SECTION,
    format_health_alert,
    format_health_summary,
    format_morning_digest,
    format_unit_failure,
)
from personal_telegram_bot.providers.health import CheckResult, Transition
from personal_telegram_bot.providers.notion_todos import Task
from personal_telegram_bot.providers.sleep import SleepSummary


def task(title, status="Not started", start=None, end=None):
    return Task(title=title, status=status, due_start=start, due_end=end, url=None)


TODAY = date(2026, 6, 11)


def test_morning_digest_empty():
    text = format_morning_digest([], [], TODAY)
    assert "Nothing due or overdue." in text


def test_triage_puts_today_before_overdue():
    overdue = [task("Old thing", status="Postponed", start=date(2026, 6, 9))]
    due_today = [task("Ship bot", status="In progress", start=TODAY)]
    text = format_morning_digest(overdue, due_today, TODAY)
    assert "Ship bot" in text and "Old thing" in text
    assert text.index("Ship bot") < text.index("Old thing")  # today comes first
    assert "1 due today" in text
    assert text.index("<blockquote expandable>") < text.index("Old thing")


def test_overdue_only_uses_plain_header():
    text = format_morning_digest([task("Old thing", start=date(2026, 6, 9))], [], TODAY)
    assert "1 overdue" in text
    assert "Old thing" in text
    assert "<blockquote expandable>" not in text


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
    assert "Sleep: 7h33m" in text


def test_board_link_only_when_configured():
    with_board = format_morning_digest(
        [], [task("x", start=TODAY)], TODAY, board_url="https://notion.so/board"
    )
    assert '<a href="https://notion.so/board">' in with_board
    assert "Bread board" in with_board
    without = format_morning_digest([], [task("x", start=TODAY)], TODAY)
    assert "Bread board" not in without


def test_morning_digest_counts_each_group_once():
    overdue = [task(f"o{i}", start=date(2026, 6, 9)) for i in range(2)]
    due_today = [task(f"d{i}", start=TODAY) for i in range(3)]
    text = format_morning_digest(overdue, due_today, TODAY)
    assert "3 due today · 2 overdue" in text


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


def test_health_alert_uses_host_label():
    transitions = [
        Transition(name="disk /", old="ok", new="warning", detail="81.0% used")
    ]

    text = format_health_alert(transitions, host="contents-may-differ")

    assert text.startswith("disk /: warning on contents-may-differ")
    assert "81.0% used" in text


def test_health_failure_is_visible_in_the_first_line():
    text = format_health_alert([
        Transition(name="api.service", old="ok", new="fail", detail="failed")
    ])
    assert text == "api.service: failed on sleeper-service"


def test_mixed_health_batch_leads_with_severity_counts():
    text = format_health_alert([
        Transition(name="disk /", old="warning", new="critical", detail="95% used"),
        Transition(name="api.service", old="ok", new="fail", detail="inactive"),
        Transition(name="web.service", old="fail", new="ok", detail="active"),
    ])
    assert text.splitlines()[0] == "sleeper-service · 1 critical · 1 failed · 1 recovered"
    assert "disk /: critical · 95% used" in text
    assert "api.service: failed · inactive" in text
    assert "web.service: recovered" in text


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
    text = format_unit_failure("kodo-api.service")
    assert "kodo-api.service" in text
    assert "journalctl" in text


def test_unit_failure_leads_with_the_outcome_and_points_to_logs():
    text = format_unit_failure("kodo-api.service")
    assert text.startswith("kodo-api.service failed on sleeper-service")
    assert "journalctl -u kodo-api.service -n 50 --no-pager" in text
    assert len(text.splitlines()) <= 5


def test_morning_digest_keeps_secondary_tasks_expandable():
    overdue = [task("Overdue item", start=date(2026, 6, 9))]
    due_today = [task(f"Today {i}", start=TODAY) for i in range(5)]
    text = format_morning_digest(overdue, due_today, TODAY)
    preview, details = text.split("<blockquote expandable>", 1)
    assert "Today 0" in preview and "Today 2" in preview
    assert "Today 3" not in preview
    assert "Today 3" in details and "Today 4" in details
    assert "Overdue item" not in preview and "Overdue item" in details
    assert "5 due today" in preview and "1 overdue" in preview


def test_recovery_alert_does_not_look_like_a_new_failure():
    text = format_health_alert([
        Transition(name="api.service", old="fail", new="ok", detail="active")
    ])
    assert "recovered" in text.lower()
    assert "⚠" not in text and "🚨" not in text
    assert "was fail" not in text


def test_disk_recovery_keeps_measurements_in_single_and_mixed_alerts():
    recovery = Transition(
        name="disk /", old="critical", new="ok",
        detail="71.0% used, 29.0 GiB available",
    )
    failure = Transition(name="api.service", old="ok", new="fail", detail="inactive")
    for transitions in ([recovery], [recovery, failure]):
        text = format_health_alert(transitions, host="fixture-host")
        assert "recovered" in text
        assert "71.0% used, 29.0 GiB available" in text


def test_long_linked_digest_fits_without_breaking_html():
    from html.parser import HTMLParser

    class BalancedHTML(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []

        def handle_starttag(self, tag, attrs):
            if tag == "blockquote":
                assert tag not in self.stack
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

    tasks = [Task(
        title=f"Task {i} <review>", status="Not started", due_start=TODAY,
        due_end=None, url=f"https://notion.so/{i}?x=" + "a" * 300,
    ) for i in range(30)]
    text = format_morning_digest(tasks[:15], tasks[15:], TODAY, board_url="https://notion.so/board")
    assert len(text) <= 3900
    assert "15 due today · 15 overdue" in text
    assert "more" in text
    assert text.endswith('<a href="https://notion.so/board">Bread board</a>')
    parser = BalancedHTML()
    parser.feed(text)
    assert not parser.stack


def test_large_health_batch_keeps_an_honest_count_and_detail_route():
    transitions = [Transition(
        name=f"api-{i:03}.service", old="ok", new="fail", detail="x" * 120,
    ) for i in range(80)]
    text = format_health_alert(transitions)
    assert len(text) <= 3900
    shown = sum(f"api-{i:03}.service:" in text for i in range(80))
    assert f"{80 - shown} more items not shown" in text
    assert "uv run botctl send health --force --dry-run" in text


def test_full_health_output_remains_available_for_console_inspection():
    results = [CheckResult(f"api-{i:03}.service", False, "x" * 120) for i in range(80)]
    compact = format_health_summary(results)
    full = format_health_summary(results, compact=False)
    assert len(compact) <= 3900
    assert "api-079.service:" in full
    assert "not shown" not in full


def test_requested_health_summary_still_leads_with_severity():
    text = format_health_summary([
        CheckResult("disk /", False, "97% used", severity="critical"),
        CheckResult("api.service", True, "active"),
    ], host="qa-host")
    assert text.splitlines()[0] == "qa-host · 1 critical · 1 ok"
    assert "disk /: critical · 97% used" in text
