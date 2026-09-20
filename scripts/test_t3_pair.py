import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nixos-config/packages/t3-pair"))
import t3_pair


class PairTests(unittest.TestCase):
    def setUp(self):
        self.invocation = "a" * 32
        self.active = True
        self.dns = "renamed.example.ts.net."
        self.port = 443
        self.runtime = {"version": 1, "origin": "http://127.0.0.1:3773"}
        self.mapping = {
            "TCP": {"443": {"HTTPS": True}},
            "Web": {"renamed.example.ts.net:443": {
                "Handlers": {"/": {"Proxy": self.runtime["origin"]}}
            }},
        }
        self.auth = {
            "authenticated": False,
            "auth": {
                "policy": "remote-reachable",
                "bootstrapMethods": ["one-time-token"],
                "sessionMethods": ["browser-session-cookie", "bearer-access-token"],
                "sessionCookieName": "t3.session",
            },
        }
        self.now = datetime.now(timezone.utc)
        self.issued = {
            "id": "test-id", "credential": "test-secret", "label": "telegram",
            "scopes": ["orchestration:read"],
            "pairUrl": "https://renamed.example.ts.net/pair#token=test-secret",
            "expiresAt": (self.now + timedelta(minutes=5)).isoformat(),
        }
        self.calls = []
        self.http_calls = []
        self.clock = 0.0
        self.before_mint = None
        self.after_mint = None
        self.process_error = None
        self.runner = self.enterContext(patch("t3_pair.subprocess.run", side_effect=self.run_process))
        self.enterContext(patch("t3_pair.Path.read_text", side_effect=lambda **kw: json.dumps(self.runtime)))
        self.opener = MagicMock()
        self.opener.open.side_effect = self.open_http
        self.enterContext(patch("t3_pair.urllib.request.build_opener", return_value=self.opener))
        self.enterContext(patch("t3_pair.time.monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch("t3_pair.time.sleep", side_effect=self.sleep))
        self.enterContext(patch("t3_pair.os.getuid", return_value=1234))

    def sleep(self, seconds):
        self.clock += seconds

    def run_process(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 30)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["env"]["XDG_RUNTIME_DIR"], "/run/user/1234")
        self.assertEqual(kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1234/bus")
        self.assertEqual(kwargs["env"]["HOME"], "/home/test")
        if self.process_error:
            raise self.process_error
        if argv[:3] == ["systemctl", "--user", "show"]:
            output = f"ActiveState={'active' if self.active else 'inactive'}\nInvocationID={self.invocation}\n"
        elif argv == ["systemctl", "--user", "start", "t3-serve.service"]:
            self.active = True
            output = ""
        elif argv == ["tailscale", "status", "--json"]:
            output = json.dumps({"Self": {"DNSName": self.dns}})
        elif argv == ["tailscale", "serve", "status", "--json"]:
            output = json.dumps(self.mapping)
        elif argv[0] == "npx":
            if self.after_mint:
                self.after_mint()
            output = json.dumps(self.issued)
        else:
            self.fail(f"Unexpected subprocess: {argv}")
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="ignored-secret")

    def open_http(self, request, *, timeout):
        self.http_calls.append(request.full_url)
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 5)
        response = MagicMock()
        response.status = 200
        response.headers.get_content_type.return_value = "application/json"
        response.read.return_value = json.dumps(self.auth).encode()
        response.geturl.return_value = request.full_url
        response.__enter__.return_value = response
        if self.before_mint:
            self.before_mint()
        return response

    def cli(self, command):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = t3_pair.main([
                "--package-spec", "t3@pinned", "--port", str(self.port),
                "--home", "/home/test", command,
            ])
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, command="status", code=1, message="unavailable"):
        result, stdout, stderr = self.cli(command)
        self.assertEqual(result, code)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, f"t3-pair: {message}\n")

    def test_status_ready_is_read_only_and_resolves_dns(self):
        code, stdout, stderr = self.cli("status")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), {
            "invocationId": self.invocation, "url": "https://renamed.example.ts.net",
        })
        self.assertEqual(self.http_calls, ["http://127.0.0.1:3773/api/auth/session"])
        self.assertFalse(any(call[0][0] == "npx" or "start" in call[0] for call in self.calls))

    def test_status_inactive_does_not_start_or_probe(self):
        self.active = False
        self.assert_failure()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.http_calls, [])

    def test_nonstandard_https_port(self):
        self.port = 8443
        self.mapping["TCP"]["8443"] = self.mapping["TCP"].pop("443")
        self.mapping["Web"]["renamed.example.ts.net:8443"] = self.mapping["Web"].pop("renamed.example.ts.net:443")
        code, stdout, _ = self.cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["url"], "https://renamed.example.ts.net:8443")

    def test_tailscale_loopback_auth_policy_is_ready(self):
        self.auth["auth"]["policy"] = "loopback-browser"
        self.assertEqual(self.cli("status")[0], 0)

    def test_wrong_backend_or_missing_https_mapping_fails(self):
        original = copy.deepcopy(self.mapping)
        for change in ("backend", "https", "route", "hostname"):
            with self.subTest(change=change):
                self.mapping = copy.deepcopy(original)
                if change == "backend":
                    self.mapping["Web"]["renamed.example.ts.net:443"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8779"
                elif change == "https":
                    self.mapping["TCP"]["443"]["HTTPS"] = False
                elif change == "route":
                    self.mapping["Web"]["renamed.example.ts.net:443"]["Handlers"]["/api/"] = {"Proxy": "http://127.0.0.1:8779"}
                else:
                    self.dns = "other.example.ts.net."
                self.assert_failure()
        self.assertEqual(self.http_calls, [])

    def test_arbitrary_http_200_is_not_ready(self):
        for auth in ({}, {"ok": True}, {"authenticated": False},
                     {**self.auth, "auth": {**self.auth["auth"], "policy": "unsafe-no-auth"}},
                     {**self.auth, "auth": {**self.auth["auth"], "bootstrapMethods": []}}):
            with self.subTest(auth=auth):
                self.auth = auth
                self.assert_failure()

    def test_create_starts_once_and_uses_exact_pinned_offline_cli(self):
        self.active = False
        code, stdout, stderr = self.cli("create")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), {
            "invocationId": self.invocation, "url": "https://renamed.example.ts.net",
            "pairUrl": self.issued["pairUrl"], "expiresAt": self.issued["expiresAt"],
        })
        commands = [call[0] for call in self.calls]
        self.assertEqual(commands.count(["systemctl", "--user", "start", "t3-serve.service"]), 1)
        self.assertIn([
            "npx", "--offline", "--yes", "t3@pinned", "auth", "pairing", "create",
            "--base-dir", "/home/test/.t3", "--ttl", "5m", "--label", "telegram",
            "--base-url", "https://renamed.example.ts.net", "--json",
        ], commands)

    def test_create_active_does_not_start_or_restart(self):
        self.assertEqual(self.cli("create")[0], 0)
        self.assertFalse(any("start" in call[0] or "restart" in call[0] for call in self.calls))

    def test_create_waits_for_readiness_but_never_mints_when_unready(self):
        self.auth = {}
        self.assert_failure("create")
        self.assertEqual(self.clock, 60)
        self.assertFalse(any(call[0][0] == "npx" or "start" in call[0] for call in self.calls))

    def test_create_recovers_when_backend_becomes_ready(self):
        original = self.open_http
        attempts = 0

        def warming_up(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("private-detail")
            return original(*args, **kwargs)

        self.opener.open.side_effect = warming_up
        self.assertEqual(self.cli("create")[0], 0)
        self.assertEqual(attempts, 3)
        self.assertEqual(self.clock, 2)

    def test_restart_before_or_after_mint_is_retryable_and_silent(self):
        for moment in ("before_mint", "after_mint"):
            with self.subTest(moment=moment):
                self.invocation = "a" * 32
                self.calls.clear()
                self.before_mint = self.after_mint = None
                setattr(self, moment, lambda: setattr(self, "invocation", "b" * 32))
                self.assert_failure("create", code=75, message="retryable")
                self.assertEqual(any(c[0][0] == "npx" for c in self.calls), moment == "after_mint")

    def test_status_restart_during_probe_is_retryable(self):
        self.before_mint = lambda: setattr(self, "invocation", "b" * 32)
        self.assert_failure(code=75, message="retryable")

    def test_invalid_pairing_output_never_escapes(self):
        original = copy.deepcopy(self.issued)
        changes = [
            {"pairUrl": "https://wrong.example/pair#token=test-secret"},
            {"pairUrl": "http://renamed.example.ts.net/pair#token=test-secret"},
            {"pairUrl": "https://renamed.example.ts.net/other#token=test-secret"},
            {"pairUrl": "https://renamed.example.ts.net/pair?token=test-secret"},
            {"pairUrl": "https://renamed.example.ts.net/pair#token=wrong"},
            {"pairUrl": "https://renamed.example.ts.net/pair#token=test-secret&extra=1"},
            {"pairUrl": "https://renamed.example.ts.net/pair#token=test-secret&token=test-secret"},
            {"credential": ""}, {"expiresAt": "secret-not-a-date"},
            {"expiresAt": (self.now - timedelta(seconds=1)).isoformat()},
            {"expiresAt": (self.now + timedelta(hours=1)).isoformat()},
            {"expiresAt": self.now.replace(tzinfo=None).isoformat()},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.issued = {**original, **change}
                self.assert_failure("create", message="invalid-response")

    def test_subprocess_errors_never_leak_output_or_arguments(self):
        for error in (
            subprocess.CalledProcessError(1, ["secret-argument"], output="secret-output", stderr="secret-error"),
            subprocess.TimeoutExpired(["secret-argument"], 5, output="secret-output"),
        ):
            with self.subTest(error=type(error).__name__):
                self.process_error = error
                self.assert_failure()

    def test_missing_runtime_fails_without_http(self):
        with patch.object(Path, "read_text", side_effect=FileNotFoundError("private-path")):
            self.assert_failure()
        self.assertEqual(self.http_calls, [])

    def test_http_errors_redirects_and_non_json_are_not_ready(self):
        original = self.open_http
        for problem in ("status", "redirect", "content-type", "json", "timeout"):
            with self.subTest(problem=problem):
                def bad_response(*args, **kwargs):
                    if problem == "timeout":
                        raise TimeoutError("private-detail")
                    response = original(*args, **kwargs)
                    if problem == "status":
                        response.status = 401
                    elif problem == "redirect":
                        response.geturl.return_value = "https://other.example/api/auth/session"
                    elif problem == "content-type":
                        response.headers.get_content_type.return_value = "text/html"
                    else:
                        response.read.return_value = b"<html>private-detail</html>"
                    return response

                self.opener.open.side_effect = bad_response
                self.assert_failure()

    def test_mint_failure_or_malformed_json_does_not_leak_secrets(self):
        original = self.run_process
        for problem in ("failure", "timeout", "json"):
            with self.subTest(problem=problem):
                def bad_cli(argv, **kwargs):
                    if argv[0] != "npx":
                        return original(argv, **kwargs)
                    if problem == "failure":
                        raise subprocess.CalledProcessError(1, argv, output="test-secret", stderr="private-detail")
                    if problem == "timeout":
                        raise subprocess.TimeoutExpired(argv, 30, output="test-secret")
                    return subprocess.CompletedProcess(argv, 0, stdout="test-secret", stderr="private-detail")

                self.runner.side_effect = bad_cli
                self.assert_failure("create", message="invalid-response" if problem == "json" else "unavailable")

    def test_missing_invocation_and_invalid_dns_fail_closed(self):
        for invocation in ("", "0" * 32, "private-detail"):
            with self.subTest(invocation=invocation):
                self.invocation = invocation
                self.assert_failure()
        self.invocation = "a" * 32
        for dns in ("", "host.example/pair", "host.example:443", "user@host.example"):
            with self.subTest(dns=dns):
                self.dns = dns
                self.assert_failure()


if __name__ == "__main__":
    unittest.main()
