import concurrent.futures
import contextlib
import json
import random
import subprocess
import threading
import time
from unittest.mock import Mock, patch

from pr_maintenance.common import Blocked
from pr_maintenance.worker import Worker
from test_git_ops import GitOps, Repository
from test_worker import specification


class CancellationTests(Repository):
    def setUp(self):
        super().setUp()
        self.config = {
            "state_dir": str(self.root / "state"), "checkout_root": str(self.root / "checkouts"),
            "skills_dir": str(self.root / "skills"), "execution_enabled": True,
            "rollout_acknowledged": True, "quiescence_mode": "best_effort",
            "best_effort_acknowledged": True, "runner_version": "pinned",
            "opencode_server": "http://localhost:1234", "min_free_bytes": 0,
            "config_file": "/fixed/config.json", "worker_executable": "/fixed/bin/pr-worker",
        }
        skill = self.root / "skills" / "cleanup-basic"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Keep scope narrow.")
        self.spec = specification()
        self.spec["input_head_sha"] = self.spec["pr"]["head"]["sha"] = self.head
        self.spec["base_sha"] = self.spec["pr"]["base"]["sha"] = self.base
        self.attempt = self.spec["attempt_id"]
        self.worker = Worker(self.config)
        self.addCleanup(self.worker.close)
        with patch.object(self.worker, "_schedule"):
            self.worker.submit(self.spec)
        self.owned = GitOps(self.worker.checkout(self.spec), str(self.origin))
        self.checks = contextlib.ExitStack()
        self.addCleanup(self.checks.close)
        self.checks.enter_context(patch.object(self.worker, "git", return_value=self.owned))
        self.checks.enter_context(patch.object(self.worker, "live", return_value=self.spec["pr"]))
        self.checks.enter_context(patch.object(self.worker.runner, "check_contract"))
        self.available = self.checks.enter_context(patch.object(self.worker.runner, "check_available"))
        self.create = self.checks.enter_context(patch.object(self.worker.runner, "create_session",
            side_effect=lambda cwd, title, agent, model, session, metadata: session))
        self.run = self.checks.enter_context(patch.object(self.worker.runner, "run"))
        self.checks.enter_context(patch.object(self.worker.runner, "quiescent", return_value=False))
        self.checks.enter_context(patch.object(self.worker, "_systemctl", return_value=True))
        self.interrupt = self.checks.enter_context(patch.object(self.worker.runner, "interrupt"))

    def cancel(self):
        return self.worker.cancel(self.attempt, 2)

    def assert_no_prompt(self):
        self.run.assert_not_called()
        self.assertEqual(self.worker.execute(self.attempt)["state"], "cancel_requested")
        self.assertFalse(self.worker.status(self.attempt)["quiescent"])

    def test_cancellation_during_preparation_prevents_session_and_prompt(self):
        self.available.side_effect = lambda *args: self.cancel()
        self.worker.execute(self.attempt)
        self.create.assert_not_called()
        self.assert_no_prompt()

    def test_cancellation_after_session_allocation_prevents_prompt(self):
        def create(cwd, title, agent, model, session, metadata):
            self.cancel()
            return session
        self.create.side_effect = create
        self.worker.execute(self.attempt)
        self.create.assert_called_once()
        self.interrupt.assert_called()
        self.assert_no_prompt()

    def test_revoked_epoch_without_transport_cancellation_prevents_launch(self):
        self.available.side_effect = lambda *args: self.worker._update(self.attempt, authorization_epoch=2)
        result = self.worker.execute(self.attempt)
        self.create.assert_not_called()
        self.run.assert_not_called()
        self.assertEqual(result["state"], "inspection_required")
        self.assertFalse(result["quiescent"])

    def test_lost_session_create_response_retains_identity_and_never_retries(self):
        self.create.side_effect = subprocess.TimeoutExpired("create-session", 30)
        first = self.worker.execute(self.attempt)
        self.assertEqual(first["state"], "deadline_exceeded")
        self.assertIsNotNone(first["session_id"])
        self.assertFalse(first["quiescent"])
        other = Worker(self.config)
        self.addCleanup(other.close)
        self.assertEqual(other.execute(self.attempt)["session_id"], first["session_id"])
        self.create.assert_called_once()
        self.run.assert_not_called()
        with self.assertRaisesRegex(Blocked, "branch"):
            other.submit(specification())

    def test_cancellation_before_verification_prevents_process_start(self):
        self.worker._update(self.attempt, started=True, state="running", session_id="ses_coding")
        self.cancel()
        with patch("pr_maintenance.worker.subprocess.Popen", side_effect=AssertionError("cancelled verification started")) as spawn, self.assertRaises(Blocked):
            self.worker._verify(self.spec, "c" * 40)
        spawn.assert_not_called()

    def test_cancellation_between_verification_commands_prevents_next_process(self):
        self.worker._update(self.attempt, started=True, state="running", session_id="ses_coding")
        self.spec["verification_profile"] = [["first"], ["second"]]
        process = Mock(pid=987654)
        process.wait.side_effect = lambda **kwargs: (self.cancel() and 0)
        self.owned.path.mkdir(parents=True)
        with patch("pr_maintenance.worker.subprocess.Popen", return_value=process) as spawn, \
             patch("pr_maintenance.worker.os.killpg", side_effect=ProcessLookupError), self.assertRaises(Blocked):
            self.worker._verify(self.spec, "c" * 40)
        self.assertEqual(spawn.call_count, 1)

    def test_cancellation_before_review_prevents_review_session(self):
        self.worker._update(self.attempt, started=True, state="running", session_id="ses_coding",
                            candidate_sha="c" * 40, manifest={"paths": {"topic.txt": "related"}}, manifest_hash="scope")
        ops = Mock()
        ops.head.return_value = "c" * 40
        ops.diff.return_value = "candidate diff"
        (self.worker.artifacts(self.attempt) / "result.json").write_text(json.dumps({
            "outcome": "changed", "blockers": [], "summary": "candidate retained",
        }))
        with patch.object(self.worker, "git", return_value=ops), \
             patch.object(self.worker, "_check_scope", return_value=["topic.txt"]), \
             patch.object(self.worker, "_verify", side_effect=lambda *args: (self.cancel() and {"commands": []})) as verify, \
             self.assertRaises(Blocked):
            self.worker._finish_coding(self.spec)
        verify.assert_called_once()
        self.create.assert_not_called()
        self.run.assert_not_called()

    def test_cancellation_during_failure_classification_is_not_overwritten(self):
        self.available.side_effect = Blocked("model lookup failed")
        def observe(spec, data, **kwargs):
            other = Worker(self.config)
            try:
                with patch.object(other, "_systemctl", return_value=True):
                    other.cancel(self.attempt, 2)
            finally:
                other.close()
            return False
        with patch.object(self.worker, "_interrupted_quiet", side_effect=observe):
            result = self.worker.execute(self.attempt)
        self.assertEqual(result["state"], "cancel_requested")
        self.assertEqual(result["authorization_epoch"], 2)
        self.assertFalse(result["quiescent"])
        self.create.assert_not_called()
        self.run.assert_not_called()

    def test_jitter_duplicate_execution_and_cancel_winner_never_launch_prompt(self):
        preparing, cancelled = threading.Event(), threading.Event()
        def available(*args):
            preparing.set()
            self.assertTrue(cancelled.wait(10))
        def execute(index):
            time.sleep(random.uniform(0, .015))
            worker = Worker(self.config)
            try:
                return worker.execute(self.attempt)
            finally:
                worker.close()
        def cancel():
            self.assertTrue(preparing.wait(10))
            time.sleep(random.uniform(0, .015))
            worker = Worker(self.config)
            try:
                return worker.cancel(self.attempt, 2)
            finally:
                worker.close()
                cancelled.set()
        with patch.object(Worker, "git", return_value=self.owned), \
             patch.object(Worker, "live", return_value=self.spec["pr"]), \
             patch("pr_maintenance.runner.Runner.check_contract"), \
             patch("pr_maintenance.runner.Runner.check_available", side_effect=available), \
             patch("pr_maintenance.runner.Runner.create_session") as create, \
             patch("pr_maintenance.runner.Runner.run") as run, \
             patch.object(Worker, "_systemctl", return_value=True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                cancellation = pool.submit(cancel)
                list(pool.map(execute, range(48)))
                cancellation.result(timeout=10)
        create.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.worker.status(self.attempt)["state"], "cancel_requested")
