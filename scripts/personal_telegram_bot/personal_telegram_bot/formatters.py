from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from .providers.health import CheckResult, Transition
from .providers.notion_todos import Task
from .providers.sleep import SleepSummary, duration_hm

MAX_TASKS_PER_SECTION = 15
MAX_JOURNAL_CHARS = 900


def _task_line(task: Task, today: date) -> str:
    parts = [f"• {task.title}"]
    annotations = []
    if task.effective_due and task.effective_due < today:
        annotations.append(f"due {task.effective_due.isoformat()}")
    if task.status not in ("Not started", "Unknown"):
        annotations.append(task.status)
    if annotations:
        parts.append(f"({', '.join(annotations)})")
    return " ".join(parts)


def _section(header: str, tasks: list[Task], today: date) -> list[str]:
    lines = [f"{header}:"]
    for task in tasks[:MAX_TASKS_PER_SECTION]:
        lines.append(_task_line(task, today))
    hidden = len(tasks) - MAX_TASKS_PER_SECTION
    if hidden > 0:
        lines.append(f"…and {hidden} more")
    return lines


def format_sleep_line(sleep: SleepSummary) -> str:
    window = f"{sleep.start:%H:%M}–{sleep.end:%H:%M}"
    return f"😴 Slept {duration_hm(sleep.duration_seconds)} ({window})"


def format_morning_digest(
    overdue: list[Task],
    due_today: list[Task],
    today: date,
    sleep: SleepSummary | None = None,
) -> str:
    header = f"Good morning ☀️ {today.strftime('%a %d %b')}"
    if sleep:
        header = f"{header}\n{format_sleep_line(sleep)}"
    if not overdue and not due_today:
        return f"{header}\n\nNo tasks due today and nothing outstanding. Clear runway."

    blocks = [header]
    if due_today:
        blocks.append("\n".join(_section("Due today", due_today, today)))
    if overdue:
        blocks.append("\n".join(_section("Overdue", overdue, today)))
    total = len(overdue) + len(due_today)
    blocks.append(f"{total} open task{'s' if total != 1 else ''}.")
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
    fmt = lambda d: d.strftime("%I%p").lstrip("0").lower()
    return f"{fmt(start)}-{fmt(end)}"


def _minutes(seconds: float) -> str:
    return f"{round(seconds / 60)}m"


def format_hour_report(report) -> str:
    tools = ", ".join(f"{name} {_minutes(secs)}" for name, secs in report.top_tools)
    lines = [
        f"🕐 {_hour_label(report.hour)}: {report.classification}",
        f"{_minutes(report.active_seconds)} active" + (f" · {tools}" if tools else ""),
    ]
    return "\n".join(lines)


def format_unit_failure(unit: str, journal_tail: str | None) -> str:
    lines = [
        f"🚨 sleeper-service: {unit} entered failed state.",
        f"Inspect with: journalctl -u {unit} -n 50",
    ]
    if journal_tail:
        lines.append("")
        lines.append(journal_tail[-MAX_JOURNAL_CHARS:])
    return "\n".join(lines)
