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
    format_unit_failure,
)
from .t3_pairing import (
    format_t3_pairing_message,
    pairing_dedupe_key,
    watch_t3_pairing_journal,
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
    from .digests import deliver_morning_digest

    sent = deliver_morning_digest(
        cfg, trigger="timer", force=args.force, dry_run=args.dry_run
    )
    if not sent:
        print(f"Morning digest already sent for {datetime.now(cfg.tz).date().isoformat()}")
    return 0


def run_sleep_summary(args) -> int:
    """Emit the sleep summary for a date as JSON or text. Deliberately does NOT
    build a full Config (no Telegram token) so the Notion sync — which runs
    under dotfiles-sync.service without the bot secret — can call it."""
    import json
    import os
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from .config import DEFAULT_LIFE_DB_PATH
    from .life_events import LifeEventsDB
    from .providers.sleep import duration_hm, sleep_for_date, sleeping_hours_for_date

    tz = ZoneInfo(os.environ.get("TARGET_TZ", "Asia/Singapore"))
    life_db_path = Path(os.environ.get("LIFE_DB", str(DEFAULT_LIFE_DB_PATH)))
    if args.date:
        wake_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        wake_date = datetime.now(tz).date()

    db = LifeEventsDB(life_db_path)
    try:
        summary = sleep_for_date(db, wake_date, tz)
        hours = sleeping_hours_for_date(db, wake_date, tz)
    finally:
        db.close()

    payload: dict = {"date": wake_date.isoformat(), "sleep": None, "sleeping_hours": hours}
    if summary is not None:
        payload["sleep"] = {
            "start": summary.start.isoformat(),
            "end": summary.end.isoformat(),
            "duration_seconds": summary.duration_seconds,
            "duration_hours": round(summary.duration_seconds / 3600, 2),
            "duration_text": duration_hm(summary.duration_seconds),
        }

    if args.json:
        print(json.dumps(payload))
    elif summary is not None:
        print(
            f"{payload['date']}: {payload['sleep']['duration_text']} "
            f"({summary.start:%H:%M}–{summary.end:%H:%M}); sleeping hours {hours}"
        )
    else:
        print(f"{payload['date']}: no sleep recorded")
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


def watch_t3_pairing(cfg: Config, args) -> int:
    db = StateDB(cfg.db_path)
    for pairing in watch_t3_pairing_journal(args.unit, args.since):
        dedupe_key = pairing_dedupe_key(pairing)
        if not args.force and not args.dry_run and db.was_sent("t3-pairing", dedupe_key):
            continue

        message_id = _deliver(cfg, format_t3_pairing_message(pairing, args.label), args.dry_run)
        if not args.dry_run:
            db.record_sent("t3-pairing", dedupe_key, message_id)
            db.log_event(
                "t3-pairing-sent",
                {
                    "unit": args.unit,
                    "has_connection_string": pairing.connection_string is not None,
                    "has_pairing_url": pairing.pairing_url is not None,
                },
            )
        if args.once:
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="botctl", description="sleeper-service personal Telegram bot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the long-polling bot daemon")
    sub.add_parser("serve-ingest", help="serve the tailnet life-event ingest endpoint")

    sleep_summary = sub.add_parser(
        "sleep-summary", help="emit sleep summary for a date (no Telegram token needed)"
    )
    sleep_summary.add_argument("--date", help="YYYY-MM-DD (default: today in TARGET_TZ)")
    sleep_summary.add_argument("--json", action="store_true", help="emit JSON")

    watch = sub.add_parser("watch", help="watch local event streams")
    watch_sub = watch.add_subparsers(dest="kind", required=True)
    t3_pairing = watch_sub.add_parser("t3-pairing", help="publish new T3 pairing keys")
    t3_pairing.add_argument("--unit", default="t3-serve", help="user systemd unit to watch")
    t3_pairing.add_argument("--since", default="now", help="journalctl --since value")
    t3_pairing.add_argument("--label", default="nervous energy", help="label shown in Telegram")
    t3_pairing.add_argument("--dry-run", action="store_true", help="print instead of sending")
    t3_pairing.add_argument("--force", action="store_true", help="bypass dedupe")
    t3_pairing.add_argument("--once", action="store_true", help="exit after the first pairing key")
    t3_pairing.set_defaults(func=watch_t3_pairing)

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

    # Token-free path: must not construct Config (which requires the bot secret).
    if args.command == "sleep-summary":
        return run_sleep_summary(args)

    cfg = Config.from_env()

    if args.command == "run":
        from .bot import run

        run(cfg)
        return 0
    if args.command == "serve-ingest":
        from .ingest_server import serve

        serve(cfg)
        return 0
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
