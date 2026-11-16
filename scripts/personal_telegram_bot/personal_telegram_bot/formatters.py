from __future__ import annotations

import html
import math
import shlex
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from .providers.health import CheckResult, Transition
from .providers.notion_todos import Task
from .providers.tailscale import DeviceKeyExpiry
from .providers.sleep import SleepSummary, duration_hm
from .tpot.seeds import SeedRow

MAX_TASKS_PER_SECTION = 15
PREVIEW_ITEMS = 3
MESSAGE_BUDGET = 3900
HEALTH_DETAILS_COMMAND = "uv run botctl send health --force --dry-run"


def _fits_message(text: str, *, reserve: int = 0) -> bool:
    # Count markup too, so the sender's raw slice cannot cut a tag or entity.
    return len(text.encode("utf-16-le")) // 2 <= MESSAGE_BUDGET - reserve


def _plain_list(header: str, items: list[str], details: str, *, footer: str = "", compact: bool = True) -> str:
    def render(shown: int) -> str:
        blocks = [header, *items[:shown]]
        if footer:
            blocks.extend(["", footer])
        if shown < len(items):
            blocks.extend(["", f"{len(items) - shown} more items not shown.", details])
        return "\n".join(blocks)

    text = render(len(items))
    if not compact:
        return text
    shown = len(items)
    while not _fits_message(text) and shown:
        shown -= 1
        text = render(shown)
    return text


def _task_link(task: Task) -> str:
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


def _task_block(header: str, tasks: list[Task], render, *, total: int | None = None) -> str:
    total = len(tasks) if total is None else total
    lines = [f"<b>{header} ({total})</b>"]
    lines.extend(render(task) for task in tasks)
    hidden = total - len(tasks)
    if hidden > 0:
        lines.append(f"…and {hidden} more")
    return "\n".join(lines)


def format_sleep_line(sleep: SleepSummary) -> str:
    window = f"{sleep.start:%H:%M}–{sleep.end:%H:%M}"
    return f"Slept {duration_hm(sleep.duration_seconds)} ({window})"


def format_morning_digest(
    overdue: list[Task],
    due_today: list[Task],
    today: date,
    sleep: SleepSummary | None = None,
    board_url: str | None = None,
    post_seeds: list[SeedRow] | None = None,
) -> str:
    """Show today's shortlist first; keep secondary tasks in an expandable quote."""
    today_limit = min(len(due_today), MAX_TASKS_PER_SECTION)
    overdue_limit = min(len(overdue), MAX_TASKS_PER_SECTION)
    seed_limit = len(post_seeds or [])
    while True:
        text = _morning_digest(
            overdue, due_today, today, sleep, board_url, post_seeds,
            today_limit, overdue_limit, seed_limit,
        )
        if _fits_message(text):
            return text
        # Remove complete items, not characters in HTML tags or URLs.
        if seed_limit:
            seed_limit -= 1
        elif overdue_limit:
            overdue_limit -= 1
        elif today_limit:
            today_limit -= 1
        else:
            board_url = None


def _morning_digest(overdue, due_today, today, sleep, board_url, post_seeds,
                    today_limit, overdue_limit, seed_limit) -> str:
    counts = []
    if due_today:
        counts.append(f"{len(due_today)} due today")
    if overdue:
        counts.append(f"{len(overdue)} overdue")
    heading = " · ".join([f"{today:%a %d %b}", *counts])
    blocks = [f"<b>{heading}</b>"]
    details: list[str] = []

    if not overdue and not due_today:
        blocks.append("Nothing due or overdue.")
    else:
        if due_today:
            blocks.append("\n".join(_today_line(task) for task in due_today[:min(PREVIEW_ITEMS, today_limit)]))
            if len(due_today) > PREVIEW_ITEMS:
                details.append(_task_block(
                    "More due today", due_today[PREVIEW_ITEMS:today_limit], _today_line,
                    total=len(due_today) - PREVIEW_ITEMS,
                ))
            hidden_preview = min(PREVIEW_ITEMS, len(due_today)) - min(PREVIEW_ITEMS, today_limit)
            if hidden_preview:
                blocks.append(f"{hidden_preview} more due today not shown")
        if overdue:
            if due_today:
                details.append(_task_block("Overdue", overdue[:overdue_limit], lambda t: _overdue_line(t, today), total=len(overdue)))
            else:
                shown = min(PREVIEW_ITEMS, overdue_limit)
                blocks.append("\n".join(_overdue_line(task, today) for task in overdue[:shown]))
                if len(overdue) > shown:
                    details.append(_task_block("More overdue", overdue[shown:overdue_limit], lambda t: _overdue_line(t, today), total=len(overdue) - shown))

    if post_seeds:
        details.append(_seed_block("Post ideas", post_seeds[:seed_limit]))
        if len(post_seeds) > seed_limit:
            details.append(f"{len(post_seeds) - seed_limit} more post ideas omitted")

    if details:
        blocks.append("<blockquote expandable>" + "\n\n".join(details) + "</blockquote>")

    footer: list[str] = []
    if sleep:
        footer.append(f"Sleep: {duration_hm(sleep.duration_seconds)}")
    if board_url:
        footer.append(f'<a href="{html.escape(board_url, quote=True)}">Bread board</a>')
    if footer:
        blocks.append(" · ".join(footer))

    return "\n\n".join(block for block in blocks if block)


