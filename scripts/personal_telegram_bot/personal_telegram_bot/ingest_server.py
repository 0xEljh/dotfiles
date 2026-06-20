"""Tailnet-only HTTP endpoint receiving phone life events.

Sleep as Android (native webhooks) and MacroDroid POST JSON to
/ingest/<source>/<token>. The firewall admits only tailscale0 traffic; the
token in the path guards against other tailnet devices (Sleep as Android
cannot set custom headers, so the path carries it — and logs redact it).
"""

from __future__ import annotations

import hmac
import json
import re
import signal
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from zoneinfo import ZoneInfo

from .life_events import (
    LifeEvent,
    LifeEventsDB,
    normalize_macrodroid,
    normalize_owntracks,
    normalize_phone,
    normalize_saa,
)

if TYPE_CHECKING:
    from .config import Config

MAX_BODY_BYTES = 64 * 1024
_TOKEN_PATTERN = re.compile(r"(/ingest/[^/\s\"]+/)[^/\s\"]+")

# Sleep as Android events that mean "I'm awake now" — either fires the morning
# digest, whichever arrives first (the digest dedupes per day).
WAKE_TRIGGER_EVENTS = {"sleep_tracking_stopped", "alarm_alert_dismiss"}

# Local-hour window [floor, ceiling) the wake-triggered digest may fire in.
# Pre-floor events (mid-night stirs, early alarm dismissals) and post-ceiling
# nap-stops are false positives; outside the window the noon fallback timer
# (telegram-bot.nix) sends the digest instead.
WAKE_GATE_HOUR_DEFAULT = 7
WAKE_GATE_HOUR_END_DEFAULT = 11


def redact_token(text: str) -> str:
    return _TOKEN_PATTERN.sub(r"\1<token>", text)


def wake_should_fire(
    event: LifeEvent, tz: ZoneInfo, gate_hour: int, gate_hour_end: int
) -> bool:
    """True if `event` is a wake trigger whose local observed-hour falls in
    [gate_hour, gate_hour_end). Gating on the event's own time (not now) means a
    late redelivery can't fire a digest at the wrong hour; storage is unaffected,
    so a gated stop still closes its sleep interval for later reducers."""
    if event.source != "sleep_as_android" or event.event_type not in WAKE_TRIGGER_EVENTS:
        return False
    hour = event.observed_at.astimezone(tz).hour
    return gate_hour <= hour < gate_hour_end


def _normalize(
    source_key: str, payload: dict, received_at: datetime, tz: ZoneInfo
) -> LifeEvent | None:
    if source_key == "saa":
        return normalize_saa(payload, received_at)
    if source_key == "macrodroid":
        return normalize_macrodroid(payload, received_at, default_tz=tz)
    if source_key == "phone":
        return normalize_phone(payload, received_at, default_tz=tz)
    if source_key == "owntracks":
        # May return None (region-less ping / non-place message): accepted, not stored.
        return normalize_owntracks(payload, received_at)
    raise KeyError(source_key)


SOURCE_KEYS = ("saa", "macrodroid", "phone", "owntracks")


class IngestHandler(BaseHTTPRequestHandler):
    server: "IngestServer"

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/healthz":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        parts = self.path.split("/")
        # Expected shape: ["", "ingest", "<source>", "<token>"]
        if len(parts) != 4 or parts[0] != "" or parts[1] != "ingest":
            self._respond(404, {"error": "not found"})
            return
        source_key, token = parts[2], parts[3]
        if not hmac.compare_digest(token, self.server.token):
            self._respond(401, {"error": "unauthorized"})
            return
        if source_key not in SOURCE_KEYS:
            self._respond(404, {"error": "unknown source"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._respond(400, {"error": "missing body"})
            return
        if length > MAX_BODY_BYTES:
            self._respond(413, {"error": "body too large"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            event = _normalize(
                source_key, payload, datetime.now(timezone.utc), self.server.default_tz
            )
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid payload"})
            return

        # A normalizer may legitimately return None (e.g. an OwnTracks region-less
        # ping): accept it with a 2xx so the phone doesn't retry, but store nothing.
        stored = False
        if event is not None:
            db = LifeEventsDB(self.server.db_path)
            try:
                stored = db.insert(event)
            finally:
                db.close()

            # Fire the wake hook regardless of dedupe, so a redelivery can still
            # retry a digest that failed the first time; the digest dedupes per day.
            # The hook MUST be non-blocking (production spawns a thread) so the
            # phone's webhook isn't held open on Notion/Telegram.
            if self.server.on_wake is not None and wake_should_fire(
                event,
                self.server.default_tz,
                self.server.wake_gate_hour,
                self.server.wake_gate_hour_end,
            ):
                self.server.on_wake()

        # OwnTracks wants a 2xx with a JSON array of commands; an empty array
        # means "nothing to do". Other sources get the simple stored flag.
        if source_key == "owntracks":
            self._respond(200, [])
        else:
            self._respond(200, {"stored": stored})

    def _respond(self, code: int, obj: object) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(
            "%s %s\n" % (self.address_string(), redact_token(format % args))
        )


class IngestServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        bind: str,
        port: int,
        token: str,
        db_path: Path | str,
        default_tz: ZoneInfo,
        on_wake: Callable[[], None] | None = None,
        wake_gate_hour: int = WAKE_GATE_HOUR_DEFAULT,
        wake_gate_hour_end: int = WAKE_GATE_HOUR_END_DEFAULT,
    ):
        self.token = token
        self.db_path = Path(db_path)
        self.default_tz = default_tz
        self.on_wake = on_wake
        self.wake_gate_hour = wake_gate_hour
        self.wake_gate_hour_end = wake_gate_hour_end
        # Fail fast at boot on a bad path, and create the schema once; request
        # threads then open short-lived connections (WAL handles concurrency).
        LifeEventsDB(self.db_path).close()
        super().__init__((bind, port), IngestHandler)


def build_server(
    bind: str,
    port: int,
    token: str,
    db_path: Path | str,
    default_tz: ZoneInfo,
    on_wake: Callable[[], None] | None = None,
    wake_gate_hour: int = WAKE_GATE_HOUR_DEFAULT,
    wake_gate_hour_end: int = WAKE_GATE_HOUR_END_DEFAULT,
) -> IngestServer:
    return IngestServer(
        bind, port, token, db_path, default_tz, on_wake, wake_gate_hour, wake_gate_hour_end
    )


def serve(cfg: "Config") -> None:
    if not cfg.life_ingest_token:
        raise SystemExit("LIFE_INGEST_TOKEN is not set (expected in bot.env)")

    def on_wake() -> None:
        # Run the digest off the request thread so the phone gets its 200 fast.
        def _run() -> None:
            try:
                from .digests import deliver_morning_digest

                sent = deliver_morning_digest(cfg, trigger="wake")
                print(
                    "wake-triggered morning digest sent"
                    if sent
                    else "wake-triggered digest skipped (already sent today)",
                    flush=True,
                )
            except Exception as exc:  # never crash the ingest server
                print(f"wake-triggered digest failed: {type(exc).__name__}: {exc}", flush=True)

        threading.Thread(target=_run, daemon=True).start()

    server = build_server(
        cfg.life_ingest_bind,
        cfg.life_ingest_port,
        cfg.life_ingest_token,
        cfg.life_db_path,
        cfg.tz,
        on_wake=on_wake,
        wake_gate_hour=cfg.wake_gate_hour,
        wake_gate_hour_end=cfg.wake_gate_hour_end,
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    host, port = server.server_address[:2]
    print(f"life-event ingest listening on {host}:{port} (db: {cfg.life_db_path})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
