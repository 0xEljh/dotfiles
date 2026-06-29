"""Phase 1: phone app-usage ingest + reducer.

MacroDroid posts an `app_foreground` event per app switch; the reducer pairs
consecutive events into per-app, per-hour durations (the same way ActivityWatch
derives window-focus time), so a phone-only hour still registers as activity.
"""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from personal_telegram_bot.cli import main
from personal_telegram_bot.life_events import LifeEvent, LifeEventsDB, normalize_phone
from personal_telegram_bot.providers.phone_usage import phone_hours_for_date

TZ = ZoneInfo("Asia/Singapore")
UTC = ZoneInfo("UTC")


def _ev(app: str, when: datetime, package: str = "com.example") -> LifeEvent:
    return LifeEvent(
        source="phone",
        event_type="app_foreground",
        observed_at=when,
        payload={"app": app, "value1": app, "package": package},
    )


def _db(tmp_path, events):
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    for event in events:
        db.insert(event)
    return db


# --- normalize_phone ---


def test_normalize_phone_app_foreground_uses_phone_clock():
    ev = normalize_phone(
        {
            "event": "app_foreground",
            "app": "YouTube",
            "package": "com.google.android.youtube",
            "ts": "2026-06-14T21:05:00",
        },
        received_at=datetime(2026, 6, 14, 13, 0, tzinfo=UTC),
        default_tz=TZ,
    )
    assert ev.source == "phone"
    assert ev.event_type == "app_foreground"
    assert ev.payload["app"] == "YouTube"
    # Phone-stamped naive ts is interpreted in the target tz and wins over receive.
    assert ev.observed_at == datetime(2026, 6, 14, 21, 5, tzinfo=TZ)


def test_normalize_phone_requires_app():
    with pytest.raises(ValueError):
        normalize_phone(
            {"event": "app_foreground"},
            received_at=datetime(2026, 6, 14, 13, 0, tzinfo=UTC),
            default_tz=TZ,
        )


def test_normalize_phone_distinct_apps_same_second_are_distinct():
    # Two different apps foregrounded in the same second must not collapse to one
    # row (value1 carries the app into the dedupe id).
    when = datetime(2026, 6, 14, 10, 0, 0, tzinfo=TZ)
    a = normalize_phone({"app": "A"}, received_at=when, default_tz=TZ)
    b = normalize_phone({"app": "B"}, received_at=when, default_tz=TZ)
    assert a.event_id != b.event_id


# --- phone_hours_for_date ---


def test_phone_reducer_pairs_durations_with_dangling_cap(tmp_path):
    db = _db(
        tmp_path,
        [
            _ev("A", datetime(2026, 6, 14, 10, 0, tzinfo=TZ)),
            _ev("B", datetime(2026, 6, 14, 10, 20, tzinfo=TZ)),
            _ev("C", datetime(2026, 6, 14, 10, 50, tzinfo=TZ)),
        ],
    )
    hours = phone_hours_for_date(db, date(2026, 6, 14), TZ)

    assert set(hours.keys()) == {10}
    assert hours[10]["A"] == 1200.0  # 10:00–10:20
    assert hours[10]["B"] == 1800.0  # 10:20–10:50
    assert hours[10]["C"] == 600.0  # dangling → capped at 11:00 hour boundary


def test_phone_reducer_splits_across_hour_boundary(tmp_path):
    db = _db(
        tmp_path,
        [
            _ev("A", datetime(2026, 6, 14, 10, 50, tzinfo=TZ)),
            _ev("B", datetime(2026, 6, 14, 11, 10, tzinfo=TZ)),
        ],
    )
    hours = phone_hours_for_date(db, date(2026, 6, 14), TZ)

    assert hours[10]["A"] == 600.0  # 10:50–11:00
    assert hours[11]["A"] == 600.0  # 11:00–11:10


def test_phone_reducer_empty(tmp_path):
    db = _db(tmp_path, [])
    assert phone_hours_for_date(db, date(2026, 6, 14), TZ) == {}


def test_launcher_excluded_but_still_bounds_sessions(tmp_path):
    db = _db(
        tmp_path,
        [
            _ev("Claude", datetime(2026, 6, 14, 10, 0, 0, tzinfo=TZ), package="com.anthropic.claude"),
            _ev(
                "Pixel Launcher",
                datetime(2026, 6, 14, 10, 0, 30, tzinfo=TZ),
                package="com.google.android.apps.nexuslauncher",
            ),
            _ev("WhatsApp", datetime(2026, 6, 14, 10, 0, 50, tzinfo=TZ), package="com.whatsapp"),
            # final dangling event is the launcher → must NOT inflate usage
            _ev(
                "Pixel Launcher",
                datetime(2026, 6, 14, 10, 5, 0, tzinfo=TZ),
                package="com.google.android.apps.nexuslauncher",
            ),
        ],
    )
    hours = phone_hours_for_date(db, date(2026, 6, 14), TZ)

    # Launcher bounds Claude (30s) without absorbing its own time into it...
    assert hours[10]["Claude"] == 30.0
    assert hours[10]["WhatsApp"] == 250.0  # 10:00:50 → 10:05:00
    # ...and the launcher never appears, including the dangling tail.
    assert "Pixel Launcher" not in hours[10]


