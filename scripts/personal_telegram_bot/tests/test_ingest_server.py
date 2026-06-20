import http.client
import json
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from personal_telegram_bot.ingest_server import build_server, redact_token, wake_should_fire
from personal_telegram_bot.life_events import LifeEvent, LifeEventsDB

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


def test_owntracks_transition_stored_and_returns_array(server):
    # OwnTracks expects a 2xx with a (possibly empty) JSON array of commands.
    status, body = _request(
        server,
        "POST",
        f"/ingest/owntracks/{TOKEN}",
        {"_type": "transition", "event": "enter", "desc": "Office", "tst": 1718340000},
    )

    assert status == 200
    assert body == []
    events = _stored(server)
    assert len(events) == 1
    assert events[0].source == "owntracks"
    assert events[0].event_type == "place_enter"
    assert events[0].state == "Office"


def test_owntracks_regionless_ping_accepted_but_not_stored(server):
    status, body = _request(
        server,
        "POST",
        f"/ingest/owntracks/{TOKEN}",
        {"_type": "location", "lat": 1.0, "lon": 2.0, "tst": 1718337600},
    )

    assert status == 200
    assert body == []
    assert _stored(server) == []


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
        # Open the gate fully: these tests exercise the trigger wiring, not the
        # time window, and must not flake when the suite runs outside 7–11am.
        wake_gate_hour=0,
        wake_gate_hour_end=24,
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


# --- wake_should_fire: the [floor, ceiling) local-hour gate ---

SGT = ZoneInfo("Asia/Singapore")


def _saa_event(kind, when):
    return LifeEvent(source="sleep_as_android", event_type=kind, observed_at=when)


def test_wake_fires_inside_window():
    e = _saa_event("sleep_tracking_stopped", datetime(2026, 6, 16, 7, 30, tzinfo=SGT))
    assert wake_should_fire(e, SGT, 7, 11)


def test_wake_floor_is_inclusive():
    e = _saa_event("alarm_alert_dismiss", datetime(2026, 6, 16, 7, 0, tzinfo=SGT))
    assert wake_should_fire(e, SGT, 7, 11)


def test_wake_blocked_before_floor():
    e = _saa_event("sleep_tracking_stopped", datetime(2026, 6, 16, 3, 0, tzinfo=SGT))
    assert not wake_should_fire(e, SGT, 7, 11)


def test_wake_predawn_alarm_dismiss_blocked():
    e = _saa_event("alarm_alert_dismiss", datetime(2026, 6, 16, 5, 0, tzinfo=SGT))
    assert not wake_should_fire(e, SGT, 7, 11)


def test_wake_ceiling_is_exclusive():
    # 11:00 belongs to the noon fallback, not the wake path.
    e = _saa_event("sleep_tracking_stopped", datetime(2026, 6, 16, 11, 0, tzinfo=SGT))
    assert not wake_should_fire(e, SGT, 7, 11)


def test_wake_late_morning_nap_blocked():
    e = _saa_event("sleep_tracking_stopped", datetime(2026, 6, 16, 13, 0, tzinfo=SGT))
    assert not wake_should_fire(e, SGT, 7, 11)


def test_wake_gate_uses_local_tz_not_naive_utc():
    # 22:30 UTC == 06:30 SGT (+8): below the 7am floor once converted, so blocked.
    early = _saa_event(
        "sleep_tracking_stopped", datetime(2026, 6, 16, 22, 30, tzinfo=timezone.utc)
    )
    assert not wake_should_fire(early, SGT, 7, 11)
    # 00:30 UTC == 08:30 SGT: inside the window.
    inside = _saa_event(
        "sleep_tracking_stopped", datetime(2026, 6, 16, 0, 30, tzinfo=timezone.utc)
    )
    assert wake_should_fire(inside, SGT, 7, 11)


def test_wake_non_trigger_event_never_fires():
    e = _saa_event("sleep_tracking_started", datetime(2026, 6, 16, 8, 0, tzinfo=SGT))
    assert not wake_should_fire(e, SGT, 7, 11)


@pytest.fixture
def gated_wake_server(tmp_path):
    # Empty window [0, 0): nothing ever fires, regardless of wall clock — proves
    # the gate is wired into do_POST (the open-gate path is covered by wake_server).
    calls = []
    srv = build_server(
        bind="127.0.0.1",
        port=0,
        token=TOKEN,
        db_path=tmp_path / "life.sqlite3",
        default_tz=ZoneInfo("Asia/Singapore"),
        on_wake=lambda: calls.append(1),
        wake_gate_hour=0,
        wake_gate_hour_end=0,
    )
    srv.wake_calls = calls
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_gate_blocks_live_stop_but_still_stores(gated_wake_server):
    _request(
        gated_wake_server, "POST", f"/ingest/saa/{TOKEN}", {"event": "sleep_tracking_stopped"}
    )

    assert gated_wake_server.wake_calls == []
    # Gating suppresses the digest, not the event: the stop is still stored so
    # the sleep reducer can close its interval; only the prompt is deferred.
    assert len(_stored(gated_wake_server)) == 1
