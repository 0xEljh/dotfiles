import copy
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pr_maintenance import common
from pr_maintenance.common import Blocked
from pr_maintenance.worker import Worker
from test_git_ops import GitOps, Repository, git
from test_worker import specification


class StartupFixture:
    def configure_worker(self, root, **changes):
        self.config = {
            "state_dir": str(root / "state"),
            "checkout_root": str(root / "checkouts"),
            "skills_dir": str(root / "skills"),
            "execution_enabled": True,
            "rollout_acknowledged": True,
            "quiescence_mode": "best_effort",
            "best_effort_acknowledged": True,
            "runner_version": "opencode v2-test",
            "opencode_server": "http://127.0.0.1:1",
            "runner_contract_verified": True,
            "runner_quiescence_argv": ["/not-executed/session-proof"],
            "config_file": str(root / "worker.json"),
            "worker_executable": str(root / "bin" / "pr-worker"),
            "min_free_bytes": 0,
        }
        self.config.update(changes)
        skill = Path(self.config["skills_dir"]) / "cleanup-basic"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Keep maintenance within the PR's scope.\n")
        self.worker = Worker(self.config)
        self.addCleanup(self.worker.close)
        self.spec = specification()
        self.schedule = self.mock_method(self.worker, "_schedule")
        self.systemctl = self.mock_method(self.worker, "_systemctl")
        self.contract = self.mock_method(self.worker.runner, "check_contract")
        self.available = self.mock_method(self.worker.runner, "check_available")
        self.create = self.mock_method(self.worker.runner, "create_session")
        self.run = self.mock_method(self.worker.runner, "run")
        self.api = self.mock_method(self.worker.runner, "api")
        self.interrupt = self.mock_method(self.worker.runner, "interrupt")
        self.quiescent = self.mock_method(self.worker.runner, "quiescent", return_value=False)
        self.github = self.mock_method(self.worker, "_github", side_effect=self.github_response)
        self.external = (self.contract, self.available, self.create, self.run, self.api,
                         self.interrupt, self.quiescent, self.github, self.systemctl)

    def mock_method(self, target, name, **kwargs):
        mocked = patch.object(target, name, **kwargs)
        result = mocked.start()
        self.addCleanup(mocked.stop)
        return result

    def github_response(self, endpoint):
        if endpoint == "repositories/10":
            return copy.deepcopy(self.spec["pr"]["head"]["repo"])
        if endpoint == "repos/EigenformAI/example/pulls/1":
            return copy.deepcopy(self.live_pr)
        if endpoint == "repos/EigenformAI/example/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.live_base_sha}}
        raise AssertionError("unexpected GitHub read: " + endpoint)

    def submit(self):
        self.live_pr = copy.deepcopy(self.spec["pr"])
        self.live_base_sha = self.live_pr["base"]["sha"]
        return self.worker.submit(self.spec)

    def assert_no_session(self):
        for boundary in (self.create, self.run, self.api, self.interrupt, self.quiescent):
            boundary.assert_not_called()

    def assert_clean(self, result):
        self.assertEqual(result["state"], "blocked")
        self.assertIs(result["quiescent"], True)
        self.assertEqual(result["publication_state"], "not_published")
        self.assertNotIn("candidate_sha", result)
        self.assertNotIn("session_id", result)
        self.assertNotIn("review_session_id", result)
        self.assertNotIn("retryable_reason", result)
        self.assert_no_session()

    def assert_held(self, result, state="inspection_required"):
        self.assertEqual(result["state"], state)
        self.assertIs(result["quiescent"], False)
        self.assertEqual(result["publication_state"], "not_published")
        with self.assertRaisesRegex(Blocked, "branch"):
            self.worker.submit(dict(self.spec, attempt_id=str(uuid.uuid4())))

    def assert_released(self):
        following = dict(self.spec, attempt_id=str(uuid.uuid4()))
        self.assertEqual(self.worker.submit(following)["state"], "accepted")

    def assert_repeat_inert(self, result):
        counts = [boundary.call_count for boundary in self.external]
        repeated = self.worker.execute(self.spec["attempt_id"])
        if result["state"] == "deadline_exceeded":
            self.assertIn(repeated["state"], ("deadline_exceeded", "inspection_required"))
        else:
            self.assertEqual(repeated["state"], result["state"])
        self.assertEqual(repeated["quiescent"], result["quiescent"])
        self.assertEqual(repeated["publication_state"], result["publication_state"])
        self.assertEqual(repeated.get("started_at"), result.get("started_at"))
        self.assertEqual([boundary.call_count for boundary in self.external], counts)
        if result["state"] == "blocked":
            self.assertEqual(repeated, result)


