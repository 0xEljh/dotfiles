from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from typing import Iterable

from .providers.health import CheckResult, Transition
from .providers.notion_todos import Task
from .providers.sleep import SleepSummary, duration_hm
from .tpot.seeds import SeedRow

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
    post_seeds: list[SeedRow] | None = None,
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

    if post_seeds:
        blocks.append(_seed_block("🌱 Still on the table", post_seeds))

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


def format_standdown(
    target_date: date,
    link_url: str | None = None,
    post_seeds: list[SeedRow] | None = None,
) -> str:
    """Minimal evening standdown (Telegram HTML): a dated header plus an optional
    deep link to that day's time-accounting page. Depth lives in Notion, not in
    the message — this is a nudge + pointer, sent with parse_mode=HTML."""
    blocks = [f"🌙 <b>Standdown · {target_date:%a %d %b}</b>"]
    if link_url:
        blocks.append(f'<a href="{html.escape(link_url, quote=True)}">📊 Time accounting →</a>')
    if post_seeds:
        blocks.append(_seed_block("🌱 Post seeds", post_seeds))
    return "\n\n".join(blocks)


def _seed_block(header: str, seeds: list[SeedRow]) -> str:
    lines = [f"<b>{header}</b>"]
    for seed in seeds:
        suffix = []
        if seed.provenance:
            suffix.append(seed.provenance)
        if seed.score is not None:
            suffix.append(f"score {seed.score:.2f}")
        line = f"• {html.escape(seed.text, quote=False)}"
        if suffix:
            line += "\n  <i>" + html.escape(" · ".join(suffix), quote=False) + "</i>"
        lines.append(line)
    return "\n".join(lines)


def format_papers(
    titles: list[str],
    week_label: str,
    board_url: str | None = None,
    log_url: str | None = None,
) -> str:
    """Weekly papers dispatch (Telegram HTML): unrefined Paper Inbox sightings
    plus pointers to the inbox and the expedition log. Terse by design — the
    nudge is the message. Sent with parse_mode=HTML, so dynamic text is escaped."""
    if not titles:
        return "Paper inbox clear. Go sight something new."

    count = len(titles)
    lines = [f"<b>Expedition dispatch — {week_label}</b>", ""]
    lines.append(f"{count} sighting{'s' if count != 1 else ''} awaiting refinement:")
    lines.extend(
        f"• {html.escape(title, quote=False)}" for title in titles[:MAX_TASKS_PER_SECTION]
    )
    hidden = count - MAX_TASKS_PER_SECTION
    if hidden > 0:
        lines.append(f"…and {hidden} more")

    footer: list[str] = []
    if board_url:
        footer.append(f'<a href="{html.escape(board_url, quote=True)}">Paper inbox</a>')
    if log_url:
        footer.append(f'<a href="{html.escape(log_url, quote=True)}">Expedition log</a>')
    if footer:
        lines.append("")
        lines.append("— — —\n" + " · ".join(footer))

    return "\n".join(lines)


def format_health_alert(transitions: list[Transition]) -> str:
    lines = ["⚠️ sleeper-service health"]
    for t in transitions:
        if t.new == "ok":
            lines.append(f"✅ {t.name}: recovered ({t.detail})")
        elif t.old == t.new == "fail":
            lines.append(f"🚨 {t.name}: still failing ({t.detail})")
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
