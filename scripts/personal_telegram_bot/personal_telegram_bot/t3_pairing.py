from __future__ import annotations

import html
import json
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .db import StateDB

PAIRING_KIND = "t3-pairing"
MONITOR_KIND = "t3-pairing-monitor"

SESSION_QUERY = """
SELECT
    session_id,
    method,
    client_label,
    client_ip_address,
    client_device_type,
    client_os,
    client_browser,
    issued_at,
    revoked_at
FROM auth_sessions
ORDER BY issued_at
"""


@dataclass(frozen=True)
class PairingSession:
    session_id: str
    label: str | None
    ip_address: str | None
    device_type: str
    os: str | None
    browser: str | None
    issued_at: str


def _sessions(rows: list[dict]) -> list[PairingSession]:
    return [
        PairingSession(
            session_id=row["session_id"],
            label=row.get("client_label"),
            ip_address=row.get("client_ip_address"),
            device_type=row.get("client_device_type") or "unknown",
            os=row.get("client_os"),
            browser=row.get("client_browser"),
            issued_at=row["issued_at"],
        )
        for row in rows
        if row.get("method") == "browser-session-cookie"
    ]


def load_local_sessions(path: Path | str) -> list[PairingSession]:
    uri = f"file:{Path(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(SESSION_QUERY).fetchall()]
    finally:
        conn.close()
    return _sessions(rows)


def parse_sessions_json(payload: str) -> list[PairingSession]:
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError("T3 session query did not return a JSON array")
    return _sessions(rows)


def load_remote_sessions(host: str, path: Path | str) -> list[PairingSession]:
    command = f"sqlite3 -json {shlex.quote(str(path))} {shlex.quote(SESSION_QUERY)}"
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, command],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return parse_sessions_json(proc.stdout or "[]")


def select_new_pairings(
    db: StateDB, host: str, sessions: list[PairingSession]
) -> list[PairingSession]:
    if not db.was_sent(MONITOR_KIND, host):
        for session in sessions:
            db.record_sent(PAIRING_KIND, f"{host}/{session.session_id}", None)
        db.record_sent(MONITOR_KIND, host, None)
        return []
    return [
        session
        for session in sessions
        if not db.was_sent(PAIRING_KIND, f"{host}/{session.session_id}")
    ]


def format_pairing_message(host: str, session: PairingSession) -> str:
    device = session.label or session.device_type
    platform = " / ".join(part for part in (session.os, session.browser) if part) or "unknown"
    ip_address = session.ip_address or "unknown"
    return (
        "🔐 <b>New T3 client paired</b>\n"
        f"Host: <code>{html.escape(host)}</code>\n"
        f"Device: {html.escape(device)} ({html.escape(session.device_type)})\n"
        f"Platform: {html.escape(platform)}\n"
        f"Tailnet IP: <code>{html.escape(ip_address)}</code>\n"
        f"Issued: <code>{html.escape(session.issued_at)}</code>"
    )
