"""Signed ingress only. The callback owns one short, durable DB transaction."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn


def _id(value, field: str) -> int:
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"{field} must be a positive numeric GitHub ID")
    return int(value)


class Webhook:
    """Validate raw deliveries without any GitHub, worker, or bot calls.

    ``apply(delivery_id, event, payload, received_at)`` must atomically record the
    delivery and accepted state update, returning True for new work or False for
    an already committed delivery. It must raise on rollback/failure and must
    not perform external calls. Keep its lock timeout short enough to finish
    within GitHub's ten-second deadline; schedule all other work afterward.
    """

    def __init__(self, config: dict, apply, *, clock=None):
        self.installation_id = _id(config.get("github_installation_id") or os.environ.get("GITHUB_INSTALLATION_ID"), "github_installation_id")
        self.organization_id = _id(config.get("organization_id", 97932676), "organization_id")
        secret = config.get("github_webhook_secret")
        if secret is None:
            filename = config.get("github_webhook_secret_file") or os.environ.get("GITHUB_WEBHOOK_SECRET_FILE")
            secret = Path(filename).read_bytes().rstrip(b"\r\n") if filename else os.environ.get("GITHUB_WEBHOOK_SECRET")
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("configure a nonempty GitHub webhook secret")
        self._secret = secret
        self.max_body = int(config.get("webhook_max_body", 1024 * 1024))
        if self.max_body <= 0:
            raise ValueError("webhook_max_body must be positive")
        self._apply = apply
        self._clock = clock or time.time

    def handle(self, body: bytes, headers, *, received_at: float | None = None) -> int:
        if len(body) > self.max_body:
            return 413
        values = {key.lower(): value for key, value in headers.items()}
        signature = values.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature) or not hmac.compare_digest(expected, signature):
            return 401
        # Delivery metadata and JSON are intentionally examined only after HMAC.
        delivery = values.get("x-github-delivery", "")
        if not isinstance(delivery, str) or len(delivery) != 36:
            return 400
        try:
            normalized_delivery = str(uuid.UUID(delivery))
        except ValueError:
            return 400
        if normalized_delivery != delivery.lower():
            return 400
        event = values.get("x-github-event", "")
        if not isinstance(event, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event):
            return 400
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return 400
        if not isinstance(payload, dict):
            return 400
        installation = payload.get("installation")
        if not isinstance(installation, dict) or type(installation.get("id")) is not int or installation["id"] != self.installation_id:
            return 403
        organization = payload.get("organization")
        organization_id = organization.get("id") if isinstance(organization, dict) else None
        if organization_id is None and event in {"installation", "installation_repositories", "ping"}:
            account = installation.get("account")
            organization_id = account.get("id") if isinstance(account, dict) else None
        if type(organization_id) is not int or organization_id != self.organization_id:
            return 403
        if event == "pull_request":
            pull = payload.get("pull_request")
            if not isinstance(pull, dict) or type(pull.get("id")) is not int or pull["id"] <= 0:
                return 400
        if event not in {"pull_request", "installation", "installation_repositories", "ping"}:
            return 204
        try:
            accepted = self._apply(normalized_delivery, event, payload, self._clock() if received_at is None else received_at)
        except Exception:
            # Never log private payloads, DB paths, or callback exception text.
            return 503
        if type(accepted) is not bool:
            return 503
        return 202 if accepted else 200


class _BoundedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, *, max_handlers: int, read_timeout: float):
        self._slots = threading.BoundedSemaphore(max_handlers)
        self._read_timeout = read_timeout
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self._read_timeout)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Default socketserver exception logging can expose callback internals.
        pass


def handler_class(config: dict, apply):
    webhook = Webhook(config, apply)
    path = config.get("webhook_path", "/github/webhook")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _reply(self, status: int):
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
            except OSError:
                pass

        def do_POST(self):
            received_at = webhook._clock()
            if self.path != path:
                self._reply(404)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding"):
                self._reply(400)
                return
            if not lengths:
                self._reply(411)
                return
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0]):
                self._reply(400)
                return
            for header in ("X-Hub-Signature-256", "X-GitHub-Delivery", "X-GitHub-Event"):
                if len(self.headers.get_all(header, [])) != 1:
                    self._reply(400)
                    return
            length = int(lengths[0])
            if length > webhook.max_body:
                self._reply(413)
                return
            try:
                body = self.rfile.read(length)
            except (OSError, socket.timeout):
                self._reply(408)
                return
            if len(body) != length:
                self._reply(400)
                return
            self._reply(webhook.handle(body, self.headers, received_at=received_at))

        def do_GET(self):
            self._reply(405)

    return Handler


def make_server(address: tuple[str, int], config: dict, apply) -> HTTPServer:
    """Serve behind HTTPS ingress with ingress rate/body/read-time limits."""
    max_handlers = int(config.get("webhook_max_handlers", 4))
    read_timeout = float(config.get("webhook_read_timeout", 3))
    if max_handlers <= 0 or not 0 < read_timeout <= 8:
        raise ValueError("webhook handlers must be positive and read timeout in (0, 8]")
    return _BoundedServer(address, handler_class(config, apply), max_handlers=max_handlers, read_timeout=read_timeout)


def serve(config: dict, apply) -> int:
    address = (config.get("webhook_host", "127.0.0.1"), int(config.get("webhook_port", 8766)))
    with make_server(address, config, apply) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
