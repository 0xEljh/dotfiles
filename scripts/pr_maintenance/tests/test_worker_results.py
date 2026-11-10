import contextlib
import json
from unittest.mock import patch

from pr_maintenance.common import Blocked
from pr_maintenance.worker import Worker
from test_git_ops import GitOps, Repository, git
from test_worker import specification


class CodingResultTests(Repository):
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
        self.result_path = self.worker.artifacts(self.attempt) / "result.json"
        checks = contextlib.ExitStack()
        self.addCleanup(checks.close)
        checks.enter_context(patch.object(self.worker, "git", return_value=self.owned))
        checks.enter_context(patch.object(self.worker, "live", return_value=self.spec["pr"]))
        checks.enter_context(patch.object(self.worker.runner, "check_contract"))
        checks.enter_context(patch.object(self.worker.runner, "check_available"))
        checks.enter_context(patch.object(self.worker.runner, "create_session",
            side_effect=lambda cwd, title, agent, model, session, metadata: session))
        self.run = checks.enter_context(patch.object(self.worker.runner, "run"))
        checks.enter_context(patch.object(self.worker.runner, "quiescent", return_value=True))
        self.verify = checks.enter_context(patch.object(self.worker, "_verify"))

    def execute_result(self, result):
        self.run.side_effect = lambda *args: self.result_path.write_text(json.dumps(result))
        return self.worker.execute(self.attempt)

    def assert_completed_blocker(self, status, error):
        self.assertEqual(status["state"], "blocked")
        self.assertTrue(status["quiescent"])
        self.assertEqual(status["publication_state"], "not_published")
        self.assertIn(error, status["error"])
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)
        self.assertTrue(self.owned.path.is_dir())
        self.verify.assert_not_called()
        self.run.assert_called_once()
        self.assertEqual(self.worker.submit(specification())["state"], "accepted")

    def test_missing_result_is_completed_blocker_not_noop(self):
        status = self.worker.execute(self.attempt)
        self.assert_completed_blocker(status, "missing private result")

    def test_malformed_result_is_completed_blocker_not_noop(self):
        self.run.side_effect = lambda *args: self.result_path.write_text("{invalid json")
        self.assert_completed_blocker(self.worker.execute(self.attempt), "valid JSON")

    def test_non_object_result_is_completed_blocker(self):
        self.assert_completed_blocker(self.execute_result([]), "JSON object")

    def test_missing_outcome_is_actionable_blocker_not_noop(self):
        self.assert_completed_blocker(self.execute_result({"summary": "No edits needed"}), "outcome")

    def test_invalid_outcome_is_actionable_blocker_not_noop(self):
        self.assert_completed_blocker(self.execute_result({"outcome": "success"}), "outcome")

    def test_unhashable_outcome_is_completed_blocker_not_a_worker_crash(self):
        self.assert_completed_blocker(self.execute_result({"outcome": []}), "outcome")

    def test_explicit_blocked_result_never_becomes_noop_even_with_legacy_summary(self):
        blocker = {"reason": "Dependencies unavailable", "next_action": "Configure the existing package manager"}
        status = self.execute_result({"outcome": "blocked", "summary": "No changes needed", "blockers": [blocker]})
        self.assert_completed_blocker(status, "declared blocked")
        self.assertEqual(status["summary"]["blockers"], [blocker])
        self.assertEqual(status["summary"]["outcome"], "blocked")

    def test_nonempty_structured_blockers_require_blocked_outcome(self):
        status = self.execute_result({"outcome": "no_change", "blockers": [
            {"reason": "Verification unavailable", "next_action": "Provide the existing test dependency"},
        ]})
        self.assert_completed_blocker(status, "outcome blocked")

    def test_blocked_result_requires_actionable_structured_blockers(self):
        status = self.execute_result({"outcome": "blocked", "blockers": [{"reason": "Missing check"}]})
        self.assert_completed_blocker(status, "next_action")

    def test_explicit_no_change_requires_no_english_summary_interpretation(self):
        status = self.execute_result({"outcome": "no_change", "summary": "Removed the obsolete blocked-state wording"})
        self.assertEqual(status["state"], "noop")
        self.assertEqual(status["summary"]["outcome"], "no_change")
        self.verify.assert_not_called()

    def test_no_change_with_edits_is_blocked_and_retains_them(self):
        def run(*args):
            (self.owned.path / "topic.txt").write_text("retained partial work\n")
            self.result_path.write_text(json.dumps({"outcome": "no_change"}))
        self.run.side_effect = run
        self.assert_completed_blocker(self.worker.execute(self.attempt), "clean original HEAD")
        self.assertEqual((self.owned.path / "topic.txt").read_text(), "retained partial work\n")

    def test_no_change_with_clean_nonoriginal_head_is_blocked(self):
        def run(*args):
            git(self.owned.path, "config", "user.name", "Test")
            git(self.owned.path, "config", "user.email", "test@example.test")
            (self.owned.path / "topic.txt").write_text("retained commit\n")
            git(self.owned.path, "add", "topic.txt")
            git(self.owned.path, "commit", "-m", "maintenance")
            self.result_path.write_text(json.dumps({"outcome": "no_change"}))
        self.run.side_effect = run
        self.assert_completed_blocker(self.worker.execute(self.attempt), "clean original HEAD")
        self.assertTrue(self.owned.clean())
        self.assertNotEqual(self.owned.head(), self.head)

    def test_changed_result_cannot_use_clean_checkout_as_noop(self):
        self.assert_completed_blocker(self.execute_result({"outcome": "changed"}), "candidate")

    def test_blocked_result_retains_recorded_candidate_without_verification_or_publication(self):
        def run(*args):
            manifest = self.result_path.with_name("manifest-request.json")
            manifest.write_text(json.dumps({"paths": {"topic.txt": "PR-related maintenance"}}))
            self.worker.record_manifest(self.attempt, manifest)
            git(self.owned.path, "config", "user.name", "Test")
            git(self.owned.path, "config", "user.email", "test@example.test")
            (self.owned.path / "topic.txt").write_text("retained candidate\n")
            git(self.owned.path, "add", "topic.txt")
            git(self.owned.path, "commit", "-m", "maintenance")
            self.worker.record_candidate(self.attempt)
            self.result_path.write_text(json.dumps({"outcome": "blocked", "blockers": [
                {"reason": "Verification unavailable", "next_action": "Provide the existing test dependency"},
            ]}))
        self.run.side_effect = run
        status = self.worker.execute(self.attempt)
        self.assert_completed_blocker(status, "declared blocked")
        self.assertEqual(status["candidate_sha"], self.owned.head())
        self.assertNotEqual(status["candidate_sha"], self.head)
        self.assertEqual((self.owned.path / "topic.txt").read_text(), "retained candidate\n")

    def test_result_secret_is_not_copied_into_blocker_or_summary(self):
        token = "ghp_" + "x" * 30
        status = self.execute_result({"outcome": "blocked", "blockers": [
            {"reason": token, "next_action": "Inspect private evidence"},
        ]})
        self.assert_completed_blocker(status, "Credential-shaped")
        self.assertNotIn(token, json.dumps(status))
        self.assertIn(token, self.result_path.read_text())

    def test_arbitrary_exception_after_quiet_coding_still_requires_recovery(self):
        self.run.side_effect = lambda *args: self.result_path.write_text(json.dumps({"outcome": "changed"}))
        with patch.object(self.worker, "_finish_coding", side_effect=OSError("unexpected tool failure")):
            status = self.worker.execute(self.attempt)
        self.assertEqual(status["state"], "inspection_required")
        self.assertFalse(status["quiescent"])
        with self.assertRaisesRegex(Blocked, "branch"):
            self.worker.submit(specification())

    def test_declared_blocked_without_fresh_session_quiet_keeps_ownership(self):
        with patch.object(self.worker.runner, "quiescent", return_value=False):
            status = self.execute_result({"outcome": "blocked", "blockers": [
                {"reason": "Checks unavailable", "next_action": "Prepare dependencies"},
            ]})
        self.assertEqual(status["state"], "inspection_required")
        self.assertFalse(status["quiescent"])
        with self.assertRaisesRegex(Blocked, "branch"):
            self.worker.submit(specification())

    def assert_result_evidence_change_holds_ownership(self, outcome, **changes):
        read_result = self.worker._coding_result
        def changed_evidence(spec, data):
            result = read_result(spec, data)
            self.worker._update(self.attempt, **changes)
            return result
        value = {"outcome": outcome}
        if outcome == "blocked":
            value["blockers"] = [{"reason": "Checks unavailable", "next_action": "Prepare dependencies"}]
        with patch.object(self.worker, "_coding_result", side_effect=changed_evidence):
            status = self.execute_result(value)
        self.assertEqual(status["state"], changes.get("state", "inspection_required"))
        self.assertFalse(status["quiescent"])
        self.verify.assert_not_called()
        with self.assertRaisesRegex(Blocked, "branch"):
            self.worker.submit(specification())

    def test_cancellation_during_result_read_cannot_be_overwritten_by_blocked(self):
        self.assert_result_evidence_change_holds_ownership("blocked", state="cancel_requested",
                                                         authorization_epoch=2, execution_interrupted=True)

    def test_revoked_epoch_during_result_read_cannot_be_overwritten_by_noop(self):
        self.assert_result_evidence_change_holds_ownership("no_change", authorization_epoch=2)

    def test_lost_quiescence_during_result_read_cannot_be_overwritten_by_blocked(self):
        self.assert_result_evidence_change_holds_ownership("blocked", quiescent=False)

    def test_interrupted_execution_during_result_read_cannot_be_overwritten_by_blocked(self):
        self.assert_result_evidence_change_holds_ownership("blocked", execution_interrupted=True)
