from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .config import Config
from .db import StateDB
from .formatters import format_health_summary
from .providers.aw_hours import check_aw_freshness
from .providers.health import run_all

logger = logging.getLogger(__name__)

HELP_TEXT = """Commands:
/status — service health and last digests
/ideate <topic> — draft post seeds from a topic
/improve <draft> — punch up a rough draft (or reply to one)
/score <text> — score whether text is worth posting
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


def parse_tpot_callback_data(data: str | None) -> tuple[str, int] | None:
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "tpot" or parts[1] not in {"used", "remix", "discarded"}:
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


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


def _require_tpot_config(cfg: Config) -> tuple[str, str] | None:
    if not cfg.tpot_inference_url or not cfg.tpot_inference_token:
        return None
    return cfg.tpot_inference_url, cfg.tpot_inference_token


async def _call_tpot(cfg: Config, requests: list[dict], *, timeout_s: int = 45):
    from .tpot.client import TpotClient

    configured = _require_tpot_config(cfg)
    if not configured:
        raise RuntimeError("TPOT inference is not configured")
    url, token = configured
    client = TpotClient(url, token)
    return await asyncio.to_thread(client.batch, requests, timeout_s=timeout_s)


def _format_candidates(title: str, candidates) -> str:
    lines = [title]
    for index, candidate in enumerate(candidates, start=1):
        score = f" (score {candidate.score:.2f})" if candidate.score is not None else ""
        lines.append(f"{index}. {candidate.text}{score}")
    return "\n\n".join(lines)


async def _reply_tpot_error(update_or_query, exc: Exception) -> None:
    from .tpot.client import OperatorActionTpotError, RetryableTpotError

    if isinstance(exc, RetryableTpotError):
        retry = f" Try again in ~{round(exc.retry_after_s / 60)} min." if exc.retry_after_s else " Try again later."
        text = f"TPOT GPU unavailable ({exc.code}).{retry}"
    elif isinstance(exc, OperatorActionTpotError):
        text = f"TPOT needs operator attention ({exc.code})."
    else:
        text = f"TPOT request failed ({type(exc).__name__})."

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text)
    else:
        await update_or_query.reply_text(text)


@_guard
async def cmd_ideate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Usage: /ideate <topic>")
        return
    cfg: Config = context.bot_data["config"]
    try:
        response = await _call_tpot(
            cfg,
            [{"id": "ideate", "op": "ideate", "topic": topic, "k": 3, "best_of": 8}],
        )
        result = response.result_by_id("ideate")
        if not result.ok:
            await update.message.reply_text(f"TPOT ideate failed: {result.code or 'unknown'}")
            return
        await update.message.reply_text(_format_candidates("Post seeds:", result.candidates))
    except Exception as exc:
        await _reply_tpot_error(update, exc)


@_guard
async def cmd_improve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = " ".join(context.args).strip()
    if not draft and update.message.reply_to_message:
        draft = update.message.reply_to_message.text or ""
    if not draft:
        await update.message.reply_text("Usage: /improve <draft> or reply to a draft with /improve")
        return
    cfg: Config = context.bot_data["config"]
    try:
        response = await _call_tpot(
            cfg,
            [{"id": "improve", "op": "improve", "draft": draft, "k": 3, "best_of": 8}],
        )
        result = response.result_by_id("improve")
        if not result.ok:
            await update.message.reply_text(f"TPOT improve failed: {result.code or 'unknown'}")
            return
        await update.message.reply_text(_format_candidates("Variants:", result.candidates))
    except Exception as exc:
        await _reply_tpot_error(update, exc)


@_guard
async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /score <text>")
        return
    cfg: Config = context.bot_data["config"]
    try:
        response = await _call_tpot(cfg, [{"id": "score", "op": "score", "texts": [text]}])
        result = response.result_by_id("score")
        if not result.ok:
            await update.message.reply_text(f"TPOT score failed: {result.code or 'unknown'}")
            return
        score = result.scores[0] if result.scores else None
        await update.message.reply_text("Score unavailable." if score is None else f"Score: {score:.2f}")
    except Exception as exc:
        await _reply_tpot_error(update, exc)


async def on_tpot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    cfg: Config = context.bot_data["config"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, cfg.allowed_user_ids):
        logger.warning("Ignoring callback from unauthorized user: %s", user)
        await query.answer("Not authorized", show_alert=True)
        return

    parsed = parse_tpot_callback_data(query.data)
    if parsed is None:
        await query.answer("Unknown action", show_alert=True)
        return
    action, seed_id = parsed

    from .tpot.seeds import SeedStore

    store = SeedStore(StateDB(cfg.db_path))
    seed = store.get_seed(seed_id)
    if seed is None:
        await query.answer("Seed not found", show_alert=True)
        return

    if action == "used":
        store.record_event(seed_id, "used", {"source": "telegram-callback"})
        await query.answer("Marked used")
        return
    if action == "discarded":
        store.record_event(seed_id, "discarded", {"source": "telegram-callback"})
        await query.answer("Skipped")
        return

    await query.answer("Remixing...")
    try:
        response = await _call_tpot(
            cfg,
            [{"id": "remix", "op": "improve", "draft": seed.text, "k": 3, "best_of": 8}],
        )
        result = response.result_by_id("remix")
        if not result.ok:
            await query.message.reply_text(f"TPOT remix failed: {result.code or 'unknown'}")
            return
        variants = [candidate.text for candidate in result.candidates]
        store.record_event(seed_id, "remixed", {"candidates": variants})
        await query.message.reply_text(_format_candidates("Remixes:", result.candidates))
    except Exception as exc:
        await _reply_tpot_error(query.message, exc)


def build_application(cfg: Config) -> Application:
    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["config"] = cfg
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ideate", cmd_ideate))
    app.add_handler(CommandHandler("improve", cmd_improve))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CallbackQueryHandler(on_tpot_callback, pattern=r"^tpot:"))
    return app


def run(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = build_application(cfg)
    logger.info("Starting long-polling daemon")
    app.run_polling(allowed_updates=["message", "callback_query"])
