"""Reduce phone `app_foreground` events into per-app, per-hour durations.

MacroDroid posts one event per app switch; an app's duration is the gap until
the next switch (the same trick ActivityWatch uses for window focus). The result
feeds the desktop hourly-stats merge so a phone-only hour still counts as
activity — uncategorised for now (app→category classification is a later phase).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..life_events import LifeEvent, LifeEventsDB

# A foreground "session" is bounded so a long idle gap (screen off, no further
# events) between two switches doesn't attribute hours to a single app. Genuine
# single-app use rarely exceeds this; idle over-attribution is the bigger risk.
MAX_FOREGROUND_MINUTES = 60.0

# "Between-apps" surfaces that aren't real app use: the home screen/launcher and
# the system UI sit between every app switch, so counting their time inflates
# usage (the launcher in particular soaks up the dangling final session). They
# still BOUND the previous app's session — they remain in the event stream as
# timestamps — we simply don't attribute their own duration. Matched by package
# (stable) with an app-name fallback; "launcher" substring catches third-party
# launchers too.
EXCLUDED_PHONE_PACKAGES = {"com.android.systemui"}
EXCLUDED_PHONE_APP_NAMES = {"System UI", "Android System"}


def _is_excluded(event: LifeEvent) -> bool:
    package = (event.payload.get("package") or "").lower()
    if package in EXCLUDED_PHONE_PACKAGES or "launcher" in package:
        return True
    app = event.payload.get("app") or ""
    return app in EXCLUDED_PHONE_APP_NAMES or "launcher" in app.lower()


def phone_hours_for_date(
    db: LifeEventsDB, wake_date: date, tz: ZoneInfo
) -> dict[int, dict[str, float]]:
    """Per-hour {app: seconds} for `wake_date` in `tz`. Pairs each foreground
    event with the next, caps idle gaps, caps a dangling final event at its hour
    boundary, and splits sessions across the clock-hours they span."""
    day_start = datetime.combine(wake_date, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    # Margin (>= the session cap) so the app active at 00:00 and the event that
    # closes the day's final in-day session are both in the query window.
    margin = timedelta(minutes=MAX_FOREGROUND_MINUTES + 1)
    # All phone events, not just app_foreground: a screen_off (or any app-less
    # event) must stay in the stream so it BOUNDS the previous app's session — the
    # loop skips attributing time to it but uses it as the prior app's edge.
    events = db.events_between(
        day_start - margin,
        day_end + margin,
        source="phone",
    )
    events = sorted(events, key=lambda e: e.observed_at)

    result: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cap = timedelta(minutes=MAX_FOREGROUND_MINUTES)
    for i, event in enumerate(events):
        app = event.payload.get("app")
        # Excluded apps (launcher / system UI) still bound the *previous* app's
        # session — each event's end is the next event's time regardless — but we
        # skip recording their own duration.
        if not app or _is_excluded(event):
            continue
        start = event.observed_at.astimezone(tz)
        if i + 1 < len(events):
            end = min(events[i + 1].observed_at.astimezone(tz), start + cap)
        else:  # dangling final event: cap at the end of its clock hour
            end = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        seg_start = max(start, day_start)
        seg_end = min(end, day_end)
        cursor = seg_start
        while cursor < seg_end:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            chunk_end = min(seg_end, next_hour)
            result[cursor.hour][app] += (chunk_end - cursor).total_seconds()
            cursor = chunk_end

    return {hour: dict(apps) for hour, apps in result.items()}
