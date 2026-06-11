from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime

from .config import Config
from .db import StateDB
from .formatters import (
    format_health_alert,
    format_health_summary,
    format_morning_digest,
    format_unit_failure,
)
from .telegram_api import send_message


def _deliver(cfg: Config, text: str, dry_run: bool) -> int | None:
    if dry_run:
        print(text)
        return None
    return send_message(cfg.telegram_token, cfg.default_chat_id, text)


def send_test(cfg: Config, args) -> int:
    message_id = _deliver(cfg, "✅ Test message from sleeper-service (nervous energy).", args.dry_run)
    if message_id:
        print(f"Sent message {message_id} to chat {cfg.default_chat_id}")
    return 0


def send_morning(cfg: Config, args) -> int:
    from .providers.notion_todos import fetch_due_tasks

    if not cfg.notion_token or not cfg.bread_datasource_id:
        print("NOTION_TOKEN / NOTION_BREAD_DATASOURCE_ID not configured", file=sys.stderr)
        return 1

    today = datetime.now(cfg.tz).date()
    date_key = today.isoformat()
    db = StateDB(cfg.db_path)
    if not args.force and not args.dry_run and db.was_sent("morning", date_key):
        print(f"Morning digest already sent for {date_key}; use --force to resend")
        return 0

    try:
        overdue, due_today = fetch_due_tasks(cfg.notion_token, cfg.bread_datasource_id, today)
        text = format_morning_digest(overdue, due_today, today)
    except Exception as exc:
        # Surface the failure on Telegram without leaking tokens or payloads.
        db.log_event("morning-error", {"error": f"{type(exc).__name__}: {exc}"})
        _deliver(
            cfg,
            f"⚠️ Morning digest failed ({type(exc).__name__}). "
            "Inspect with: journalctl -u personal-telegram-bot-morning",
            args.dry_run,
        )
        raise

    message_id = _deliver(cfg, text, args.dry_run)
    if not args.dry_run:
        db.record_sent("morning", date_key, message_id)
        db.log_event("morning-sent", {"overdue": len(overdue), "due_today": len(due_today)})
    return 0


def send_hour(cfg: Config, args) -> int:
    from .formatters import format_hour_report
    from .providers.aw_hours import classify_previous_hour, previous_hour

    now = datetime.now(cfg.tz)
    target = previous_hour(now)
    date_key = target.strftime("%Y-%m-%dT%H:00")
    db = StateDB(cfg.db_path)
    if not args.force and not args.dry_run and db.was_sent("hour", date_key):
        print(f"Hour report already sent for {date_key}")
        return 0

    report = classify_previous_hour(now)
    if report is None:
        print(f"No classification for {date_key} (no data or below thresholds)")
        return 0

    message_id = _deliver(cfg, format_hour_report(report), args.dry_run)
    if not args.dry_run:
        db.record_sent("hour", date_key, message_id)
        db.log_event("hour-sent", {"hour": date_key, "classification": report.classification})
    return 0


def send_health(cfg: Config, args) -> int:
    from .providers.aw_hours import check_aw_freshness
    from .providers.health import diff_transitions, run_all

    db = StateDB(cfg.db_path)
    results = run_all(cfg.health_units, cfg.health_urls)
    results.append(check_aw_freshness(cfg.aw_data_dir, cfg.aw_max_age_hours))
    transitions = diff_transitions(db.get_health_statuses(), results)

    if args.force:
        _deliver(cfg, format_health_summary(results), args.dry_run)
    elif transitions:
        _deliver(cfg, format_health_alert(transitions), args.dry_run)
    else:
        print("No health transitions")

    if not args.dry_run:
        for result in results:
            db.set_health_status(result.name, result.status, result.detail)
        if transitions:
            db.record_sent("health-alert", datetime.now(cfg.tz).isoformat(timespec="seconds"), None)
            db.log_event(
                "health-transitions",
                {t.name: f"{t.old}->{t.new}" for t in transitions},
            )
    return 0


# A unit that fails repeatedly (e.g. a 20-minute timer) alerts at most once
# per window; the 5-minute health poll still tracks ongoing state.
FAILURE_ALERT_WINDOW_HOURS = 4


def failure_window_key(unit: str, now: datetime) -> str:
    return f"{unit}/{now.strftime('%Y-%m-%d')}/{now.hour // FAILURE_ALERT_WINDOW_HOURS}"


def send_failure(cfg: Config, args) -> int:
    db = StateDB(cfg.db_path)
    window_key = failure_window_key(args.unit, datetime.now(cfg.tz))
    if not args.force and not args.dry_run and db.was_sent("unit-failure", window_key):
        print(f"Already alerted for {args.unit} in this window ({window_key})")
        return 0

    journal_tail = None
    try:
        proc = subprocess.run(
            ["journalctl", "-u", args.unit, "-n", "10", "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        journal_tail = proc.stdout.strip() or None
    except Exception:
        pass  # journal access is best-effort; the alert still goes out

    message_id = _deliver(cfg, format_unit_failure(args.unit, journal_tail), args.dry_run)
    if not args.dry_run:
        db.record_sent("unit-failure", window_key, message_id)
        db.log_event("unit-failure", {"unit": args.unit})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="botctl", description="sleeper-service personal Telegram bot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the long-polling bot daemon")

    send = sub.add_parser("send", help="send a one-off message")
    send_sub = send.add_subparsers(dest="kind", required=True)
    for kind, func in (
        ("test", send_test),
        ("morning", send_morning),
        ("hour", send_hour),
        ("health", send_health),
        ("failure", send_failure),
    ):
        p = send_sub.add_parser(kind)
        p.add_argument("--dry-run", action="store_true", help="print instead of sending")
        p.add_argument("--force", action="store_true", help="bypass dedupe / send full summary")
        if kind == "failure":
            p.add_argument("--unit", required=True, help="systemd unit that failed")
        p.set_defaults(func=func)

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "run":
        from .bot import run

        run(cfg)
        return 0
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