def test_system_ui_excluded(tmp_path):
    db = _db(
        tmp_path,
        [
            _ev("System UI", datetime(2026, 6, 14, 10, 0, 0, tzinfo=TZ), package="com.android.systemui"),
            _ev("WhatsApp", datetime(2026, 6, 14, 10, 0, 20, tzinfo=TZ), package="com.whatsapp"),
        ],
    )
    hours = phone_hours_for_date(db, date(2026, 6, 14), TZ)

    assert "System UI" not in hours.get(10, {})
    assert "WhatsApp" in hours[10]


# --- phone-summary CLI (token-free, like sleep-summary) ---


def test_phone_summary_cli_json(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "life.sqlite3"
    db = LifeEventsDB(db_path)
    db.insert(_ev("A", datetime(2026, 6, 14, 10, 0, tzinfo=TZ)))
    db.insert(_ev("B", datetime(2026, 6, 14, 10, 20, tzinfo=TZ)))
    db.close()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("LIFE_DB", str(db_path))
    monkeypatch.setenv("TARGET_TZ", "Asia/Singapore")

    rc = main(["phone-summary", "--date", "2026-06-14", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["date"] == "2026-06-14"
    assert payload["hours"]["10"]["A"] == 1200.0


# --- screen events as session boundaries (the idle fix) ---


def _screen(event_type: str, when: datetime) -> LifeEvent:
    return LifeEvent(
        source="phone",
        event_type=event_type,
        observed_at=when,
        payload={"event": event_type, "value1": event_type},
    )


def test_normalize_phone_screen_off_has_no_app():
    ev = normalize_phone(
        {"event": "screen_off"},
        received_at=datetime(2026, 6, 14, 13, 0, tzinfo=UTC),
        default_tz=TZ,
    )
    assert ev.source == "phone"
    assert ev.event_type == "screen_off"
    assert "app" not in ev.payload


def test_normalize_phone_screen_on_uses_phone_clock():
    ev = normalize_phone(
        {"event": "screen_on", "ts": "2026-06-14T21:00:00"},
        received_at=datetime(2026, 6, 14, 13, 0, tzinfo=UTC),
        default_tz=TZ,
    )
    assert ev.event_type == "screen_on"
    assert ev.observed_at == datetime(2026, 6, 14, 21, 0, tzinfo=TZ)


def test_normalize_phone_unknown_appless_event_raises():
    with pytest.raises(ValueError):
        normalize_phone(
            {"event": "wiggle"},
            received_at=datetime(2026, 6, 14, 13, 0, tzinfo=UTC),
            default_tz=TZ,
        )


def test_screen_off_bounds_foreground_app(tmp_path):
    # App open, screen off 5 min later, next pickup an hour on: the app earns
    # exactly 5 min, not the idle stretch to the next event.
    db = _db(
        tmp_path,
        [
            _ev("YouTube", datetime(2026, 6, 14, 10, 0, tzinfo=TZ)),
            _screen("screen_off", datetime(2026, 6, 14, 10, 5, tzinfo=TZ)),
            _ev("Chrome", datetime(2026, 6, 14, 11, 0, tzinfo=TZ)),
        ],
    )
    hours = phone_hours_for_date(db, date(2026, 6, 14), TZ)

    assert hours[10]["YouTube"] == 300.0  # 10:00–10:05, closed by screen_off
    assert "YouTube" not in hours.get(11, {})  # no idle bleed into the next hour


def test_screen_events_carry_no_app_time(tmp_path):
    db = _db(
        tmp_path,
        [
            _screen("screen_on", datetime(2026, 6, 14, 9, 0, tzinfo=TZ)),
            _ev("Maps", datetime(2026, 6, 14, 9, 1, tzinfo=TZ)),
            _screen("screen_off", datetime(2026, 6, 14, 9, 6, tzinfo=TZ)),
        ],
    )
    hours = phone_hours_for_date(db, date(2026, 6, 14), TZ)

    assert hours[9] == {"Maps": 300.0}  # 9:01–9:06; screen events add nothing
