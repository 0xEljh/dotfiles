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
from .telegram_api import send_message


def _deliver(cfg: Config, text: str, dry_run: bool, parse_mode: str | None = None) -> int | None:
    if dry_run:
        print(text)
        return None
    return send_message(cfg.telegram_token, cfg.default_chat_id, text, parse_mode=parse_mode)


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


def send_standdown(cfg: Config, args) -> int:
    from .digests import deliver_evening_standdown

    sent = deliver_evening_standdown(
        cfg, trigger="timer", force=args.force, dry_run=args.dry_run
    )
    if not sent:
        print("Standdown skipped (already sent today, or not home / outside window).")
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


def run_phone_summary(args) -> int:
    """Emit per-hour phone app-usage for a date as JSON or text. Token-free, like
    sleep-summary, so the Notion sync can call it without the bot secret."""
    import json
    import os
    from collections import defaultdict
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from .config import DEFAULT_LIFE_DB_PATH
    from .life_events import LifeEventsDB
    from .providers.phone_usage import phone_hours_for_date

    tz = ZoneInfo(os.environ.get("TARGET_TZ", "Asia/Singapore"))
    life_db_path = Path(os.environ.get("LIFE_DB", str(DEFAULT_LIFE_DB_PATH)))
    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target = datetime.now(tz).date()

    db = LifeEventsDB(life_db_path)
    try:
        hours = phone_hours_for_date(db, target, tz)
    finally:
        db.close()

    by_app: dict[str, float] = defaultdict(float)
    for apps in hours.values():
        for app, seconds in apps.items():
            by_app[app] += seconds
    top_apps = sorted(by_app.items(), key=lambda kv: -kv[1])
    total_seconds = sum(by_app.values())

    payload = {
        "date": target.isoformat(),
        "hours": {str(hour): apps for hour, apps in sorted(hours.items())},
        "total_seconds": total_seconds,
        "top_apps": top_apps,
    }

    if args.json:
        print(json.dumps(payload))
    elif hours:
        top = ", ".join(f"{app} {round(s / 60)}m" for app, s in top_apps[:3])
        print(f"{payload['date']}: phone {round(total_seconds / 60)}m over {len(hours)} hours ({top})")
    else:
        print(f"{payload['date']}: no phone activity recorded")
    return 0


def run_location_summary(args) -> int:
    """Emit per-hour dominant place + dwell-per-place for a date as JSON or text.
    Token-free, like sleep-summary, so the Notion sync can call it."""
    import json
    import os
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from .config import DEFAULT_LIFE_DB_PATH
    from .life_events import LifeEventsDB
    from .providers.location import dwell_for_date, place_for_hours

    tz = ZoneInfo(os.environ.get("TARGET_TZ", "Asia/Singapore"))
    life_db_path = Path(os.environ.get("LIFE_DB", str(DEFAULT_LIFE_DB_PATH)))
    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target = datetime.now(tz).date()

    db = LifeEventsDB(life_db_path)
    try:
        hours = place_for_hours(db, target, tz)
        dwell = dwell_for_date(db, target, tz)
    finally:
        db.close()

    payload = {
        "date": target.isoformat(),
        "hours": {str(hour): place for hour, place in sorted(hours.items())},
        "dwell": dwell,
    }

    if args.json:
        print(json.dumps(payload))
    elif dwell:
        places = ", ".join(
            f"{place} {round(seconds / 3600, 1)}h"
            for place, seconds in sorted(dwell.items(), key=lambda kv: -kv[1])
        )
        print(f"{payload['date']}: {places}")
    else:
        print(f"{payload['date']}: no location recorded")
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

    message_id = _deliver(cfg, format_hour_report(report), args.dry_run, parse_mode="HTML")
    if not args.dry_run:
        db.record_sent("hour", date_key, message_id)
        db.log_event("hour-sent", {"hour": date_key, "classification": report.classification})
    return 0


def send_health(cfg: Config, args) -> int:
    from .providers.aw_hours import (
        check_aw_freshness,
        stale_aw_reminder_transition,
        stale_aw_reminder_window_key,
    )
    from .providers.health import diff_transitions, run_all

    db = StateDB(cfg.db_path)
    results = run_all(cfg.health_units, cfg.health_urls)
    aw_result = check_aw_freshness(cfg.aw_data_dir, cfg.aw_max_age_hours)
    results.append(aw_result)
    transitions = diff_transitions(db.get_health_statuses(), results)
    now = datetime.now(cfg.tz)
    aw_row = db.get_health_row(aw_result.name)
    aw_reminder_key = stale_aw_reminder_window_key(
        aw_row,
        aw_result,
        now,
        cfg.aw_systematic_after_hours,
        cfg.aw_stale_reminder_hours,
    )
    aw_reminder = stale_aw_reminder_transition(
        aw_row,
        aw_result,
        now,
        cfg.aw_systematic_after_hours,
    )
    if (
        aw_reminder
        and aw_reminder_key
        and (args.dry_run or not db.was_sent("aw-data-systematic", aw_reminder_key))
    ):
        transitions.append(aw_reminder)

    sent_transition_alert = False
    if args.force:
        _deliver(cfg, format_health_summary(results), args.dry_run)
    elif transitions:
        _deliver(cfg, format_health_alert(transitions), args.dry_run)
        sent_transition_alert = True
    else:
        print("No health transitions")

    if not args.dry_run:
        for result in results:
            db.set_health_status(result.name, result.status, result.detail)
        if sent_transition_alert:
            db.record_sent("health-alert", datetime.now(cfg.tz).isoformat(timespec="seconds"), None)
            if aw_reminder and aw_reminder_key and aw_reminder in transitions:
                db.record_sent("aw-data-systematic", aw_reminder_key, None)
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
    sub.add_parser("serve-ingest", help="serve the tailnet life-event ingest endpoint")

    sleep_summary = sub.add_parser(
        "sleep-summary", help="emit sleep summary for a date (no Telegram token needed)"
    )
    sleep_summary.add_argument("--date", help="YYYY-MM-DD (default: today in TARGET_TZ)")
    sleep_summary.add_argument("--json", action="store_true", help="emit JSON")

    phone_summary = sub.add_parser(
        "phone-summary", help="emit phone app-usage for a date (no Telegram token needed)"
    )
    phone_summary.add_argument("--date", help="YYYY-MM-DD (default: today in TARGET_TZ)")
    phone_summary.add_argument("--json", action="store_true", help="emit JSON")

    location_summary = sub.add_parser(
        "location-summary", help="emit place/dwell for a date (no Telegram token needed)"
    )
    location_summary.add_argument("--date", help="YYYY-MM-DD (default: today in TARGET_TZ)")
    location_summary.add_argument("--json", action="store_true", help="emit JSON")

    send = sub.add_parser("send", help="send a one-off message")
    send_sub = send.add_subparsers(dest="kind", required=True)
    for kind, func in (
        ("test", send_test),
        ("morning", send_morning),
        ("standdown", send_standdown),
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
    if args.command == "phone-summary":
        return run_phone_summary(args)
    if args.command == "location-summary":
        return run_location_summary(args)

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
