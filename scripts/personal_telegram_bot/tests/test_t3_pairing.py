import sqlite3

from personal_telegram_bot.db import StateDB
from personal_telegram_bot.t3_pairing import (
    PairingSession,
    format_pairing_message,
    load_local_sessions,
    parse_sessions_json,
    select_new_pairings,
)


def _create_t3_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE auth_sessions (
            session_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            scopes TEXT NOT NULL,
            method TEXT NOT NULL,
            client_label TEXT,
            client_ip_address TEXT,
            client_user_agent TEXT,
            client_device_type TEXT NOT NULL DEFAULT 'unknown',
            client_os TEXT,
            client_browser TEXT,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_connected_at TEXT,
            revoked_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO auth_sessions (
            session_id, subject, scopes, method, client_label,
            client_ip_address, client_user_agent, client_device_type,
            client_os, client_browser, issued_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "session-1",
            "one-time-token",
            '["environment:read"]',
            "browser-session-cookie",
            "Pixel 8",
            "100.87.71.120",
            "Mozilla/5.0 secret-agent-detail",
            "mobile",
            "Android",
            "Chrome",
            "2026-07-27T08:00:00.000Z",
            "2026-08-26T08:00:00.000Z",
        ),
    )
    conn.commit()
    conn.close()


def test_load_local_sessions_reads_authorized_clients(tmp_path):
    path = tmp_path / "state.sqlite"
    _create_t3_db(path)

    sessions = load_local_sessions(path)

    assert sessions == [
        PairingSession(
            session_id="session-1",
            label="Pixel 8",
            ip_address="100.87.71.120",
            device_type="mobile",
            os="Android",
            browser="Chrome",
            issued_at="2026-07-27T08:00:00.000Z",
        )
    ]


def test_parse_remote_json_keeps_revoked_pairing_events_but_ignores_non_browser_sessions():
    payload = """[
      {"session_id":"paired","method":"browser-session-cookie","client_label":null,
       "client_ip_address":"100.1.2.3","client_device_type":"desktop",
       "client_os":"macOS","client_browser":"Safari","issued_at":"2026-07-27T08:00:00Z",
       "revoked_at":null},
      {"session_id":"revoked","method":"browser-session-cookie","client_device_type":"mobile",
       "issued_at":"2026-07-27T08:01:00Z","revoked_at":"2026-07-27T08:02:00Z"},
      {"session_id":"cli","method":"bearer-access-token","client_device_type":"bot",
       "issued_at":"2026-07-27T08:03:00Z","revoked_at":null}
    ]"""

    sessions = parse_sessions_json(payload)

    assert [session.session_id for session in sessions] == ["paired", "revoked"]
    assert sessions[0].label is None


def test_first_poll_baselines_existing_sessions_then_returns_only_new(tmp_path):
    db = StateDB(tmp_path / "bot.sqlite3")
    existing = PairingSession("existing", None, None, "desktop", "macOS", "Safari", "2026-07-27T08:00:00Z")
    new = PairingSession("new", "Pixel 8", "100.1.2.3", "mobile", "Android", "Chrome", "2026-07-27T08:01:00Z")

    assert select_new_pairings(db, "wsl", [existing]) == []
    assert select_new_pairings(db, "wsl", [existing, new]) == [new]
    assert select_new_pairings(db, "wsl", [existing, new]) == [new]

    db.record_sent("t3-pairing", "wsl/new", 123)
    assert select_new_pairings(db, "wsl", [existing, new]) == []


def test_pairing_message_contains_useful_metadata_but_no_session_secret():
    session = PairingSession(
        "sensitive-session-id",
        "Pixel <8>",
        "100.1.2.3",
        "mobile",
        "Android",
        "Chrome",
        "2026-07-27T08:01:00Z",
    )

    message = format_pairing_message("contents-may-differ", session)

    assert "New T3 client paired" in message
    assert "contents-may-differ" in message
    assert "Pixel &lt;8&gt;" in message
    assert "Android" in message
    assert "100.1.2.3" in message
    assert "sensitive-session-id" not in message
