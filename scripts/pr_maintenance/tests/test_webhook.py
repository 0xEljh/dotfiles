import hashlib
import hmac
import http.client
import json
import random
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from pr_maintenance.webhook import Webhook, make_server, serve


SECRET = b"test-secret-never-read-from-environment"
DELIVERY = "32d0911b-70dc-4aa9-9483-76f51d351d04"
CONFIG = {"github_webhook_secret": SECRET, "github_installation_id": 34, "organization_id": 97932676}


def payload(**extra):
    return {"installation": {"id": 34}, "organization": {"id": 97932676}, "pull_request": {"id": 123}, "action": "opened", **extra}


def signed(body, delivery=DELIVERY, event="pull_request"):
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest(), "X-GitHub-Delivery": delivery, "X-GitHub-Event": event}


class WebhookTests(unittest.TestCase):
    def test_valid_duplicate_calls_transactional_callback_and_acknowledges(self):
        body = json.dumps(payload()).encode()
        callback = Mock(side_effect=[True, False])
        webhook = Webhook(CONFIG, callback, clock=lambda: 1234)
        self.assertEqual(webhook.handle(body, signed(body)), 202)
        self.assertEqual(webhook.handle(body, signed(body)), 200)
        callback.assert_called_with(DELIVERY, "pull_request", payload(), 1234)

    def test_bad_signature_is_rejected_before_json_parsing(self):
        callback = Mock()
        webhook = Webhook(CONFIG, callback)
        body = b"not JSON"
        headers = signed(body)
        headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
        with patch("pr_maintenance.webhook.json.loads") as parse:
            self.assertEqual(webhook.handle(body, headers), 401)
        parse.assert_not_called()
        callback.assert_not_called()

    def test_signature_covers_raw_bytes_not_reencoded_json(self):
        body = b'{"installation": {"id":34}, "organization":{"id":97932676}, "pull_request":{"id":123}}'
        webhook = Webhook(CONFIG, Mock(return_value=True))
        normalized = json.dumps(json.loads(body)).encode()
        self.assertEqual(webhook.handle(body, signed(normalized)), 401)
        self.assertEqual(webhook.handle(body, signed(body)), 202)

    def test_malformed_signed_json_and_oversized_body_are_not_applied(self):
        callback = Mock()
        webhook = Webhook(CONFIG | {"webhook_max_body": 128}, callback)
        self.assertEqual(webhook.handle(b"{", signed(b"{")), 400)
        body = b"x" * 129
        with patch("pr_maintenance.webhook.json.loads") as parse:
            self.assertEqual(webhook.handle(body, signed(body)), 413)
        parse.assert_not_called()
        callback.assert_not_called()

    def test_ids_must_match_and_delivery_must_be_a_bounded_uuid(self):
        for data, delivery in ((payload(installation={"id": 99}), DELIVERY), (payload(organization={"id": 99}), DELIVERY), (payload(), ""), (payload(), "x" * 1000), (payload(), "not-a-uuid")):
            with self.subTest(delivery=delivery[:40], data=data):
                callback = Mock()
                body = json.dumps(data).encode()
                webhook = Webhook(CONFIG, callback)
                self.assertIn(webhook.handle(body, signed(body, delivery)), (400, 403))
                callback.assert_not_called()

    def test_installation_removal_accepts_configured_organization_account(self):
        body = json.dumps({"installation": {"id": 34, "account": {"id": 97932676}}, "action": "deleted"}).encode()
        callback = Mock(return_value=True)
        webhook = Webhook(CONFIG, callback)
        self.assertEqual(webhook.handle(body, signed(body, event="installation")), 202)
        self.assertEqual(callback.call_args.args[1], "installation")

    def test_database_failure_is_not_a_false_acknowledgement(self):
        body = json.dumps(payload()).encode()
        webhook = Webhook(CONFIG, Mock(side_effect=RuntimeError("private database path")))
        self.assertEqual(webhook.handle(body, signed(body)), 503)

    def test_serve_uses_configured_loopback_listener_and_callback(self):
        config = CONFIG | {"webhook_host": "127.0.0.1", "webhook_port": 8123}
        apply = Mock()
        server = Mock()
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        with patch("pr_maintenance.webhook.make_server", return_value=server) as make:
            self.assertEqual(serve(config, apply), 0)
        make.assert_called_once_with(("127.0.0.1", 8123), config, apply)
        server.serve_forever.assert_called_once_with()

    def test_concurrent_duplicate_jitter_preserves_one_callback_commit(self):
        committed = set()
        lock = threading.Lock()

        def apply(delivery, event, data, received):
            time.sleep(random.Random(threading.get_ident()).random() / 1000)
            with lock:
                if delivery in committed:
                    return False
                committed.add(delivery)
                return True

        webhook = Webhook(CONFIG, apply)
        body = json.dumps(payload()).encode()
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: webhook.handle(body, signed(body)), range(80)))
        self.assertEqual(statuses.count(202), 1)
        self.assertEqual(statuses.count(200), 79)
        self.assertEqual(committed, {DELIVERY})

    def test_server_bounds_live_handlers_and_closes_overloaded_connections(self):
        entered = threading.Event()
        release = threading.Event()

        def apply(*args):
            entered.set()
            release.wait(3)
            return True

        server = make_server(("127.0.0.1", 0), CONFIG | {"webhook_max_handlers": 1}, apply)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = json.dumps(payload()).encode()

        def send():
            connection = http.client.HTTPConnection(*server.server_address, timeout=4)
            try:
                connection.request("POST", "/github/webhook", body, signed(body))
                response = connection.getresponse()
                response.read()
                return response.status
            except ConnectionResetError:
                # Rejecting before reading an overloaded request can reset TCP.
                # A bounded refusal is valid; accepting it as work is not.
                return "connection_reset"
            finally:
                connection.close()

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                active = pool.submit(send)
                self.assertTrue(entered.wait(2))
                self.assertIn(send(), (503, "connection_reset"))
                release.set()
                self.assertEqual(active.result(timeout=3), 202)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
