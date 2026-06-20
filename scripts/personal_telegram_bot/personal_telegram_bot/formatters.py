from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from typing import Iterable

from .providers.health import CheckResult, Transition
from .providers.notion_todos import Task
from .providers.sleep import SleepSummary, duration_hm

MAX_TASKS_PER_SECTION = 15
MAX_JOURNAL_CHARS = 900


def _task_link(task: Task) -> str:
    """Task title as a Telegram-HTML link to its Notion page (plain if no URL)."""
    title = html.escape(task.title, quote=False)  # text content: only & < > need escaping
    if task.url:
        return f'<a href="{html.escape(task.url, quote=True)}">{title}</a>'
    return title


def _today_line(task: Task) -> str:
    line = f"• {_task_link(task)}"
    if task.status not in ("Not started", "Unknown"):
        line += f" · {html.escape(task.status)}"
    return line


def _overdue_line(task: Task, today: date) -> str:
    line = f"• {_task_link(task)}"
    due = task.effective_due
    if due:
        days = (today - due).days
        line += f" — {days} day{'s' if days != 1 else ''} late ({due.day} {due:%b})"
    return line


def _task_block(header: str, tasks: list[Task], render) -> str:
    lines = [f"<b>{header} ({len(tasks)})</b>"]
    lines.extend(render(task) for task in tasks[:MAX_TASKS_PER_SECTION])
    hidden = len(tasks) - MAX_TASKS_PER_SECTION
    if hidden > 0:
        lines.append(f"…and {hidden} more")
    return "\n".join(lines)


def format_sleep_line(sleep: SleepSummary) -> str:
    window = f"{sleep.start:%H:%M}–{sleep.end:%H:%M}"
    return f"😴 Slept {duration_hm(sleep.duration_seconds)} ({window})"


def format_morning_digest(
    overdue: list[Task],
    due_today: list[Task],
    today: date,
    sleep: SleepSummary | None = None,
    board_url: str | None = None,
) -> str:
    """Triage-style morning digest (Telegram HTML): today's tasks lead, overdue is
    demoted, and sleep + meta form a footer strip. Task titles link to their
    Notion pages. Sent with parse_mode=HTML, so dynamic text is HTML-escaped."""
    blocks = [f"<b>☀️ {today:%a %d %b}</b>"]

    if not overdue and not due_today:
        blocks.append("✅ Clear runway — nothing due or overdue.")
    else:
        if due_today:
            blocks.append(_task_block("📌 Today", due_today, _today_line))
        if overdue:
            header = "🔴 Also overdue" if due_today else "🔴 Overdue"
            blocks.append(_task_block(header, overdue, lambda t: _overdue_line(t, today)))

    footer: list[str] = []
    if sleep:
        footer.append(f"😴 {duration_hm(sleep.duration_seconds)}")
    total = len(overdue) + len(due_today)
    if total:
        footer.append(f"{total} open")
    if board_url:
        footer.append(f'<a href="{html.escape(board_url, quote=True)}">Bread board</a>')
    if footer:
        blocks.append("— — —\n" + " · ".join(footer))

    return "\n\n".join(blocks)


def format_health_alert(transitions: list[Transition]) -> str:
    lines = ["⚠️ sleeper-service health"]
    for t in transitions:
        if t.new == "ok":
            lines.append(f"✅ {t.name}: recovered ({t.detail})")
        else:
            was = f", was {t.old}" if t.old else ""
            lines.append(f"❌ {t.name}: {t.detail}{was}")
    return "\n".join(lines)


def format_health_summary(results: Iterable[CheckResult]) -> str:
    lines = ["sleeper-service status"]
    for r in results:
        mark = "✅" if r.ok else "❌"
        lines.append(f"{mark} {r.name}: {r.detail}")
    return "\n".join(lines)


def _hour_label(hour: int) -> str:
    start = datetime(2000, 1, 1, hour)
    end = start + timedelta(hours=1)
    sm, em = start.strftime("%p").lower(), end.strftime("%p").lower()
    sh, eh = start.strftime("%I").lstrip("0"), end.strftime("%I").lstrip("0")
    # Collapse the meridiem when both ends share it: "2–3pm" vs "11am–12pm".
    return f"{sh}–{eh}{em}" if sm == em else f"{sh}{sm}–{eh}{em}"


def _minutes(seconds: float) -> str:
    return f"{round(seconds / 60)}m"


# Per-classification glyph for the hourly report header.
CLASSIFICATION_EMOJI = {"Deep Work": "🛠", "Shallow Work": "✍️"}


def format_hour_report(report) -> str:
    """Hourly activity report (Telegram HTML): a bold classified header + the top
    tools that earned it."""
    emoji = CLASSIFICATION_EMOJI.get(report.classification, "🕐")
    header = f"{emoji} <b>{_hour_label(report.hour)} · {html.escape(report.classification)}</b>"
    tools = " · ".join(
        f"{html.escape(name)} {_minutes(secs)}" for name, secs in report.top_tools
    )
    body = f"{_minutes(report.active_seconds)} active" + (f" · {tools}" if tools else "")
    return f"{header}\n{body}"


def format_unit_failure(unit: str, journal_tail: str | None) -> str:
    lines = [
        f"🚨 sleeper-service: {unit} entered failed state.",
        f"Inspect with: journalctl -u {unit} -n 50",
    ]
    if journal_tail:
        lines.append("")
        lines.append(journal_tail[-MAX_JOURNAL_CHARS:])
    return "\n".join(lines)
