from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from personal_telegram_bot.formatters import format_sleep_line
from personal_telegram_bot.life_events import LifeEvent, LifeEventsDB
from personal_telegram_bot.providers.sleep import (
    SleepSummary,
    duration_hm,
    last_night_sleep,
    sleep_for_date,
    sleeping_hours_for_date,
    split_interval_by_day,
)

TZ = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 6, 14, 9, 30, tzinfo=TZ)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=TZ)


def _sleep_event(kind: str, when: datetime) -> LifeEvent:
    return LifeEvent(
        source="sleep_as_android",
        event_type=kind,
        observed_at=when,
        state=None,
        payload={},
    )


def _db_with(tmp_path, events) -> LifeEventsDB:
    db = LifeEventsDB(tmp_path / "life.sqlite3")
    for event in events:
        db.insert(event)
    return db


def test_pairs_latest_completed_interval(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
        ],
    )

    summary = last_night_sleep(db, now=NOW)

    assert summary is not None
    assert summary.start == _dt("2026-06-13 23:48")
    assert summary.end == _dt("2026-06-14 07:21")
    expected = timedelta(hours=7, minutes=33).total_seconds()
    assert summary.duration_seconds == expected


def test_none_when_no_completed_interval(tmp_path):
    db = _db_with(
        tmp_path, [_sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48"))]
    )

    assert last_night_sleep(db, now=NOW) is None


def test_none_on_empty_db(tmp_path):
    db = _db_with(tmp_path, [])

    assert last_night_sleep(db, now=NOW) is None


def test_restarted_tracking_uses_earliest_start(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:00")),
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:30")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:00")),
        ],
    )

    summary = last_night_sleep(db, now=NOW)

    assert summary is not None
    assert summary.start == _dt("2026-06-13 23:00")
    assert summary.end == _dt("2026-06-14 07:00")


def test_main_sleep_beats_morning_nap(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
            _sleep_event("sleep_tracking_started", _dt("2026-06-14 08:00")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 08:40")),
        ],
    )

    summary = last_night_sleep(db, now=NOW)

    assert summary is not None
    assert summary.start == _dt("2026-06-13 23:48")
    assert summary.end == _dt("2026-06-14 07:21")


def test_orphan_stop_is_ignored(tmp_path):
    db = _db_with(
        tmp_path, [_sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21"))]
    )

    assert last_night_sleep(db, now=NOW) is None


def test_short_interval_is_not_counted_as_sleep(tmp_path):
    # A short tracking blip (auto-start noise) is not a night's sleep.
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-14 00:59")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 01:15")),
        ],
    )

    assert sleep_for_date(db, date(2026, 6, 14), TZ) is None
    assert last_night_sleep(db, now=NOW) is None
    assert sleeping_hours_for_date(db, date(2026, 6, 14), TZ) == []


def test_interval_below_three_hour_minimum_is_not_sleep(tmp_path):
    # 2h30m clears a nap but not a night: below the 3h floor, it is not sleep.
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-14 01:00")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 03:30")),
        ],
    )

    assert sleep_for_date(db, date(2026, 6, 14), TZ) is None


def test_interval_meeting_three_hour_minimum_is_sleep(tmp_path):
    # Exactly 3h passes — the floor is inclusive.
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-14 01:00")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 04:00")),
        ],
    )

    summary = sleep_for_date(db, date(2026, 6, 14), TZ)
    assert summary is not None
    assert summary.duration_seconds == timedelta(hours=3).total_seconds()


def test_summary_times_are_in_now_timezone(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
        ],
    )

    summary = last_night_sleep(db, now=NOW)

    assert summary is not None
    assert summary.start.utcoffset() == NOW.utcoffset()
    assert summary.end.utcoffset() == NOW.utcoffset()


def test_split_interval_across_midnight():
    parts = split_interval_by_day(_dt("2026-06-13 23:48"), _dt("2026-06-14 07:21"), TZ)

    assert parts == [
        (date(2026, 6, 13), 12 * 60.0),
        (date(2026, 6, 14), (7 * 60 + 21) * 60.0),
    ]


def test_split_interval_single_day():
    parts = split_interval_by_day(_dt("2026-06-14 14:00"), _dt("2026-06-14 15:30"), TZ)

    assert parts == [(date(2026, 6, 14), 90 * 60.0)]


def test_format_sleep_line():
    summary = SleepSummary(start=_dt("2026-06-13 23:48"), end=_dt("2026-06-14 07:21"))

    assert format_sleep_line(summary) == "😴 Slept 7h33m (23:48–07:21)"


def test_format_sleep_line_subhour_duration():
    summary = SleepSummary(start=_dt("2026-06-14 14:00"), end=_dt("2026-06-14 14:45"))

    assert format_sleep_line(summary) == "😴 Slept 45m (14:00–14:45)"


# --- sleep_for_date: explicit-date attribution (Notion daily sync) ---


def test_sleep_for_date_returns_interval_ending_on_that_date(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
        ],
    )

    summary = sleep_for_date(db, date(2026, 6, 14), TZ)

    assert summary is not None
    assert summary.start == _dt("2026-06-13 23:48")
    assert summary.end == _dt("2026-06-14 07:21")


def test_sleep_for_date_ignores_other_nights(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
        ],
    )

    # The night ended on the 14th, so the 13th and 15th have nothing.
    assert sleep_for_date(db, date(2026, 6, 13), TZ) is None
    assert sleep_for_date(db, date(2026, 6, 15), TZ) is None


def test_last_night_sleep_matches_sleep_for_date(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
        ],
    )

    assert last_night_sleep(db, now=NOW) == sleep_for_date(db, date(2026, 6, 14), TZ)


# --- sleeping_hours_for_date: per-hour overlay for the "bio" tag ---


def test_sleeping_hours_tags_hours_with_majority_overlap(tmp_path):
    db = _db_with(
        tmp_path,
        [
            _sleep_event("sleep_tracking_started", _dt("2026-06-13 23:48")),
            _sleep_event("sleep_tracking_stopped", _dt("2026-06-14 07:21")),
        ],
    )

    # Hours 0–6 are fully/mostly asleep; hour 7 only has 21 min (< 30) so it drops.
    assert sleeping_hours_for_date(db, date(2026, 6, 14), TZ) == [0, 1, 2, 3, 4, 5, 6]


def test_sleeping_hours_empty_without_sleep(tmp_path):
    db = _db_with(tmp_path, [])

    assert sleeping_hours_for_date(db, date(2026, 6, 14), TZ) == []


def test_duration_hm_formats():
    assert duration_hm(timedelta(hours=7, minutes=33).total_seconds()) == "7h33m"
    assert duration_hm(timedelta(hours=8).total_seconds()) == "8h"
    assert duration_hm(timedelta(minutes=45).total_seconds()) == "45m"
