"""Shared digest delivery, callable from the timer (cli) and the wake-event
handler (ingest_server). Dedupe is once per local day via the bot state DB, so
whichever trigger fires first wins and the other becomes a no-op."""

from __future__ import annotations

import threading
from datetime import datetime

from .config import Config
from .db import StateDB
from .formatters import format_morning_digest
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