def format_standdown(
    target_date: date,
    link_url: str | None = None,
    post_seeds: list[SeedRow] | None = None,
) -> str:
    """Minimal evening standdown (Telegram HTML): a dated header plus an optional
    deep link to that day's time-accounting page. Depth lives in Notion, not in
    the message — this is a nudge + pointer, sent with parse_mode=HTML."""
    blocks = [f"<b>Standdown · {target_date:%a %d %b}</b>"]
    if link_url:
        blocks.append(f'<a href="{html.escape(link_url, quote=True)}">Review your day</a>')
    if post_seeds:
        seed_limit = len(post_seeds)
        while seed_limit:
            detail = "<blockquote expandable>" + _seed_block("Post ideas", post_seeds[:seed_limit]) + "</blockquote>"
            if _fits_message("\n\n".join(blocks + [detail]), reserve=60):
                blocks.append(detail)
                break
            seed_limit -= 1
        if seed_limit < len(post_seeds):
            blocks.append(f"{len(post_seeds) - seed_limit} more post ideas omitted")
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
        return "Paper inbox clear."

    count = len(titles)
    shown = min(count, MAX_TASKS_PER_SECTION)
    while True:
        text = _papers(titles, week_label, board_url, log_url, shown)
        if _fits_message(text):
            return text
        if shown:
            shown -= 1
        elif board_url or log_url:
            board_url = log_url = None
        else:
            week_label = week_label[:80]


def _papers(titles, week_label, board_url, log_url, shown) -> str:
    count = len(titles)
    lines = [f"<b>{count} paper{'s' if count != 1 else ''} to review · {html.escape(week_label)}</b>", ""]
    lines.extend(
        f"• {html.escape(title, quote=False)}" for title in titles[:min(PREVIEW_ITEMS, shown)]
    )
    details = [f"• {html.escape(title, quote=False)}" for title in titles[PREVIEW_ITEMS:shown]]
    hidden = count - shown
    if hidden > 0:
        details.append(f"…and {hidden} more in the paper inbox")
    if details:
        lines.extend(["", "<blockquote expandable>" + "\n".join(details) + "</blockquote>"])

    footer: list[str] = []
    if board_url:
        footer.append(f'<a href="{html.escape(board_url, quote=True)}">Paper inbox</a>')
    if log_url:
        footer.append(f'<a href="{html.escape(log_url, quote=True)}">Expedition log</a>')
    if footer:
        lines.append("")
        lines.append(" · ".join(footer))

    return "\n".join(lines)


def format_health_alert(
    transitions: list[Transition], *, host: str = "sleeper-service",
    details_command: str = HEALTH_DETAILS_COMMAND,
) -> str:
    if len(transitions) == 1:
        t = transitions[0]
        status = _transition_status(t)
        headline = f"{t.name}: {status} on {host}"
        detail = _transition_detail(t)
        return _plain_list(headline, [detail] if detail else [],
                           f"Full status on {host}:\n{details_command}")

    outcome = _health_counts((t.new for t in transitions), healthy_label="recovered")
    lines = [f"{host} · {outcome}"]
    for t in transitions:
        status = _transition_status(t)
        detail_text = _transition_detail(t)
        detail = f" · {detail_text}" if detail_text else ""
        lines.append(f"{t.name}: {status}{detail}")
    return _plain_list(lines[0], lines[1:], f"Full status on {host}:\n{details_command}")