class LocalStartupTests(StartupFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assert_local_rejection(self, **changes):
        self.configure_worker(self.root, **changes)
        self.submit()
        with patch("pr_maintenance.worker.subprocess.run") as process, \
             patch("pr_maintenance.worker.subprocess.Popen") as spawn:
            result = self.worker.execute(self.spec["attempt_id"])
            self.assert_clean(result)
            self.assert_repeat_inert(result)
            process.assert_not_called()
            spawn.assert_not_called()
        for boundary in self.external:
            boundary.assert_not_called()
        self.assertFalse(self.worker.checkout(self.spec).exists())
        self.assert_released()

    def test_disabled_execution_is_terminal_without_external_tools(self):
        self.assert_local_rejection(execution_enabled=False)

    def test_missing_rollout_acknowledgement_is_terminal_without_external_tools(self):
        self.assert_local_rejection(rollout_acknowledged=False)

    def test_unacknowledged_best_effort_is_rejected_before_runner_checks(self):
        self.assert_local_rejection(best_effort_acknowledged=False)

    def test_missing_runner_pin_is_rejected_before_runner_checks(self):
        self.assert_local_rejection(runner_version="")

    def test_missing_server_is_rejected_before_runner_checks(self):
        self.assert_local_rejection(opencode_server="")

    def test_unknown_quiescence_mode_is_rejected_before_runner_checks(self):
        self.assert_local_rejection(quiescence_mode="unknown")

    def test_assurance_change_after_acceptance_is_a_clean_local_rejection(self):
        self.configure_worker(self.root)
        self.submit()
        self.config["quiescence_mode"] = "strict"
        with patch("pr_maintenance.worker.subprocess.run") as process:
            result = self.worker.execute(self.spec["attempt_id"])
            self.assert_clean(result)
            self.assert_repeat_inert(result)
            process.assert_not_called()
        for boundary in self.external:
            boundary.assert_not_called()
        self.assert_released()

    def test_insufficient_disk_is_terminal_without_external_tools(self):
        with patch("pr_maintenance.worker.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            self.assert_local_rejection(min_free_bytes=1)

    def test_first_external_check_observes_a_durable_started_attempt(self):
        self.configure_worker(self.root)
        self.submit()
        observed = []

        def check_contract(_):
            observer = Worker(self.config)
            try:
                status = observer.status(self.spec["attempt_id"])
            finally:
                observer.close()
            self.assertEqual(status["state"], "running")
            self.assertIs(status["quiescent"], False)
            self.assertIsInstance(status["started_at"], float)
            observed.append(status["started_at"])
            raise Blocked("runner contract read failed")

        self.contract.side_effect = check_contract
        result = self.worker.execute(self.spec["attempt_id"])
        self.assertEqual(observed, [result["started_at"]])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.contract.assert_called_once()
        self.github.assert_not_called()
        self.assert_no_session()

    def test_uncertain_external_preflight_failures_remain_held(self):
        self.configure_worker(self.root)
        failures = ((Blocked("runner observation unavailable"), "inspection_required"),
                    (OSError("runner executable unavailable"), "inspection_required"),
                    (subprocess.TimeoutExpired(["mock-runner", "--version"], 15), "deadline_exceeded"))
        for index, (error, state) in enumerate(failures):
            with self.subTest(error=type(error).__name__):
                if index:
                    self.spec = specification()
                    self.spec["pr_id"] = self.spec["pr"]["id"] = 77 + index
                    self.spec["pr"]["head"]["ref"] = "topic-" + str(index)
                self.submit()
                self.contract.side_effect = error
                result = self.worker.execute(self.spec["attempt_id"])
                self.assert_held(result, state)
                self.assert_repeat_inert(result)
        self.github.assert_not_called()
        self.assert_no_session()

    def test_successfully_read_ineligible_lifecycle_is_clean_in_best_effort(self):
        self.configure_worker(self.root)
        for index, changes in enumerate(({"draft": True}, {"state": "closed"}, {"merged": True})):
            with self.subTest(changes=changes):
                self.spec = specification()
                self.spec["pr_id"] = self.spec["pr"]["id"] = 77 + index
                self.spec["pr"]["head"]["ref"] = "lifecycle-" + str(index)
                self.submit()
                self.live_pr.update(changes)
                result = self.worker.execute(self.spec["attempt_id"])
                self.assert_clean(result)
                self.assert_repeat_inert(result)
                self.assert_released()
        self.available.assert_not_called()
        self.assertFalse(self.worker.checkout(self.spec).exists())

    def test_lifecycle_rejection_in_strict_mode_retains_ownership(self):
        self.configure_worker(self.root, quiescence_mode="strict")
        self.submit()
        self.live_pr["draft"] = True
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.available.assert_not_called()
        self.assert_no_session()

    def test_live_read_failure_is_not_a_clean_lifecycle_rejection(self):
        self.configure_worker(self.root)
        self.submit()
        self.github.side_effect = Blocked("live read-only GitHub validation unavailable")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.assert_no_session()

    def test_incomplete_live_response_is_not_known_clean_rejection(self):
        self.configure_worker(self.root)
        self.submit()
        self.live_pr.pop("base")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.assert_no_session()

    def test_live_head_and_base_changes_are_clean_without_automatic_conflict_retry(self):
        self.configure_worker(self.root)
        for index, (side, stage) in enumerate((("head", "cleanup"), ("base", "cleanup"), ("base", "conflicts"))):
            with self.subTest(side=side, stage=stage):
                self.spec = specification()
                self.spec["pr_id"] = self.spec["pr"]["id"] = 100 + index
                self.spec["pr"]["head"]["ref"] = "moved-" + str(index)
                self.spec["stage"] = stage
                self.submit()
                self.live_pr[side]["sha"] = "c" * 40
                if side == "base":
                    self.live_base_sha = "c" * 40
                result = self.worker.execute(self.spec["attempt_id"])
                self.assert_clean(result)
                self.assert_repeat_inert(result)
                self.assert_released()
        self.available.assert_not_called()


class GitStartupTests(StartupFixture, Repository):
    def setUp(self):
        super().setUp()
        self.configure_worker(self.root)
        self.spec["input_head_sha"] = self.spec["pr"]["head"]["sha"] = self.head
        self.spec["base_sha"] = self.spec["pr"]["base"]["sha"] = self.base
        self.startup_ops = GitOps(self.worker.checkout(self.spec), str(self.origin))
        self.git_factory = self.mock_method(self.worker, "git", return_value=self.startup_ops)
        self.external += (self.git_factory,)

    def advance(self, branch):
        git(self.seed, "checkout", branch)
        (self.seed / "advance.txt").write_text("concurrent branch update\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "concurrent update")
        git(self.seed, "push", "origin", branch)
        return git(self.seed, "rev-parse", "HEAD")

    def assert_partial_preserved(self, result, advanced, slot):
        checkout = self.startup_ops.path
        self.assertTrue((checkout / ".git").is_dir())
        self.assertEqual(git(checkout, "rev-parse", "refs/pr-maintenance/" + slot), advanced)
        self.assertFalse((checkout / "topic.txt").exists())
        retained = checkout / "retained-inspection.txt"
        retained.write_text("keep this partial checkout\n")
        self.assert_repeat_inert(result)
        self.assertEqual(retained.read_text(), "keep this partial checkout\n")
        self.assertEqual(git(checkout, "rev-parse", "refs/pr-maintenance/" + slot), advanced)
        self.available.assert_not_called()
        self.assert_no_session()
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"),
                         advanced if slot == "input" else self.head)

    def test_real_base_fetch_mismatch_releases_clean_attempt_and_preserves_partial_clone(self):
        self.spec["stage"] = "conflicts"
        self.submit()
        advanced = self.advance("main")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_clean(result)
        self.assert_partial_preserved(result, advanced, "base")
        self.assert_released()

    def test_real_head_fetch_mismatch_releases_clean_attempt_and_preserves_partial_clone(self):
        self.submit()
        advanced = self.advance("topic")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_clean(result)
        self.assert_partial_preserved(result, advanced, "input")
        self.assert_released()

    def test_real_base_fetch_mismatch_in_strict_mode_stays_held(self):
        self.config["quiescence_mode"] = "strict"
        self.submit()
        advanced = self.advance("main")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_partial_preserved(result, advanced, "base")

    def test_existing_dirty_checkout_is_preserved_and_not_reused(self):
        self.submit()
        shutil.copytree(self.ops.path, self.startup_ops.path)
        retained = self.startup_ops.path / "topic.txt"
        retained.write_text("interrupted edits must remain\n")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.assertEqual(retained.read_text(), "interrupted edits must remain\n")
        self.assertEqual(git(self.startup_ops.path, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)
        self.available.assert_not_called()
        self.assert_no_session()

    def test_real_checkout_failure_stays_held_and_retains_obstruction(self):
        self.submit()
        fetch = self.startup_ops.fetch
        retained = self.startup_ops.path / "topic.txt"

        def obstruct_checkout(branch, slot):
            observed = fetch(branch, slot)
            if slot == "base":
                retained.write_text("untracked inspection evidence\n")
            return observed

        with patch.object(self.startup_ops, "fetch", side_effect=obstruct_checkout):
            result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assertIn("checkout", result["error"])
        self.assert_repeat_inert(result)
        self.assertEqual(retained.read_text(), "untracked inspection evidence\n")
        self.available.assert_not_called()
        self.assert_no_session()

    def test_clean_rejection_type_after_checkout_cannot_release_ownership(self):
        self.submit()
        self.available.side_effect = common.PreflightRejected("model no longer eligible")
        result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.assertEqual(git(self.startup_ops.path, "rev-parse", "HEAD"), self.head)
        self.assertEqual((self.startup_ops.path / "topic.txt").read_text(), "topic\n")
        self.assert_no_session()

    def test_clean_rejection_type_during_checkout_cannot_release_ownership(self):
        self.submit()
        run = self.startup_ops.run

        def failed_checkout(*args, **kwargs):
            if args[0] == "checkout":
                (self.startup_ops.path / "retained.txt").write_text("checkout began\n")
                raise common.PreflightRejected("checkout was rejected after intent")
            return run(*args, **kwargs)

        with patch.object(self.startup_ops, "run", side_effect=failed_checkout):
            result = self.worker.execute(self.spec["attempt_id"])
        self.assert_held(result)
        self.assert_repeat_inert(result)
        self.assertEqual((self.startup_ops.path / "retained.txt").read_text(), "checkout began\n")
        self.available.assert_not_called()
        self.assert_no_session()
