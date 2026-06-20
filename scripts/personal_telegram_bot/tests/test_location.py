"""Phase 2: OwnTracks location ingest + reducer.

OwnTracks (HTTP mode) posts `transition` enter/leave events for named regions
and region-stamped `location` pings. We store NAMED PLACES ONLY (never raw
coordinates) and reduce them into dwell-per-place and a dominant place per hour
— raw context for refining the activity classification later.
"""

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from personal_telegram_bot.cli import main
from personal_telegram_bot.life_events import (
    LifeEvent,
    LifeEventsDB,
    normalize_owntracks,
)
from personal_telegram_bot.providers.location import dwell_for_date, place_for_hours

TZ = ZoneInfo("Asia/Singapore")
RECEIVED = datetime(2026, 6, 14, 4, 0, tzinfo=timezone.utc)


# --- normalize_owntracks ---


def test_transition_enter_keeps_place_drops_coordinates():
    ev = normalize_owntracks(
        {
            "_type": "transition",
            "event": "enter",
            "desc": "Office",
            "rid": "office01",
            "lat": 1.2966,
            "lon": 103.7764,
            "tst": 1718340000,
        },
        received_at=RECEIVED,
    )
    assert ev is not None
    assert ev.source == "owntracks"
    assert ev.event_type == "place_enter"
    assert ev.state == "Office"
    assert "lat" not in ev.payload and "lon" not in ev.payload
    assert ev.observed_at == datetime.fromtimestamp(1718340000, tz=timezone.utc)


def test_transition_leave_maps_to_place_leave():
    ev = normalize_owntracks(
        {"_type": "transition", "event": "leave", "desc": "Gym", "tst": 1718350000},
        received_at=RECEIVED,
    )
    assert ev is not None
    assert ev.event_type == "place_leave"
    assert ev.state == "Gym"


def test_location_ping_keeps_region_only():
    ev = normalize_owntracks(
        {
            "_type": "location",
            "lat": 1.3521,
            "lon": 103.8198,
            "tst": 1718337600,
            "inregions": ["Home"],
        },
        received_at=RECEIVED,
    )
    assert ev is not None
    assert ev.event_type == "place_present"
    assert ev.state == "Home"
    assert "lat" not in ev.payload and "lon" not in ev.payload


def test_regionless_ping_is_dropped():
    assert (
        normalize_owntracks(
            {"_type": "location", "lat": 1.0, "lon": 2.0, "tst": 1718337600},
            received_at=RECEIVED,
        )
        is None
    )


def test_unknown_type_is_dropped():
    assert normalize_owntracks({"_type": "waypoint"}, received_at=RECEIVED) is None


# --- reducer ---


def _place_event(kind: str, place: str, when: datetime) -> LifeEvent:
    return LifeEvent(
        source="owntracks",
        event_type=kind,
        observed_at=when,
        state=place,
        payload={"desc": place, "value1": place},
    )


def _db(tmp_path, events):
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    for event in events:
        db.insert(event)
    return db


def test_dominant_place_per_hour_and_dwell(tmp_path):
    db = _db(
        tmp_path,
        [
            _place_event("place_enter", "Office", datetime(2026, 6, 14, 9, 0, tzinfo=TZ)),
            _place_event("place_leave", "Office", datetime(2026, 6, 14, 12, 30, tzinfo=TZ)),
        ],
    )

    hours = place_for_hours(db, date(2026, 6, 14), TZ)
    dwell = dwell_for_date(db, date(2026, 6, 14), TZ)

    assert hours == {9: "Office", 10: "Office", 11: "Office", 12: "Office"}
    assert round(dwell["Office"]) == 12600  # 3.5h


def test_present_ping_fills_without_transition(tmp_path):
    # No enter/leave — just two presence pings an hour apart confirm Home.
    db = _db(
        tmp_path,
        [
            _place_event("place_present", "Home", datetime(2026, 6, 14, 22, 0, tzinfo=TZ)),
            _place_event("place_present", "Home", datetime(2026, 6, 14, 22, 40, tzinfo=TZ)),
        ],
    )

    hours = place_for_hours(db, date(2026, 6, 14), TZ)

    assert hours.get(22) == "Home"


def test_empty_location_db(tmp_path):
    db = _db(tmp_path, [])
    assert place_for_hours(db, date(2026, 6, 14), TZ) == {}
    assert dwell_for_date(db, date(2026, 6, 14), TZ) == {}


# --- location-summary CLI (token-free) ---


def test_location_summary_cli_json(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "life.sqlite3"
    db = LifeEventsDB(db_path)
    db.insert(_place_event("place_enter", "Office", datetime(2026, 6, 14, 9, 0, tzinfo=TZ)))
    db.insert(_place_event("place_leave", "Office", datetime(2026, 6, 14, 12, 30, tzinfo=TZ)))
    db.close()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("LIFE_DB", str(db_path))
    monkeypatch.setenv("TARGET_TZ", "Asia/Singapore")

    rc = main(["location-summary", "--date", "2026-06-14", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["date"] == "2026-06-14"
    assert payload["hours"]["9"] == "Office"
    assert round(payload["dwell"]["Office"]) == 12600
