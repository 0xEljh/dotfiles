from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from personal_telegram_bot.life_events import (
    LifeEventsDB,
    normalize_macrodroid,
    normalize_saa,
)

TZ = ZoneInfo("Asia/Singapore")
RECEIVED = datetime(2026, 6, 13, 23, 48, 12, tzinfo=TZ)


def test_normalize_saa_maps_sleep_states():
    started = normalize_saa({"event": "sleep_tracking_started"}, RECEIVED)
    stopped = normalize_saa({"event": "sleep_tracking_stopped"}, RECEIVED)

    assert started.source == "sleep_as_android"
    assert started.event_type == "sleep_tracking_started"
    assert started.state == "sleeping"
    assert started.observed_at == RECEIVED
    assert stopped.state == "awake"


def test_normalize_saa_tolerates_unknown_events():
    event = normalize_saa({"event": "lullaby_start"}, RECEIVED)

    assert event.event_type == "lullaby_start"
    assert event.state is None


def test_normalize_saa_rejects_missing_event():
    with pytest.raises(ValueError):
        normalize_saa({"value1": "123"}, RECEIVED)


def test_normalize_saa_keeps_only_nonempty_values():
    event = normalize_saa(
        {"event": "alarm_alert_dismiss", "value1": "1764981000000", "value2": ""},
        RECEIVED,
    )

    assert event.payload["value1"] == "1764981000000"
    assert "value2" not in event.payload


def test_normalize_macrodroid_prefers_phone_timestamp():
    event = normalize_macrodroid(
        {"event": "screen_on", "ts": "2026-06-13T22:15:03"}, RECEIVED, default_tz=TZ
    )

    assert event.source == "macrodroid"
    assert event.event_type == "screen_on"
    assert event.observed_at == datetime(2026, 6, 13, 22, 15, 3, tzinfo=TZ)


def test_normalize_macrodroid_falls_back_to_received_time():
    event = normalize_macrodroid(
        {"event": "screen_off", "ts": "not-a-time"}, RECEIVED, default_tz=TZ
    )

    assert event.observed_at == RECEIVED


def test_normalize_macrodroid_rejects_missing_event():
    with pytest.raises(ValueError):
        normalize_macrodroid({"ts": "2026-06-13T22:15:03"}, RECEIVED, default_tz=TZ)


def test_event_id_is_deterministic_and_second_precise():
    a = normalize_saa({"event": "sleep_tracking_started"}, RECEIVED)
    b = normalize_saa({"event": "sleep_tracking_started"}, RECEIVED)
    c = normalize_saa({"event": "sleep_tracking_started"}, RECEIVED.replace(second=13))

    assert a.event_id == b.event_id
    assert a.event_id != c.event_id
    assert len(a.event_id) == 64


def test_insert_dedupes_redelivery(tmp_path):
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    event = normalize_saa({"event": "sleep_tracking_started"}, RECEIVED)

    assert db.insert(event) is True
    assert db.insert(event) is False

    rows = db.events_between(RECEIVED - timedelta(hours=1), RECEIVED + timedelta(hours=1))
    assert len(rows) == 1


def test_events_between_filters_window_source_and_type(tmp_path):
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    db.insert(normalize_saa({"event": "sleep_tracking_started"}, RECEIVED))
    db.insert(normalize_macrodroid({"event": "screen_on"}, RECEIVED, default_tz=TZ))
    db.insert(
        normalize_saa(
            {"event": "sleep_tracking_stopped"},
            datetime(2026, 6, 14, 7, 21, 0, tzinfo=TZ),
        )
    )

    window = db.events_between(
        RECEIVED - timedelta(hours=1),
        RECEIVED + timedelta(hours=12),
        source="sleep_as_android",
        event_types=("sleep_tracking_started", "sleep_tracking_stopped"),
    )

    assert [e.event_type for e in window] == [
        "sleep_tracking_started",
        "sleep_tracking_stopped",
    ]
    assert all(e.source == "sleep_as_android" for e in window)


def test_events_round_trip_preserves_instant_and_payload(tmp_path):
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    db.insert(normalize_saa({"event": "alarm_alert_dismiss", "value1": "17649"}, RECEIVED))

    (event,) = db.events_between(RECEIVED - timedelta(hours=1), RECEIVED + timedelta(hours=1))

    assert event.observed_at == RECEIVED  # same instant, possibly different tz
    assert event.payload == {"event": "alarm_alert_dismiss", "value1": "17649"}


def test_last_event_at(tmp_path):
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    assert db.last_event_at() is None

    db.insert(normalize_saa({"event": "sleep_tracking_started"}, RECEIVED))
    assert db.last_event_at() == RECEIVED
