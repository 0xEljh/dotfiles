from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Config
from .db import StateDB
from .formatters import format_health_summary
from .providers.aw_hours import check_aw_freshness
from .providers.health import run_all

logger = logging.getLogger(__name__)

HELP_TEXT = """Commands:
/status — service health and last digests
/help — this message

Scheduled: morning Bread digest at 09:30, health checks every 5 minutes,
hourly activity classification at :10 past the hour."""


def is_authorized(user_id: int | None, allowed: frozenset[int]) -> bool:
    return user_id is not None and user_id in allowed


def _guard(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg: Config = context.bot_data["config"]
        user = update.effective_user
        if not is_authorized(user.id if user else None, cfg.allowed_user_ids):
            logger.warning("Ignoring message from unauthorized user: %s", user)
            return
        await handler(update, context)

    return wrapped


@_guard
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("nervous energy online.\n\n" + HELP_TEXT)


@_guard
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


@_guard
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["config"]
    results = await asyncio.to_thread(run_all, cfg.health_units, cfg.health_urls)
    results.append(check_aw_freshness(cfg.aw_data_dir, cfg.aw_max_age_hours))
    lines = [format_health_summary(results)]

    db = StateDB(cfg.db_path)
    for kind in ("morning", "health-alert"):
        row = db.last_sent(kind)
        if row:
            lines.append(f"Last {kind}: {row['date_key']} (sent {row['sent_at']})")
    await update.message.reply_text("\n\n".join(lines))


def build_application(cfg: Config) -> Application:
    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["config"] = cfg
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    return app


def run(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = build_application(cfg)
    logger.info("Starting long-polling daemon")
    app.run_polling(allowed_updates=["message"])
