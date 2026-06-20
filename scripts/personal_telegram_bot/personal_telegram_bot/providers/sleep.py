"""Reduce Sleep as Android lifecycle events into sleep intervals.

Two consumers: the morning digest wants "the night I just woke from"
(now-relative), and the Notion daily sync wants "the night attributed to
calendar date D" plus which of D's clock-hours were spent asleep.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..life_events import LifeEventsDB

# Longer pairs are treated as tracking left running, not sleep.
MAX_PLAUSIBLE_SLEEP_HOURS = 16
# Shorter pairs are treated as a tracking blip or nap, not a night's sleep — the
# user is reliably asleep at least this long. Filters out the over-sensitive
# short intervals automatic tracking produces.
MIN_PLAUSIBLE_SLEEP_HOURS = 3
# An hour counts as "asleep" for the Notion overlay if at least this much of it
# overlaps the sleep interval (mirrors the AW hourly thresholds' coarseness).
SLEEPING_HOUR_MIN_MINUTES = 30.0


@dataclass(frozen=True)
class SleepSummary:
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def duration_hm(seconds: float) -> str:
    """Compact human duration: 7h33m / 8h / 45m."""
    hours, minutes = divmod(round(seconds / 60), 60)
    if hours and minutes:
        return f"{hours}h{minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _pair_intervals(events) -> list[tuple[datetime, datetime]]:
    """Pair start/stop events chronologically. Repeated starts keep the
    earliest (tracking restarted mid-sleep); orphan stops are dropped."""
    intervals: list[tuple[datetime, datetime]] = []
    pending_start: datetime | None = None
    for event in events:
        if event.event_type == "sleep_tracking_started":
            if pending_start is None:
                pending_start = event.observed_at
        else:  # sleep_tracking_stopped
            if pending_start is not None and event.observed_at > pending_start:
                intervals.append((pending_start, event.observed_at))
            pending_start = None
    return [
        (start, end)
        for start, end in intervals
        if timedelta(hours=MIN_PLAUSIBLE_SLEEP_HOURS)
        <= end - start
        <= timedelta(hours=MAX_PLAUSIBLE_SLEEP_HOURS)
    ]


def _completed_intervals(
    db: LifeEventsDB, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    events = db.events_between(
        start,
        end,
        source="sleep_as_android",
        event_types=("sleep_tracking_started", "sleep_tracking_stopped"),
    )
    return _pair_intervals(events)


def sleep_for_date(db: LifeEventsDB, wake_date: date, tz: ZoneInfo) -> SleepSummary | None:
    """Main sleep attributed to `wake_date`: the longest completed interval
    that ENDS on that date. Used for daily accounting (Notion, the digest)."""
    window_start = datetime.combine(wake_date - timedelta(days=1), time.min, tzinfo=tz)
    window_end = datetime.combine(wake_date + timedelta(days=1), time.min, tzinfo=tz)
    candidates = [
        (start, end)
        for start, end in _completed_intervals(db, window_start, window_end)
        if end.astimezone(tz).date() == wake_date
    ]
    if not candidates:
        return None
    start, end = max(candidates, key=lambda interval: interval[1] - interval[0])
    return SleepSummary(start=start.astimezone(tz), end=end.astimezone(tz))


def last_night_sleep(db: LifeEventsDB, now: datetime) -> SleepSummary | None:
    """The night just woken from — the main sleep ending on today's date."""
    return sleep_for_date(db, now.date(), now.tzinfo)


def sleeping_hours_for_date(
    db: LifeEventsDB, wake_date: date, tz: ZoneInfo
) -> list[int]:
    """Clock-hours (0–23) of `wake_date` that the main sleep interval covers by
    at least SLEEPING_HOUR_MIN_MINUTES. Typically the early-morning hours; the
    pre-midnight tail belongs to the prior calendar day and is not tagged here."""
    summary = sleep_for_date(db, wake_date, tz)
    if summary is None:
        return []
    hours = []
    for hour in range(24):
        hour_start = datetime.combine(wake_date, time(hour), tzinfo=tz)
        hour_end = hour_start + timedelta(hours=1)
        overlap = (
            min(summary.end, hour_end) - max(summary.start, hour_start)
        ).total_seconds()
        if overlap >= SLEEPING_HOUR_MIN_MINUTES * 60:
            hours.append(hour)
    return hours


def split_interval_by_day(
    start: datetime, end: datetime, tz: ZoneInfo
) -> list[tuple[date, float]]:
    """Split an interval at journal-day boundaries, for daily accounting."""
    parts: list[tuple[date, float]] = []
    cursor = start.astimezone(tz)
    end = end.astimezone(tz)
    while cursor < end:
        day = cursor.date()
        next_midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
        segment_end = min(end, next_midnight)
        parts.append((day, (segment_end - cursor).total_seconds()))
        cursor = segment_end
    return parts
