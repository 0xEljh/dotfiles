"""Shared digest delivery, callable from the timer (cli) and the wake-event
handler (ingest_server). Dedupe is once per local day via the bot state DB, so
whichever trigger fires first wins and the other becomes a no-op."""

from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta

from .config import Config
from .db import StateDB
from .formatters import format_morning_digest, format_papers, format_standdown
from .telegram_api import send_message

# Serializes concurrent in-process triggers (e.g. two wake events arriving
# together) so the was_sent/record_sent check-then-act can't double-send.
_morning_lock = threading.Lock()


def deliver_morning_digest(
    cfg: Config,
    *,
    trigger: str,
    force: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> bool:
    """Build and send the morning digest. Returns True if a message was sent
    (or printed for dry_run), False if it was already sent today."""
    from .life_events import LifeEventsDB
    from .providers.notion_todos import fetch_due_tasks
    from .providers.sleep import last_night_sleep

    now = now or datetime.now(cfg.tz)
    today = now.date()
    date_key = today.isoformat()

    with _morning_lock:
        db = StateDB(cfg.db_path)
        if not force and not dry_run and db.was_sent("morning", date_key):
            return False
        if not cfg.notion_token or not cfg.bread_datasource_id:
            raise RuntimeError("NOTION_TOKEN / NOTION_BREAD_DATASOURCE_ID not configured")

        sleep = None
        try:
            life_db = LifeEventsDB(cfg.life_db_path)
            try:
                sleep = last_night_sleep(life_db, now=now)
            finally:
                life_db.close()
        except Exception:
            pass  # sleep is best-effort context; never block the digest

        try:
            overdue, due_today = fetch_due_tasks(cfg.notion_token, cfg.bread_datasource_id, today)
            text = format_morning_digest(
                overdue, due_today, today, sleep=sleep, board_url=cfg.bread_url
            )
        except Exception as exc:
            db.log_event("morning-error", {"trigger": trigger, "error": f"{type(exc).__name__}: {exc}"})
            if not dry_run:
                send_message(
                    cfg.telegram_token,
                    cfg.default_chat_id,
                    f"⚠️ Morning digest failed ({type(exc).__name__}). "
                    "Inspect with: journalctl -u personal-telegram-bot-ingest",
                )
            raise

        if dry_run:
            print(text)
            return True

        message_id = send_message(
            cfg.telegram_token, cfg.default_chat_id, text, parse_mode="HTML"
        )
        db.record_sent("morning", date_key, message_id)
        db.log_event(
            "morning-sent",
            {"trigger": trigger, "overdue": len(overdue), "due_today": len(due_today)},
        )
        return True


# --- Evening standdown: a location-gated late-evening digest ----------------

# Fires once you're home (or at Cheryl's) in the late-evening / small-hours
# window. Unlike the morning wake gate (a local-hour gate), the gate here is
# *place*; the window only bounds when we bother checking.
ALLOWED_STANDDOWN_PLACES = frozenset({"Home", "Cheryl"})
STANDDOWN_EVENING_START = time(21, 45)
STANDDOWN_MORNING_END = time(3, 0)


def in_standdown_window(
    now: datetime,
    *,
    evening_start: time = STANDDOWN_EVENING_START,
    morning_end: time = STANDDOWN_MORNING_END,
) -> bool:
    """True in [evening_start, midnight) ∪ [midnight, morning_end) — the window
    spans midnight so a post-midnight homecoming still qualifies."""
    local = now.time()
    return local >= evening_start or local < morning_end


def standdown_target_date(
    now: datetime, *, morning_end: time = STANDDOWN_MORNING_END
) -> date:
    """The day a standdown summarizes: today in the evening, the *previous* day in
    the small hours (a 01:00 homecoming closes out the day that just ended)."""
    return now.date() - timedelta(days=1) if now.time() < morning_end else now.date()


def standdown_should_fire(
    now: datetime,
    current_place: str | None,
    *,
    allowed_places=ALLOWED_STANDDOWN_PLACES,
    evening_start: time = STANDDOWN_EVENING_START,
    morning_end: time = STANDDOWN_MORNING_END,
) -> bool:
    """Send the standdown iff you're at an allowed place AND inside the window."""
    return current_place in allowed_places and in_standdown_window(
        now, evening_start=evening_start, morning_end=morning_end
    )


_standdown_lock = threading.Lock()


def deliver_evening_standdown(
    cfg: Config,
    *,
    trigger: str,
    force: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> bool:
    """Send the evening standdown — a dated header + a deep link to that day's
    Time-Accounting page — iff you're home (or at Cheryl's) inside the window,
    deduped once per target day. `force` bypasses the gate and dedupe (manual
    test); a poll that finds you out / outside the window simply returns False and
    the next tick retries. The deep link falls back to the static database URL
    when the Time-Accountant integration isn't configured or the page doesn't
    exist yet."""
    from .life_events import LifeEventsDB
    from .providers.location import current_place
    from .providers.time_accounting import day_page_url

    now = now or datetime.now(cfg.tz)
    target = standdown_target_date(now)
    date_key = target.isoformat()

    with _standdown_lock:
        db = StateDB(cfg.db_path)
        if not force and not dry_run and db.was_sent("standdown", date_key):
            return False

        place = None
        try:
            life_db = LifeEventsDB(cfg.life_db_path)
            try:
                place = current_place(life_db, now)
            finally:
                life_db.close()
        except Exception:
            place = None  # location is the gate, but never crash on a bad read

        if not force and not standdown_should_fire(now, place):
            return False

        link = (
            day_page_url(cfg.time_accountant_secret, cfg.time_accounting_datasource_id, target)
            or cfg.time_accounting_url
        )
        text = format_standdown(target, link)

        if dry_run:
            print(text)
            return True

        message_id = send_message(
            cfg.telegram_token, cfg.default_chat_id, text, parse_mode="HTML"
        )
        db.record_sent("standdown", date_key, message_id)
        db.log_event(
            "standdown-sent",
            {
                "trigger": trigger,
                "place": place,
                "deep_link": bool(link) and link != cfg.time_accounting_url,
            },
        )
        return True


# --- Weekly papers dispatch: nudge refinement of Paper Inbox sightings ------

# Where refined sightings land; linked in the dispatch footer.
EXPEDITION_LOG_URL = "https://0xeljh.com/posts"

_papers_lock = threading.Lock()


def deliver_papers_digest(
    cfg: Config,
    *,
    trigger: str,
    force: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> bool:
    """Send the weekly papers dispatch — Paper Inbox rows whose Status select
    isn't "landed" — deduped once per ISO week. Returns True if a message was
    sent (or printed for dry_run), False if already sent this week. The Paper
    Inbox integration is optional infrastructure: missing Notion config prints
    a note and returns False instead of raising."""
    from .providers.paper_inbox import fetch_pending

    now = now or datetime.now(cfg.tz)
    week_key = now.strftime("%G-W%V")

    with _papers_lock:
        db = StateDB(cfg.db_path)
        if not force and not dry_run and db.was_sent("papers", week_key):
            return False
        if not cfg.notion_token or not cfg.paper_inbox_datasource_id:
            print(
                "Papers digest not configured "
                "(NOTION_TOKEN / NOTION_PAPER_INBOX_DATASOURCE_ID unset); skipping."
            )
            return False

        try:
            titles = fetch_pending(cfg.notion_token, cfg.paper_inbox_datasource_id)
            text = format_papers(
                titles,
                f"week {int(now.strftime('%V'))}",
                board_url=cfg.paper_inbox_url,
                log_url=EXPEDITION_LOG_URL,
            )
        except Exception as exc:
            db.log_event("papers-error", {"trigger": trigger, "error": f"{type(exc).__name__}: {exc}"})
            if not dry_run:
                send_message(
                    cfg.telegram_token,
                    cfg.default_chat_id,
                    f"⚠️ Papers digest failed ({type(exc).__name__}). "
                    "Inspect with: journalctl -u personal-telegram-bot-papers",
                )
            raise

        if dry_run:
            print(text)
            return True

        message_id = send_message(
            cfg.telegram_token, cfg.default_chat_id, text, parse_mode="HTML"
        )
        db.record_sent("papers", week_key, message_id)
        db.log_event("papers-sent", {"trigger": trigger, "pending": len(titles)})
        return True
