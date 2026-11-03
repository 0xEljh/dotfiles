import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from pr_maintenance.common import Blocked
from pr_maintenance.runner import Runner


class RunnerTests(unittest.TestCase):
    def test_unproved_runner_never_creates_session(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:4096"})
        with patch.object(runner, "api") as api:
            with self.assertRaisesRegex(Blocked, "pinned"):
                runner.check_contract(Path.cwd())
            api.assert_not_called()

    def test_pinned_cli_output_and_server_version_are_both_checked(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:49374", "runner_contract_verified": True,
                         "runner_version": "opencode v2.0.28-preserve.1", "runner_quiescence_argv": ["/trusted/probe"]})
        version = subprocess.CompletedProcess([], 0, b"opencode v2.0.28-preserve.1\n")
        help_output = subprocess.CompletedProcess([], 0, b"--session --server --model --agent --format")
        with patch("pr_maintenance.runner.subprocess.run", side_effect=[version, help_output]), \
             patch.object(runner, "api", return_value={"version": "2.0.28-preserve.1"}):
            runner.check_contract(Path.cwd())

    def test_create_session_uses_explicit_location_and_retains_id(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:4096"})
        session = {"id": "ses_test123", "agent": "worker", "model": {"providerID": "provider", "id": "model"},
                   "location": {"directory": "/owned"}, "metadata": {"attempt_id": "attempt", "spec_hash": "hash"}}
        with patch.object(runner, "api", return_value={"data": session}) as api:
            self.assertEqual(runner.create_session(Path("/owned"), "title", "worker", "provider/model", "ses_test123", {"attempt_id": "attempt", "spec_hash": "hash"}), "ses_test123")
            self.assertEqual(api.call_args.args[2]["location"], {"directory": "/owned"})
            self.assertEqual(api.call_args.args[2]["id"], "ses_test123")

    def test_existing_session_with_wrong_ownership_is_rejected(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:4096"})
        with patch.object(runner, "api", return_value={"data": {"id": "ses_test123"}}):
            with self.assertRaisesRegex(Blocked, "ownership"):
                runner.create_session(Path("/owned"), "title", "worker", "provider/model", "ses_test123", {"attempt_id": "attempt"})

    def test_server_normalized_default_variant_is_not_a_model_substitution(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:4096"})
        session = {"id": "ses_test123", "agent": "worker", "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "default"},
                   "location": {"directory": "/owned"}, "metadata": {"attempt_id": "attempt"}}
        with patch.object(runner, "api", return_value={"data": session}):
            self.assertEqual(runner.create_session(Path("/owned"), "title", "worker", "openai/gpt-6.1-sol", "ses_test123", {"attempt_id": "attempt"}), "ses_test123")
            with self.assertRaisesRegex(Blocked, "model"):
                runner.create_session(Path("/owned"), "title", "worker", "openai/gpt-6.1-sol#high", "ses_test123", {"attempt_id": "attempt"})

    def test_managed_connection_uses_existing_discovery_auth_not_explicit_url(self):
        server = "http://127.0.0.1:49374"
        runner = Runner({"opencode_server": server, "opencode_connection": "managed"})
        with patch.object(runner, "_managed_endpoint", return_value=server), patch("pr_maintenance.runner.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b'{"version":"2.0.28-preserve.1"}')
            self.assertEqual(runner.api("get", "/api/info")["version"], "2.0.28-preserve.1")
            self.assertNotIn("--server", run.call_args.args[0])
        with patch.object(runner, "_managed_endpoint", return_value="http://127.0.0.1:59999"), patch("pr_maintenance.runner.subprocess.run") as run:
            with self.assertRaisesRegex(Blocked, "endpoint"):
                runner.api("get", "/api/info")
            run.assert_not_called()

    def test_managed_discovery_failures_are_content_free_blocked_results(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:49374", "opencode_connection": "managed"})
        for error in (OSError("private diagnostic"), subprocess.TimeoutExpired("opencode", 15)):
            with self.subTest(error=type(error).__name__), patch("pr_maintenance.runner.subprocess.run", side_effect=error):
                with self.assertRaisesRegex(Blocked, "endpoint"):
                    runner._managed_endpoint()
        with patch("pr_maintenance.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"\xff")):
            with self.assertRaisesRegex(Blocked, "endpoint"):
                runner._managed_endpoint()

    def test_substantive_run_uses_cwd_and_explicit_pinned_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Runner({"opencode_server": "http://127.0.0.1:4096", "opencode_binary": "opencode"})
            with patch("pr_maintenance.runner.subprocess.run") as run:
                run.return_value.returncode = 0
                runner.run(root, "ses_test123", "provider/model", "worker", "work", root / "events.jsonl", 30)
                argv = run.call_args.args[0]
                self.assertEqual(run.call_args.kwargs["cwd"], root)
                self.assertEqual(run.call_args.kwargs["env"]["PWD"], str(root))
                self.assertEqual(run.call_args.kwargs["input"], b"work")
                for flag in ("--session", "--server", "--model", "--agent", "--format"):
                    self.assertIn(flag, argv)
                self.assertNotIn("--dir", argv)

    def test_idle_foreground_is_not_descendant_quiescence(self):
        runner = Runner({"opencode_server": "http://127.0.0.1:4096"})
        with patch.object(runner, "api", return_value={"data": {}}):
            self.assertFalse(runner.quiescent("ses_test123", Path.cwd()))

    def test_strict_probe_still_requires_descendant_proof(self):
        runner = Runner({"runner_contract_verified": True, "runner_quiescence_argv": ["/trusted/probe", "{session_id}"]})
        for descendants in (True, False, None):
            evidence = {"session_id": "ses_test123", "quiescent": True, "descendants_quiescent": descendants}
            with self.subTest(descendants=descendants), patch("pr_maintenance.runner.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps(evidence).encode())
                self.assertEqual(runner.quiescent("ses_test123", Path.cwd()), descendants is True)

    def test_review_requires_stored_bound_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.jsonl"
            path.write_text(json.dumps({"type": "text", "part": {"text": json.dumps({"decision": "approve", "candidate_sha": "a" * 40, "manifest_hash": "manifest", "findings": []})}}) + "\n")
            self.assertTrue(Runner.review_approved(path, "a" * 40, "manifest"))
            self.assertFalse(Runner.review_approved(path, "b" * 40, "manifest"))


class BestEffortRunnerTests(unittest.TestCase):
    def setUp(self):
        self.cwd = Path("/owned checkout")
        self.session_id = "ses_test123"
        self.config = {"opencode_server": "http://127.0.0.1:49374", "runner_version": "opencode v2.0.28-preserve.1",
                       "quiescence_mode": "best_effort", "best_effort_acknowledged": True}
        self.runner = Runner(self.config)
        self.active = "/api/session/active"
        self.inbox = f"/api/session/{self.session_id}/inbox"
        self.children = "/api/session"
        self.shell = "/api/shell"
        self.pty = "/api/pty"
        self.terminal = f"/api/experimental/session/{self.session_id}/terminal"
        self.responses = {
            self.active: {"data": {}}, self.inbox: {"data": []},
            self.children: {"data": [], "cursor": {"previous": None, "next": None}},
            self.shell: {"location": {"directory": str(self.cwd)}, "data": []},
            self.pty: {"location": {"directory": str(self.cwd)}, "data": []}, self.terminal: {"data": []},
        }

    def api(self, method, path, body=None, cwd=None):
        self.assertEqual(method, "get")
        self.assertIsNone(body)
        self.assertEqual(cwd, self.cwd)
        url = urlsplit(path)
        query = parse_qs(url.query)
        if url.path in (self.shell, self.pty):
            self.assertEqual(query, {"location[directory]": [str(self.cwd)]})
        elif url.path == self.children:
            self.assertEqual(query, {"parentID": [self.session_id], "limit": ["1"]})
        else:
            self.assertEqual(query, {})
        return self.responses[url.path]

    def quiet(self):
        with patch.object(self.runner, "api", side_effect=self.api):
            return self.runner.quiescent(self.session_id, self.cwd)

    def shell_row(self, status="running"):
        return {"id": "sh_test", "status": status, "command": "build", "cwd": str(self.cwd), "shell": "/bin/sh",
                "file": "/output", "pid": 123, "metadata": {}, "time": {"started": 1}}

    def terminal_row(self, status="running", persistent=False):
        row = {"id": "pty_test", "status": status, "title": "build", "command": "/bin/sh", "args": [],
               "cwd": str(self.cwd), "pid": 123}
        if persistent:
            row.update(sessionID=self.session_id, foregroundProcess=None, size={"cols": 80, "rows": 24},
                       output={"head": 0, "tail": 0})
        return row

    def test_explicit_boolean_acknowledgement_is_required(self):
        for acknowledgement in (None, False, "true", 1):
            config = dict(self.config, best_effort_acknowledged=acknowledgement, runner_contract_verified=True,
                          runner_quiescence_argv=["/trusted/probe"])
            runner = Runner(config)
            with self.subTest(acknowledgement=acknowledgement), \
                 patch("pr_maintenance.runner.subprocess.run") as run, patch.object(runner, "api") as api:
                with self.assertRaisesRegex(Blocked, "acknowledged"):
                    runner.check_contract(self.cwd)
                self.assertFalse(runner.quiescent(self.session_id, self.cwd))
                run.assert_not_called()
                api.assert_not_called()

    def test_pin_and_nonempty_server_are_required(self):
        for key in ("runner_version", "opencode_server"):
            for value in (None, "", " ", 1):
                runner = Runner(dict(self.config, **{key: value}))
                with self.subTest(key=key, value=value), patch("pr_maintenance.runner.subprocess.run") as run:
                    with self.assertRaises(Blocked):
                        runner.check_contract(self.cwd)
                    self.assertFalse(runner.quiescent(self.session_id, self.cwd))
                    run.assert_not_called()

    def test_best_effort_contract_does_not_require_trusted_probe(self):
        def run(argv, **kwargs):
            output = b"opencode v2.0.28-preserve.1\n" if argv[-1] == "--version" else b"--session --server --model --agent --format"
            return subprocess.CompletedProcess(argv, 0, output)

        with patch("pr_maintenance.runner.subprocess.run", side_effect=run), \
             patch.object(self.runner, "api", return_value={"version": "2.0.28-preserve.1"}):
            self.runner.check_contract(self.cwd)

    def test_both_modes_enforce_cli_server_versions_and_required_flags(self):
        for mode in ("strict", "best_effort"):
            runner = Runner(dict(self.config, quiescence_mode=mode, runner_contract_verified=True,
                                 runner_quiescence_argv=["/trusted/probe"]))
            for mismatch in ("cli", "server", "flags"):
                def run(argv, **kwargs):
                    if argv[-1] == "--version":
                        output = b"wrong" if mismatch == "cli" else b"opencode v2.0.28-preserve.1"
                    else:
                        output = b"--session --server --model --agent" if mismatch == "flags" else b"--session --server --model --agent --format"
                    return subprocess.CompletedProcess(argv, 0, output)

                info = {"version": "wrong" if mismatch == "server" else "2.0.28-preserve.1"}
                with self.subTest(mode=mode, mismatch=mismatch), patch("pr_maintenance.runner.subprocess.run", side_effect=run), \
                     patch.object(runner, "api", return_value=info):
                    with self.assertRaisesRegex(Blocked, "runner_version|flags"):
                        runner.check_contract(self.cwd)

    def test_unknown_modes_are_rejected_without_execution(self):
        for mode in ("observe", "", None, []):
            runner = Runner(dict(self.config, quiescence_mode=mode))
            with self.subTest(mode=mode), patch.object(runner, "api") as api, \
                 patch("pr_maintenance.runner.subprocess.run") as run:
                with self.assertRaisesRegex(Blocked, "unknown.*mode"):
                    runner.check_contract(self.cwd)
                self.assertFalse(runner.quiescent(self.session_id, self.cwd))
                api.assert_not_called()
                run.assert_not_called()

    def test_strict_remains_default_even_with_acknowledgement(self):
        config = {key: value for key, value in self.config.items() if key != "quiescence_mode"}
        runner = Runner(config)
        with patch.object(runner, "api", side_effect=self.api) as api:
            self.assertFalse(runner.quiescent(self.session_id, self.cwd))
            api.assert_not_called()

    def test_clear_observations_allow_best_effort_completion(self):
        self.assertTrue(self.quiet())

    def test_null_or_absent_cursor_links_allow_completion(self):
        for cursor in ({}, {"previous": None}, {"next": None}, {"previous": None, "next": None},
                       {"previous": "previous_page", "next": None}):
            with self.subTest(cursor=cursor), patch.dict(self.responses, {self.children: {"data": [], "cursor": cursor}}):
                self.assertTrue(self.quiet())

    def test_best_effort_uses_observations_even_when_a_strict_probe_is_configured(self):
        self.config.update(runner_contract_verified=True, runner_quiescence_argv=["/trusted/probe"])
        with patch("pr_maintenance.runner.subprocess.run") as run:
            self.assertTrue(self.quiet())
            run.assert_not_called()

    def test_idle_foreground_alone_is_insufficient(self):
        with patch.object(self.runner, "api", return_value={"data": {}}):
            self.assertFalse(self.runner.quiescent(self.session_id, self.cwd))

    def test_active_queued_or_child_work_blocks_even_for_completed_children(self):
        cases = ((self.active, {"data": {self.session_id: {"type": "running"}}}),
                 (self.inbox, {"data": [{"id": "queued_work", "type": "user"}]}),
                 (self.children, {"data": [{"id": "ses_child", "parentID": self.session_id, "time": {"completed": 1}}], "cursor": {}}),
                 (self.children, {"data": [], "cursor": {"next": "next_page"}}))
        for endpoint, response in cases:
            with self.subTest(endpoint=endpoint, response=response), patch.dict(self.responses, {endpoint: response}):
                self.assertFalse(self.quiet())

    def test_running_shell_or_terminal_blocks_regardless_of_metadata_ownership(self):
        shell = self.shell_row()
        shell["metadata"] = {"sessionID": "ses_someone_else"}
        for endpoint, row in ((self.shell, shell), (self.pty, self.terminal_row()),
                              (self.terminal, self.terminal_row(persistent=True))):
            with self.subTest(endpoint=endpoint), patch.dict(self.responses[endpoint], {"data": [row]}):
                self.assertFalse(self.quiet())

    def test_retained_exited_terminals_do_not_block_completion(self):
        self.responses[self.pty]["data"] = [self.terminal_row(status="exited")]
        self.responses[self.terminal]["data"] = [self.terminal_row(status="exited", persistent=True)]
        self.assertTrue(self.quiet())

    def test_unrelated_active_sessions_do_not_block_owned_checkout(self):
        self.responses[self.active] = {"data": {"ses_other": {"type": "running"}}}
        self.assertTrue(self.quiet())

    def test_malformed_response_envelopes_never_mean_quiet(self):
        for endpoint in self.responses:
            for response in (None, [], {}, {"data": None}, {"data": ""}):
                with self.subTest(endpoint=endpoint, response=response), patch.dict(self.responses, {endpoint: response}):
                    self.assertFalse(self.quiet())
            missing_data = {key: value for key, value in self.responses[endpoint].items() if key != "data"}
            wrong_data = [] if endpoint == self.active else {}
            for response in (missing_data, dict(self.responses[endpoint], data=wrong_data)):
                with self.subTest(endpoint=endpoint, response=response), patch.dict(self.responses, {endpoint: response}):
                    self.assertFalse(self.quiet())

    def test_missing_or_mismatched_location_and_child_cursor_block(self):
        for endpoint in (self.shell, self.pty):
            for location in (None, {}, {"directory": "/elsewhere"}, {"directory": str(self.cwd), "extra": True}):
                with self.subTest(endpoint=endpoint, location=location), patch.dict(self.responses[endpoint], {"location": location}):
                    self.assertFalse(self.quiet())
        for response in ({"data": []}, {"data": [], "cursor": None}, {"data": [], "cursor": []},
                         {"data": [], "cursor": {"next": ""}}, {"data": [], "cursor": {"next": False}},
                         {"data": [], "cursor": {"next": 0}}, {"data": [], "cursor": {"next": []}},
                         {"data": [], "cursor": {"previous": False}}, {"data": [], "cursor": {"previous": ""}},
                         {"data": [], "cursor": {"previous": []}}):
            with self.subTest(response=response), patch.dict(self.responses, {self.children: response}):
                self.assertFalse(self.quiet())

    def test_unknown_status_and_malformed_process_rows_block(self):
        for endpoint in (self.shell, self.pty, self.terminal):
            unknown = self.shell_row("unknown") if endpoint == self.shell else self.terminal_row("unknown", endpoint == self.terminal)
            for row in (None, [], {}, {"status": "exited"}, unknown):
                with self.subTest(endpoint=endpoint, row=row), patch.dict(self.responses[endpoint], {"data": [row]}):
                    self.assertFalse(self.quiet())
        for row in ({"type": "unknown"}, None, "running"):
            with self.subTest(active=row), patch.dict(self.responses[self.active], {"data": {"ses_other": row}}):
                self.assertFalse(self.quiet())

    def test_malformed_retained_terminal_fields_block_completion(self):
        for endpoint in (self.pty, self.terminal):
            changes = [{"id": "wrong"}, {"cwd": None}, {"args": None}, {"args": [1]}, {"pid": False},
                       {"pid": -1}, {"exitCode": -1}, {"exitCode": "0"}]
            if endpoint == self.terminal:
                changes += [{"sessionID": "ses_other"}, {"foregroundProcess": {}}, {"size": None},
                            {"size": {"cols": 0, "rows": 24}}, {"output": {"head": 0}},
                            {"output": {"head": False, "tail": 0}}]
            for change in changes:
                row = self.terminal_row("exited", endpoint == self.terminal)
                row.update(change)
                with self.subTest(endpoint=endpoint, change=change), patch.dict(self.responses[endpoint], {"data": [row]}):
                    self.assertFalse(self.quiet())

    def test_api_unavailability_and_errors_block_completion(self):
        for endpoint in self.responses:
            for error in (Blocked("unavailable"), OSError("unavailable"), subprocess.TimeoutExpired("api", 30), ValueError("invalid JSON")):
                def unavailable(method, path, body=None, cwd=None):
                    if urlsplit(path).path == endpoint:
                        raise error
                    return self.api(method, path, body, cwd)

                with self.subTest(endpoint=endpoint, error=type(error).__name__), \
                     patch.object(self.runner, "api", side_effect=unavailable):
                    self.assertFalse(self.runner.quiescent(self.session_id, self.cwd))

    def test_work_appearing_after_a_clear_snapshot_blocks_completion(self):
        def late_shell(method, path, body=None, cwd=None):
            response = self.api(method, path, body, cwd)
            if urlsplit(path).path == self.terminal:
                self.responses[self.shell]["data"] = [self.shell_row()]
            return response

        with patch.object(self.runner, "api", side_effect=late_shell):
            self.assertFalse(self.runner.quiescent(self.session_id, self.cwd))

    def test_late_activity_or_inbox_work_is_rechecked_after_full_snapshots(self):
        for endpoint, busy in ((self.active, {"data": {self.session_id: {"type": "running"}}}),
                               (self.inbox, {"data": [{"id": "late_work"}]})):
            observations = iter((self.responses[endpoint], self.responses[endpoint], busy))

            def late_work(method, path, body=None, cwd=None):
                response = self.api(method, path, body, cwd)
                return next(observations, busy) if urlsplit(path).path == endpoint else response

            with self.subTest(endpoint=endpoint), patch.object(self.runner, "api", side_effect=late_work):
                self.assertFalse(self.runner.quiescent(self.session_id, self.cwd))
