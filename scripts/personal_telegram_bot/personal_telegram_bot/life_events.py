"""Normalized phone-originated life events (sleep, screen sessions).

Storage and normalization follow docs/design/personal-presence-sleep-tracking.md
and docs/design/time-accounting-next-steps.md: redacted payloads only, with
deterministic event IDs so webhook redelivery is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS life_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    state TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    payload_json TEXT,
    raw_retained INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_life_events_observed_at
    ON life_events (observed_at);
CREATE INDEX IF NOT EXISTS idx_life_events_source_type
    ON life_events (source, event_type);
"""

# Sleep lifecycle events carry a coarse state overlay; every other event type
# (phases, alarms, screen events) is recorded without one.
SAA_STATES = {
    "sleep_tracking_started": "sleeping",
    "sleep_tracking_stopped": "awake",
}


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class LifeEvent:
    source: str
    event_type: str
    observed_at: datetime  # tz-aware
    state: str | None = None
    payload: dict = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        # Second precision: redelivery of a client-stamped event collapses to
        # one row, while real bursts (distinct seconds) stay distinct. value1
        # disambiguates Sleep as Android alarm events sharing a receive second.
        raw = "|".join(
            [
                self.source,
                self.event_type,
                _iso_utc(self.observed_at),
                self.state or "",
                str(self.payload.get("value1", "")),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_saa(payload: dict, received_at: datetime) -> LifeEvent:
    """Normalize a Sleep as Android webhook payload: {"event": ..., "value1-3": ...}.

    Unknown event names are tolerated and recorded; reducers filter later.
    """
    event = payload.get("event")
    if not isinstance(event, str) or not event:
        raise ValueError("payload missing 'event'")
    kept = {"event": event}
    for key in ("value1", "value2", "value3"):
        value = payload.get(key)
        if value not in (None, ""):
            kept[key] = value
    return LifeEvent(
        source="sleep_as_android",
        event_type=event,
        observed_at=received_at,
        state=SAA_STATES.get(event),
        payload=kept,
    )


def normalize_macrodroid(
    payload: dict, received_at: datetime, default_tz: ZoneInfo
) -> LifeEvent:
    """Normalize a MacroDroid screen-event payload: {"event": ..., "ts": iso?}.

    The phone timestamp wins when parseable (it makes redelivery dedupe exact);
    a naive timestamp is interpreted in the configured target timezone.
    """
    event = payload.get("event")
    if not isinstance(event, str) or not event:
        raise ValueError("payload missing 'event'")
    kept = {"event": event}
    observed = received_at
    ts = payload.get("ts")
    if isinstance(ts, str) and ts:
        kept["ts"] = ts
        try:
            parsed = datetime.fromisoformat(ts)
            observed = parsed if parsed.tzinfo else parsed.replace(tzinfo=default_tz)
        except ValueError:
            pass  # unparseable phone clock: receive time is close enough
    return LifeEvent(
        source="macrodroid",
        event_type=event,
        observed_at=observed,
        state=None,
        payload=kept,
    )


class LifeEventsDB:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def insert(self, event: LifeEvent) -> bool:
        """Store a normalized event; returns False for a deduped redelivery."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO life_events"
                " (id, source, event_type, observed_at, state, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.source,
                    event.event_type,
                    _iso_utc(event.observed_at),
                    event.state,
                    json.dumps(event.payload, default=str),
                    _iso_utc(datetime.now(timezone.utc)),
                ),
            )
        return cur.rowcount == 1

    def events_between(
        self,
        start: datetime,
        end: datetime,
        source: str | None = None,
        event_types: Iterable[str] | None = None,
    ) -> list[LifeEvent]:
        query = "SELECT * FROM life_events WHERE observed_at >= ? AND observed_at < ?"
        params: list = [_iso_utc(start), _iso_utc(end)]
        if source:
            query += " AND source = ?"
            params.append(source)
        if event_types:
            types = list(event_types)
            query += f" AND event_type IN ({','.join('?' * len(types))})"
            params.extend(types)
        query += " ORDER BY observed_at"
        rows = self.conn.execute(query, params).fetchall()
        return [
            LifeEvent(
                source=row["source"],
                event_type=row["event_type"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                state=row["state"],
                payload=json.loads(row["payload_json"] or "{}"),
            )
            for row in rows
        ]

    def last_event_at(self) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(observed_at) AS latest FROM life_events"
        ).fetchone()
        return datetime.fromisoformat(row["latest"]) if row and row["latest"] else None

    def close(self) -> None:
        self.conn.close()
