"""Reduce OwnTracks place events into dwell-per-place and a per-hour place.

`place_enter` / `place_leave` transitions bound a place session; `place_present`
pings (region-stamped locations) confirm presence and fill gaps when a
transition was missed. Output is raw context — dominant place per clock-hour and
seconds-per-place — for refining the activity classification in a later phase.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..life_events import LifeEvent, LifeEventsDB

# Tag an hour with the place that dominated it only if presence reaches this — a
# few minutes passing through a region shouldn't claim the hour.
LOCATION_HOUR_MIN_MINUTES = 15.0
# Bound an open session (a missed `leave`, or a stale last region) so a forgotten
# enter doesn't attribute days to one place.
MAX_PLACE_SESSION_HOURS = 16.0


def _place_segments(
    db: LifeEventsDB, day_start: datetime, day_end: datetime, tz: ZoneInfo
) -> list[tuple[str, datetime, datetime]]:
    """(place, start, end) segments overlapping [day_start, day_end). Built by
    walking events: enter/present open or switch the current place, leave closes
    it. A margin pulls in the session that was already open at midnight."""
    margin = timedelta(hours=MAX_PLACE_SESSION_HOURS)
    events: list[LifeEvent] = db.events_between(
        day_start - margin, day_end + margin, source="owntracks"
    )
    events.sort(key=lambda e: e.observed_at)

    segments: list[tuple[str, datetime, datetime]] = []
    current: tuple[str, datetime] | None = None  # (place, start)

    def close(end: datetime) -> None:
        nonlocal current
        if current is not None and end > current[1]:
            segments.append((current[0], current[1], end))
        current = None

    for event in events:
        moment = event.observed_at.astimezone(tz)
        place = event.state
        if not place:
            continue
        if event.event_type == "place_leave":
            if current is not None and current[0] == place:
                close(moment)
        else:  # place_enter / place_present: you are in `place` from `moment`
            if current is None:
                current = (place, moment)
            elif current[0] != place:
                close(moment)
                current = (place, moment)
            # same place already open: extend implicitly (keep the start)

    if current is not None:  # dangling open session
        close(min(day_end, current[1] + timedelta(hours=MAX_PLACE_SESSION_HOURS)))

    clipped = []
    for place, start, end in segments:
        seg_start, seg_end = max(start, day_start), min(end, day_end)
        if seg_end > seg_start:
            clipped.append((place, seg_start, seg_end))
    return clipped


def place_for_hours(db: LifeEventsDB, wake_date: date, tz: ZoneInfo) -> dict[int, str]:
    """Dominant place for each clock-hour of `wake_date` (the place with the most
    presence, if it reaches LOCATION_HOUR_MIN_MINUTES)."""
    day_start = datetime.combine(wake_date, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    segments = _place_segments(db, day_start, day_end, tz)

    per_hour: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for place, start, end in segments:
        cursor = start
        while cursor < end:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            chunk_end = min(end, next_hour)
            per_hour[cursor.hour][place] += (chunk_end - cursor).total_seconds()
            cursor = chunk_end

    result: dict[int, str] = {}
    for hour, places in per_hour.items():
        place, seconds = max(places.items(), key=lambda kv: kv[1])
        if seconds >= LOCATION_HOUR_MIN_MINUTES * 60:
            result[hour] = place
    return result


def dwell_for_date(db: LifeEventsDB, wake_date: date, tz: ZoneInfo) -> dict[str, float]:
    """Seconds spent per place on `wake_date`."""
    day_start = datetime.combine(wake_date, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    dwell: dict[str, float] = defaultdict(float)
    for place, start, end in _place_segments(db, day_start, day_end, tz):
        dwell[place] += (end - start).total_seconds()
    return dict(dwell)
