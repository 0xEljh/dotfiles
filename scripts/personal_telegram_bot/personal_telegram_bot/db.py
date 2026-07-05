from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_digests (
    kind TEXT NOT NULL,
    date_key TEXT NOT NULL,
    message_id INTEGER,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (kind, date_key)
);
CREATE TABLE IF NOT EXISTS health_state (
    check_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT,
    since TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS tpot_seeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_date TEXT NOT NULL,
    topic TEXT NOT NULL,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    score REAL,
    model_versions TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tpot_seeds_date_topic ON tpot_seeds(seed_date, topic);
CREATE INDEX IF NOT EXISTS idx_tpot_seeds_status_date ON tpot_seeds(status, seed_date);
CREATE TABLE IF NOT EXISTS tpot_seed_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_id INTEGER NOT NULL REFERENCES tpot_seeds(id),
    event TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tpot_seed_events_seed ON tpot_seed_events(seed_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateDB:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def was_sent(self, kind: str, date_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sent_digests WHERE kind = ? AND date_key = ?",
            (kind, date_key),
        ).fetchone()
        return row is not None

    def record_sent(self, kind: str, date_key: str, message_id: int | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO sent_digests (kind, date_key, message_id, sent_at)"
                " VALUES (?, ?, ?, ?)",
                (kind, date_key, message_id, _now()),
            )

    def last_sent(self, kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sent_digests WHERE kind = ? ORDER BY date_key DESC LIMIT 1",
            (kind,),
        ).fetchone()

    def get_health_statuses(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT check_name, status FROM health_state").fetchall()
        return {row["check_name"]: row["status"] for row in rows}

    def get_health_row(self, check_name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM health_state WHERE check_name = ?", (check_name,)
        ).fetchone()

    def set_health_status(
        self, check_name: str, status: str, detail: str = "", now: str | None = None
    ) -> None:
        now = now or _now()
        existing = self.get_health_row(check_name)
        since = existing["since"] if existing and existing["status"] == status else now
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO health_state"
                " (check_name, status, detail, since, updated_at) VALUES (?, ?, ?, ?, ?)",
                (check_name, status, detail, since, now),
            )

    def log_event(self, kind: str, payload: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                (_now(), kind, json.dumps(payload, default=str)),
            )
