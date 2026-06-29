"""Evening standdown trigger: a location-gated, late-evening digest.

Unlike the morning wake gate (which gates on a local *hour*), the standdown gates
on *place* — it fires only when you're home (or at Cheryl's) in the
late-evening / small-hours window, and links that day's Notion page. Pure-logic
tests here; delivery/dedupe is exercised against the StateDB elsewhere.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.digests import (
    in_standdown_window,
    standdown_should_fire,
    standdown_target_date,
)
from personal_telegram_bot.formatters import format_standdown

TZ = ZoneInfo("Asia/Singapore")


def _at(hour: int, minute: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=TZ)


# --- in_standdown_window: [21:45, 03:00) spanning midnight ---


def test_window_opens_at_2145():
    assert in_standdown_window(_at(21, 45))


def test_window_closed_just_before_open():
    assert not in_standdown_window(_at(21, 44))


def test_window_spans_midnight():
    assert in_standdown_window(_at(0, 30))


def test_window_closes_at_0300_exclusive():
    assert not in_standdown_window(_at(3, 0))


def test_window_daytime_is_closed():
    assert not in_standdown_window(_at(14, 0))


# --- standdown_target_date: evening = today, small hours = yesterday ---


def test_target_is_today_in_the_evening():
    assert standdown_target_date(_at(22, 0, day=28)) == date(2026, 6, 28)


def test_target_is_yesterday_after_midnight():
    # A 01:00 homecoming closes out the day that just ended.
    assert standdown_target_date(_at(1, 0, day=29)) == date(2026, 6, 28)


# --- standdown_should_fire: place gate AND window ---


def test_fires_at_home_in_window():
    assert standdown_should_fire(_at(22, 0), "Home")


def test_fires_at_cheryl_in_window():
    assert standdown_should_fire(_at(22, 30), "Cheryl")


def test_fires_post_midnight_at_home():
    assert standdown_should_fire(_at(1, 30, day=29), "Home")


def test_blocked_when_away_none_place():
    assert not standdown_should_fire(_at(22, 0), None)


def test_blocked_at_unlisted_place():
    assert not standdown_should_fire(_at(22, 0), "Office")


def test_blocked_outside_window_even_at_home():
    assert not standdown_should_fire(_at(14, 0), "Home")


# --- format_standdown: minimal header + optional deep link ---


def test_format_with_link_escapes_url():
    out = format_standdown(date(2026, 6, 28), "https://notion.so/abc?x=1&y=2")
    assert "Standdown" in out and "28 Jun" in out
    assert "📊" in out
    assert "https://notion.so/abc?x=1&amp;y=2" in out  # & is HTML-escaped


def test_format_without_link_is_header_only():
    out = format_standdown(date(2026, 6, 28))
    assert "Standdown" in out and "28 Jun" in out
    assert "http" not in out