def _transition_status(transition: Transition) -> str:
    if transition.old == transition.new == "fail":
        return "still failing"
    return {"fail": "failed", "ok": "recovered"}.get(transition.new, transition.new)


def _transition_detail(transition: Transition) -> str:
    if transition.new == "ok" and not transition.name.startswith("disk "):
        return ""
    return transition.detail if transition.detail != _transition_status(transition) else ""


def _health_counts(states: Iterable[str], *, healthy_label: str) -> str:
    counts = dict.fromkeys(("critical", "fail", "warning", "ok"), 0)
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    return " · ".join(
        f"{count} " + {"fail": "failed", "ok": healthy_label}.get(state, state)
        for state, count in counts.items() if count
    ) or "no checks"


def format_health_summary(
    results: Iterable[CheckResult], *, host: str = "sleeper-service", compact: bool = True,
    details_command: str = HEALTH_DETAILS_COMMAND,
) -> str:
    results = list(results)
    outcome = _health_counts((r.status for r in results), healthy_label="ok")
    lines = [f"{host} · {outcome}"]
    for r in results:
        status = {"ok": "ok", "fail": "failed"}.get(r.status, r.status)
        detail = f" · {r.detail}" if r.detail and r.detail != status else ""
        lines.append(f"{r.name}: {status}{detail}")
    return _plain_list(lines[0], lines[1:], f"Full status on {host}:\n{details_command}", compact=compact)


def format_tailscale_key_expiry(devices: list[DeviceKeyExpiry], now: datetime, *, compact: bool = True) -> str:
    if not devices:
        raise ValueError("at least one Tailscale device is required")
    lines = ["Tailscale devices need reauthentication"]
    now_utc = now.astimezone(timezone.utc)
    for device in devices:
        if device.expires_at is None:
            detail = "expired"
        else:
            delta_days = (device.expires_at - now_utc).total_seconds() / 86400
            if device.expired or delta_days <= 0:
                days = max(0, int(-delta_days))
                detail = "expired today" if days == 0 else f"expired {days} day{'s' if days != 1 else ''} ago"
            else:
                days = max(1, int(delta_days + 0.999999))
                local_expiry = device.expires_at.astimezone(now.tzinfo)
                detail = f"expires in {days} day{'s' if days != 1 else ''} ({local_expiry:%d %b})"
        lines.append(f"• {device.name}: {detail}")
    return _plain_list(
        lines[0], lines[1:],
        "Full list in the bot checkout:\nuv run botctl send tailscale-keys --force --dry-run",
        footer="On each affected device, run: sudo tailscale up", compact=compact,
    )


def _hour_label(hour: int) -> str:
    start = datetime(2000, 1, 1, hour)
    end = start + timedelta(hours=1)
    sm, em = start.strftime("%p").lower(), end.strftime("%p").lower()
    sh, eh = start.strftime("%I").lstrip("0"), end.strftime("%I").lstrip("0")
    # Collapse the meridiem when both ends share it: "2–3pm" vs "11am–12pm".
    return f"{sh}–{eh}{em}" if sm == em else f"{sh}{sm}–{eh}{em}"


def _minutes(seconds: float) -> str:
    if 0 < seconds < 60 and not math.isclose(seconds, 60, rel_tol=1e-12, abs_tol=0):
        return "&lt;1m"
    return f"{round(seconds / 60)}m"


def format_hour_report(report) -> str:
    classification = report.classification or "Activity"
    header = f"<b>{_hour_label(report.hour)} · {html.escape(classification)}</b>"
    tools = " · ".join(
        f"{html.escape(name)} {_minutes(secs)}" for name, secs in report.top_tools
    )
    body = f"{_minutes(report.active_seconds)} active" + (f" · {tools}" if tools else "")
    if report.ai_chats:
        ai_chats = " · ".join(
            f"{html.escape(name)} {_minutes(secs)}" for name, secs in report.ai_chats
        )
        body += f"\nAI chat: {ai_chats}"
    return f"{header}\n{body}"


def format_unit_failure(unit: str, *, host: str = "sleeper-service") -> str:
    return (
        f"{unit} failed on {host}\n\n"
        "Logs:\n"
        f"journalctl -u {shlex.quote(unit)} -n 50 --no-pager"
    )
