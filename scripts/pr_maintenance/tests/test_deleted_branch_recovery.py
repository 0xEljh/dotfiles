import contextlib
import copy
from unittest.mock import patch

from pr_maintenance.common import Blocked, digest
from pr_maintenance.worker import Worker
from test_git_ops import Repository, git
from test_worker import specification


class DeletedBranchRecoveryTests(Repository):
    def setUp(self):
        super().setUp()
        self.config = {"state_dir": str(self.root / "state"), "checkout_root": str(self.root / "checkouts"),
                       "quiescence_mode": "best_effort", "best_effort_acknowledged": True,
                       "runner_version": "pinned", "opencode_server": "http://localhost:1234"}
        self.worker = Worker(self.config)
        self.addCleanup(self.worker.close)
        self.spec = specification()
        self.spec["input_head_sha"] = self.spec["pr"]["head"]["sha"] = self.head
        self.spec["base_sha"] = self.spec["pr"]["base"]["sha"] = self.base
        self.attempt = self.spec["attempt_id"]
        self.worker.submit(self.spec)
        self.commit = self.candidate()
        git(self.origin, "fetch", str(self.ops.path), self.commit)
        git(self.seed, "checkout", "main")
        git(self.seed, "merge", "--no-ff", "topic", "-m", "merge original PR")
        git(self.seed, "push", "origin", "main")
        self.merged = git(self.seed, "rev-parse", "HEAD")
        self.current_base = self.merged
        git(self.origin, "update-ref", "refs/pull/1/head", self.head)
        git(self.origin, "update-ref", "-d", "refs/heads/topic")
        self.pr = copy.deepcopy(self.spec["pr"])
        self.pr.update(state="closed", merged=True, merge_commit_sha=self.merged)
        self.worker._update(self.attempt, started=True, state="cancel_requested", quiescent=False,
                            publication_state="unpublished", candidate_sha=self.commit, session_id="ses_coding")
        checks = contextlib.ExitStack()
        self.addCleanup(checks.close)
        checks.enter_context(patch.object(self.worker, "git", return_value=self.ops))
        self.github = checks.enter_context(patch.object(self.worker, "_github", side_effect=self.metadata))
        checks.enter_context(patch.object(self.worker, "checkout", return_value=self.ops.path))
        checks.enter_context(patch.object(self.worker, "_stopped_unit_evidence", return_value={"unit": self.worker.unit(self.attempt)}))
        checks.enter_context(patch.object(self.worker.runner, "quiescent", return_value=True))
        checks.enter_context(patch.object(self.worker.runner, "api", return_value={"data": {
            "id": "ses_coding", "location": {"directory": str(self.ops.path)},
            "metadata": {"attempt_id": self.attempt, "spec_hash": digest(self.spec)}}}))
        self.run = checks.enter_context(patch.object(self.worker.runner, "run"))
        self.schedule = checks.enter_context(patch.object(self.worker, "_schedule"))
        self.publish = checks.enter_context(patch.object(self.ops, "publish"))

    def metadata(self, endpoint):
        if endpoint == "repos/EigenformAI/example":
            return copy.deepcopy(self.spec["pr"]["head"]["repo"])
        if endpoint == "repos/EigenformAI/example/pulls/1":
            return copy.deepcopy(self.pr)
        if endpoint == "repos/EigenformAI/example/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.current_base}}
        raise AssertionError(endpoint)

    def resolve(self, **options):
        return self.worker.resolve_interrupted(self.attempt, accept_best_effort=True,
            reason="Inspected merged PR and deleted branch; accept retained-head publication observations",
            **dict({"accept_deleted_branch": True}, **options))

    def assert_refused(self, **options):
        with self.assertRaises(Blocked):
            self.resolve(**options)
        status = self.worker.status(self.attempt)
        self.assertEqual(status["state"], "cancel_requested")
        self.assertFalse(status["quiescent"])
        self.assertIsNotNone(self.worker.db.execute("SELECT 1 FROM worker_branches WHERE attempt_id=?", (self.attempt,)).fetchone())
        self.run.assert_not_called()
        self.schedule.assert_not_called()
        self.publish.assert_not_called()

    def test_closed_pr_retained_head_can_confirm_nonpublication_without_recreating_branch(self):
        before = git(self.origin, "show-ref")
        result = self.resolve()
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(result["quiescent"])
        self.assertEqual(result["publication_state"], "not_published")
        evidence = result["operator_recovery"]["publication_evidence"]
        self.assertEqual(evidence["source"], "closed_pr_head")
        self.assertTrue(evidence["head_branch_missing"])
        self.assertEqual(evidence["head_sha"], self.head)
        self.assertEqual(evidence["base_sha"], self.merged)
        self.assertTrue(result["operator_recovery"]["accepted_deleted_branch"])
        self.assertEqual(result["candidate_sha"], self.commit)
        self.assertEqual(git(self.origin, "show-ref"), before)
        self.assertEqual(self.ops.head(), self.commit)
        self.assertEqual(git(self.ops.path, "rev-parse", "refs/pr-maintenance/input"), self.head)
        self.assertEqual(git(self.ops.path, "rev-parse", "refs/pr-maintenance/base"), self.base)
        self.assertEqual(self.resolve()["operator_recovery"], result["operator_recovery"])
        self.run.assert_not_called()
        self.schedule.assert_not_called()
        self.publish.assert_not_called()

    def test_deleted_branch_requires_separate_acknowledgement(self):
        self.assert_refused(accept_deleted_branch=False)

    def test_candidate_reachable_from_current_base_is_not_unpublished(self):
        git(self.origin, "update-ref", "refs/heads/main", self.commit)
        self.current_base = self.commit
        self.assert_refused()

    def test_changed_retained_head_cannot_confirm_nonpublication(self):
        git(self.origin, "update-ref", "refs/pull/1/head", self.commit)
        self.assert_refused()

    def test_missing_retained_ref_cannot_reuse_stale_local_recovery_evidence(self):
        git(self.ops.path, "update-ref", "refs/pr-maintenance/recovery/1/head", self.head)
        git(self.origin, "update-ref", "-d", "refs/pull/1/head")
        self.assert_refused()

    def test_open_or_changed_pr_identity_or_head_is_rejected(self):
        original = copy.deepcopy(self.pr)
        for changed in ({"state": "open"}, {"id": 999}, {"number": 2},
                        {"head": dict(original["head"], sha=self.commit)},
                        {"head": dict(original["head"], repo=None)},
                        {"head": dict(original["head"], ref="elsewhere")},
                        {"base": dict(original["base"], repo=dict(original["base"]["repo"], id=99))}):
            with self.subTest(changed=changed):
                self.pr = dict(original, **changed)
                self.assert_refused()

    def test_prefix_collision_is_not_a_recreated_exact_branch(self):
        git(self.origin, "update-ref", "refs/heads/topic-extra", self.head)
        self.assertEqual(self.resolve()["state"], "blocked")

    def test_branch_recreated_during_recovery_is_rejected(self):
        run = self.ops.run
        observations = []
        def recreate(*args, **kwargs):
            if args[0] == "ls-remote":
                observations.append(1)
                if len(observations) == 2:
                    git(self.origin, "update-ref", "refs/heads/topic", self.commit)
            return run(*args, **kwargs)
        with patch.object(self.ops, "run", side_effect=recreate):
            self.assert_refused()

    def test_branch_lookup_failure_is_not_absence(self):
        run = self.ops.run
        def unavailable(*args, **kwargs):
            if args[0] == "ls-remote":
                raise Blocked("branch lookup unavailable")
            return run(*args, **kwargs)
        with patch.object(self.ops, "run", side_effect=unavailable):
            self.assert_refused()

    def test_base_advanced_after_api_snapshot_is_rejected(self):
        git(self.origin, "update-ref", "refs/heads/main", self.head)
        self.assert_refused()

    def test_retargeted_remote_cannot_supply_deleted_branch_evidence(self):
        git(self.ops.path, "remote", "set-url", "origin", str(self.root / "missing.git"))
        self.assert_refused()

    def test_closed_unmerged_pr_uses_the_same_retained_head_checks(self):
        self.pr.update(merged=False, merge_commit_sha=None)
        result = self.resolve()
        self.assertFalse(result["operator_recovery"]["publication_evidence"]["merged"])

    def test_fork_head_cannot_supply_base_repository_pull_ref_evidence(self):
        fork = copy.deepcopy(self.spec)
        fork["pr"]["head"]["repo"] = dict(fork["pr"]["head"]["repo"], id=11, full_name="EigenformAI/fork")
        self.pr["head"]["repo"] = copy.deepcopy(fork["pr"]["head"]["repo"])
        with self.assertRaises(Blocked):
            self.worker._closed_pr_nonpublication(fork, {"candidate_sha": self.commit}, self.ops)

    def test_unknown_publication_stays_held_even_with_deleted_branch_acknowledgement(self):
        self.worker._update(self.attempt, publication_state="unknown")
        self.assert_refused()
