import http.client
import json
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from personal_telegram_bot.ingest_server import build_server, redact_token
from personal_telegram_bot.life_events import LifeEventsDB

TOKEN = "test-token-123"
WIDE_WINDOW = (
    datetime(2000, 1, 1, tzinfo=timezone.utc),
    datetime(2100, 1, 1, tzinfo=timezone.utc),
)


@pytest.fixture
def server(tmp_path):
    srv = build_server(
        bind="127.0.0.1",
        port=0,
        token=TOKEN,
        db_path=tmp_path / "life.sqlite3",
        default_tz=ZoneInfo("Asia/Singapore"),
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _request(srv, method, path, body=None, raw_body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    payload = raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)
    conn.request(method, path, payload, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw) if raw else {}


def _stored(srv):
    db = LifeEventsDB(srv.db_path)
    try:
        return db.events_between(*WIDE_WINDOW)
    finally:
        db.close()


def test_rejects_wrong_token_and_writes_nothing(server):
    status, _ = _request(
        server, "POST", "/ingest/saa/wrong-token", {"event": "sleep_tracking_started"}
    )

    assert status == 401
    assert _stored(server) == []


def test_unknown_source_is_404(server):
    status, _ = _request(server, "POST", f"/ingest/nope/{TOKEN}", {"event": "x"})

    assert status == 404
    assert _stored(server) == []


def test_malformed_path_is_404(server):
    status, _ = _request(server, "POST", "/ingest/saa", {"event": "x"})

    assert status == 404


def test_stores_saa_event(server):
    status, body = _request(
        server, "POST", f"/ingest/saa/{TOKEN}", {"event": "sleep_tracking_started"}
    )

    assert status == 200
    assert body == {"stored": True}
    events = _stored(server)
    assert len(events) == 1
    assert events[0].source == "sleep_as_android"
    assert events[0].event_type == "sleep_tracking_started"


def test_duplicate_delivery_is_idempotent(server):
    # Client-stamped event: the dedupe ID is independent of receive time.
    body = {"event": "screen_on", "ts": "2026-06-13T22:15:03"}

    _, first = _request(server, "POST", f"/ingest/macrodroid/{TOKEN}", body)
    _, second = _request(server, "POST", f"/ingest/macrodroid/{TOKEN}", body)

    assert first == {"stored": True}
    assert second == {"stored": False}
    assert len(_stored(server)) == 1


def test_bad_json_is_400(server):
    status, _ = _request(server, "POST", f"/ingest/saa/{TOKEN}", raw_body="{not json")

    assert status == 400
    assert _stored(server) == []


def test_missing_event_field_is_400(server):
    status, _ = _request(server, "POST", f"/ingest/saa/{TOKEN}", {"value1": "x"})

    assert status == 400
    assert _stored(server) == []


def test_healthz(server):
    status, body = _request(server, "GET", "/healthz")

    assert status == 200
    assert body == {"status": "ok"}


def test_redact_token_hides_secret():
    assert TOKEN not in redact_token(f"/ingest/saa/{TOKEN}")
    assert "<token>" in redact_token(f"/ingest/saa/{TOKEN}")
    # Also inside a full request log line, the form journald sees.
    line = f'"POST /ingest/saa/{TOKEN} HTTP/1.1" 200 -'
    assert TOKEN not in redact_token(line)
    assert redact_token("/healthz") == "/healthz"


@pytest.fixture
def wake_server(tmp_path):
    calls = []
    srv = build_server(
        bind="127.0.0.1",
        port=0,
        token=TOKEN,
        db_path=tmp_path / "life.sqlite3",
        default_tz=ZoneInfo("Asia/Singapore"),
        on_wake=lambda: calls.append(1),
    )
    srv.wake_calls = calls
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_stop_tracking_fires_wake(wake_server):
    _request(wake_server, "POST", f"/ingest/saa/{TOKEN}", {"event": "sleep_tracking_stopped"})

    assert len(wake_server.wake_calls) == 1


def test_alarm_dismiss_fires_wake(wake_server):
    _request(wake_server, "POST", f"/ingest/saa/{TOKEN}", {"event": "alarm_alert_dismiss"})

    assert len(wake_server.wake_calls) == 1


def test_sleep_start_does_not_fire_wake(wake_server):
    _request(wake_server, "POST", f"/ingest/saa/{TOKEN}", {"event": "sleep_tracking_started"})

    assert wake_server.wake_calls == []


def test_screen_event_does_not_fire_wake(wake_server):
    _request(
        wake_server, "POST", f"/ingest/macrodroid/{TOKEN}", {"event": "screen_on"}
    )

    assert wake_server.wake_calls == []


def test_bad_token_does_not_fire_wake(wake_server):
    _request(wake_server, "POST", "/ingest/saa/wrong", {"event": "sleep_tracking_stopped"})

    assert wake_server.wake_calls == []
