import concurrent.futures
import contextlib
import errno
import io
import json
import random
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

from pr_maintenance.common import Blocked, digest
from pr_maintenance.worker import Busy, Worker, main
import test_worker
from test_git_ops import Repository, git
from test_worker import specification


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        test_worker.WorkerTests.setUp(self)
        self.config.update(quiescence_mode="best_effort", best_effort_acknowledged=True,
                           runner_version="pinned", opencode_server="http://localhost:1234")
        self.worker = Worker(self.config)
        self.addCleanup(self.worker.close)
        self.attempt = self.spec["attempt_id"]
        self.worker.submit(self.spec)
        self.worker._update(self.attempt, state="cancel_requested", started=True, quiescent=False,
                            execution_interrupted=True, publication_state="unpublished",
                            candidate_sha="c" * 40, session_id="ses_coding", review_session_id="ses_review",
                            authorization={"stale": "grant"}, local_pgid=None)
        self.unit = {"unit": self.worker.unit(self.attempt), "LoadState": "loaded", "ActiveState": "inactive",
                     "SubState": "dead", "MainPID": 0, "ControlPID": 0}
        self.ops = Mock()
        self.ops.publication.return_value = "unpublished"
        self.checks = contextlib.ExitStack()
        self.addCleanup(self.checks.close)
        self.unit_check = self.checks.enter_context(patch.object(self.worker, "_stopped_unit_evidence", return_value=self.unit, create=True))
        self.quiet = self.checks.enter_context(patch.object(self.worker.runner, "quiescent", return_value=True))
        self.details = self.checks.enter_context(patch.object(self.worker.runner, "api", side_effect=self.session_details))
        self.checks.enter_context(patch.object(self.worker, "git", return_value=self.ops))
        self.metadata = self.checks.enter_context(patch.object(self.worker, "_github", return_value={
            "id": 10, "full_name": "EigenformAI/renamed-public", "owner": {"id": 97932676}, "private": False}))
        self.schedule = self.checks.enter_context(patch.object(self.worker, "_schedule"))
        self.published = self.checks.enter_context(patch.object(self.worker, "_published"))
        self.prompt = self.checks.enter_context(patch.object(self.worker.runner, "run"))
        self.live = self.checks.enter_context(patch.object(self.worker, "live", side_effect=AssertionError("recovery must not require eligible PR lifecycle")))

    def session_details(self, method, path, *, cwd):
        return {"data": {"id": path.rsplit("/", 1)[-1], "location": {"directory": str(cwd)},
                         "metadata": {"attempt_id": self.attempt, "spec_hash": digest(self.spec)}}}

    def resolve(self, **kwargs):
        return self.worker.resolve_interrupted(self.attempt, **dict(
            {"accept_best_effort": True, "reason": "Inspected retained work; accept detached-process uncertainty"}, **kwargs))

    def assert_held(self):
        result = self.worker.status(self.attempt)
        self.assertEqual(result["state"], "cancel_requested")
        self.assertFalse(result["quiescent"])
        self.assertNotIn("operator_recovery", result)
        self.assertIsNotNone(self.worker.db.execute("SELECT 1 FROM worker_branches WHERE attempt_id=?", (self.attempt,)).fetchone())
        self.ops.publish.assert_not_called()
        self.published.assert_not_called()
        self.schedule.assert_not_called()
        self.prompt.assert_not_called()

    def test_recovery_retains_artifacts_revokes_grant_and_releases_branch_without_publication(self):
        checkout = self.worker.checkout(self.spec)
        checkout.mkdir(parents=True)
        (checkout / "retained.txt").write_text("interrupted edits")
        log = self.worker.artifacts(self.attempt) / "events.jsonl"
        log.write_text("retained log")
        result = self.resolve()
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(result["quiescent"])
        self.assertEqual(result["publication_state"], "not_published")
        self.assertEqual(result["candidate_sha"], "c" * 40)
        self.assertEqual(result["authorization_epoch"], 2)
        self.assertEqual(result["operator_recovery"]["prior_state"], "cancel_requested")
        self.assertEqual(result["operator_recovery"]["assurance"], "best_effort")
        self.assertTrue(result["operator_recovery"]["accepted_best_effort"])
        self.assertEqual(result["operator_recovery"]["unit"], self.unit)
        self.assertEqual(result["operator_recovery"]["repository_id"], 10)
        self.assertEqual(result["operator_recovery"]["repository_full_name"], "EigenformAI/renamed-public")
        self.assertIn("uncertainty", result["operator_recovery"]["reason"])
        self.assertIsInstance(result["operator_recovery"]["at"], float)
        self.assertIn("manual best-effort recovery", result["error"])
        self.assertIn("no publication", result["error"])
        self.assertIn("not proof of detached descendant exit", result["error"])
        _, data = self.worker._row(self.attempt)
        self.assertIsNone(data["authorization"])
        self.assertTrue(data["execution_interrupted"])
        self.assertEqual((checkout / "retained.txt").read_text(), "interrupted edits")
        self.assertEqual(log.read_text(), "retained log")
        self.assertEqual(self.quiet.call_count, 2)
        self.ops.publication.assert_called_once_with("topic", "b" * 40, "c" * 40)
        self.metadata.assert_called_once_with("repos/EigenformAI/example")
        self.ops.publish.assert_not_called()
        self.published.assert_not_called()
        self.schedule.assert_not_called()
        self.prompt.assert_not_called()
        repeated = self.resolve(reason="A second operator reason must not replace the audit")
        self.assertEqual(repeated["operator_recovery"], result["operator_recovery"])
        self.assertEqual(repeated["authorization_epoch"], 2)
        self.unit_check.assert_called_once()
        other = Worker(self.config)
        self.addCleanup(other.close)
        self.assertEqual(other.status(self.attempt)["operator_recovery"], result["operator_recovery"])
        self.assertEqual(other.execute(self.attempt)["state"], "blocked")
        self.assertEqual(other.reconcile()[0]["state"], "blocked")
        self.live.assert_not_called()
        self.worker.submit(specification())

    def test_explicit_flag_and_nonblank_secret_checked_reason_are_required(self):
        for kwargs in ({"accept_best_effort": False}, {"accept_best_effort": 1}, {"reason": "  "},
                       {"reason": "ghp_" + "x" * 30}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Blocked):
                self.resolve(**kwargs)
            self.assert_held()
        with self.assertRaises(Blocked):
            self.worker.resolve_interrupted(self.attempt)

    def test_config_must_explicitly_acknowledge_best_effort(self):
        for changes in ({"quiescence_mode": "strict"}, {"best_effort_acknowledged": False},
                        {"best_effort_acknowledged": 1}):
            with self.subTest(changes=changes), patch.dict(self.config, changes), self.assertRaises(Blocked):
                self.resolve()
            self.assert_held()

    def test_only_started_held_best_effort_known_nonpublication_is_eligible(self):
        for changes in ({"started": False}, {"execution_assurance": "strict"}, {"state": "running"},
                        {"state": "accepted"}, {"state": "ready_to_publish"}, {"state": "publication_unknown"},
                        {"publication_state": "unknown"}, {"publication_state": "push_in_flight"},
                        {"publication_state": "published"}):
            _, original = self.worker._row(self.attempt)
            with self.subTest(changes=changes):
                self.worker._update(self.attempt, **changes)
                with self.assertRaises(Blocked):
                    self.resolve()
                self.worker._update(self.attempt, **original)
                self.assert_held()

    def test_other_held_states_and_no_candidate_can_be_resolved(self):
        for state in ("inspection_required", "deadline_exceeded"):
            with self.subTest(state=state):
                self.worker._update(self.attempt, state=state, candidate_sha=None, publication_state="not_started")
                result = self.resolve()
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(result["operator_recovery"]["prior_state"], state)
                self.ops.publication.assert_not_called()
                # Restore ownership to independently exercise the next held state.
                self.worker.db.execute("INSERT INTO worker_branches VALUES (?,?)", (self.worker.branch(self.spec), self.attempt))
                self.worker._update(self.attempt, state="cancel_requested", operator_recovery=None, quiescent=False)

    def test_candidate_recorded_is_known_nonpublication(self):
        self.worker._update(self.attempt, publication_state="candidate_recorded")
        self.assertEqual(self.resolve()["state"], "blocked")
        self.ops.publication.assert_called_once_with("topic", "b" * 40, "c" * 40)

    def sessionless(self):
        self.worker._update(self.attempt, session_id=None, review_session_id=None, candidate_sha=None,
                            authorization=None)

    def test_sessionless_recovery_requires_second_opt_in_and_records_missing_observations(self):
        self.sessionless()
        with self.assertRaises(Blocked):
            self.resolve()
        result = self.resolve(accept_sessionless=True)
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(result["quiescent"])
        audit = result["operator_recovery"]
        self.assertTrue(audit["accepted_sessionless"])
        self.assertEqual(audit["execution_phase"], "legacy_unknown")
        self.assertEqual(audit["session_observations"], "unavailable")
        self.quiet.assert_not_called()
        self.ops.publication.assert_not_called()
        self.prompt.assert_not_called()
        self.schedule.assert_not_called()

    def test_sessionless_recovery_rejects_prompt_candidate_verification_or_later_phase(self):
        self.sessionless()
        _, original = self.worker._row(self.attempt)
        for changes in ({"candidate_sha": "c" * 40}, {"prompt_hash": "prompt"},
                        {"verification": {}}, {"manifest": {}}, {"execution_phase": "coding"},
                        {"execution_phase": "checkout"}, {"session_id": ""}):
            with self.subTest(changes=changes):
                self.worker._update(self.attempt, **changes)
                with self.assertRaises(Blocked):
                    self.resolve(accept_sessionless=True)
                self.worker.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (json.dumps(original), self.attempt))
                self.assert_held()

    def test_sessionless_recovery_rejects_artifacts_even_without_ledger_prompt_marker(self):
        self.sessionless()
        for name in ("prompt.txt", "prompt.txt.tmp", "events.jsonl", "verification-0.log", "manifest.json"):
            with self.subTest(name=name):
                artifact = self.worker.artifacts(self.attempt) / name
                artifact.write_text("retained evidence")
                with self.assertRaises(Blocked):
                    self.resolve(accept_sessionless=True)
                artifact.unlink()
                self.assert_held()

    def test_sessionless_recovery_retains_unit_and_process_group_guards(self):
        self.sessionless()
        self.unit_check.side_effect = Blocked("active unit")
        with self.assertRaises(Blocked):
            self.resolve(accept_sessionless=True)
        self.unit_check.side_effect = None
        self.worker._update(self.attempt, local_pgid=987654)
        with patch("pr_maintenance.worker.os.killpg", return_value=None), self.assertRaises(Blocked):
            self.resolve(accept_sessionless=True)
        self.assert_held()

    def test_sessionless_recovery_rechecks_prompt_evidence_before_commit(self):
        self.sessionless()
        def unit(attempt):
            self.worker._update(attempt, prompt_hash="late evidence")
            return self.unit
        self.unit_check.side_effect = unit
        with self.assertRaises(Blocked):
            self.resolve(accept_sessionless=True)
        self.assert_held()

    def test_remote_result_other_than_unpublished_cannot_release_ownership(self):
        for outcome in ("already_published", "unknown", None, Blocked("head advanced"),
                        OSError("network unavailable"), subprocess.TimeoutExpired("git", 30)):
            with self.subTest(outcome=outcome):
                self.ops.publication.side_effect = outcome if isinstance(outcome, Exception) else None
                self.ops.publication.return_value = outcome
                with self.assertRaises(Blocked):
                    self.resolve()
                self.assert_held()

    def test_unit_failure_cannot_release_ownership(self):
        self.unit_check.side_effect = Blocked("unit state unknown")
        with self.assertRaises(Blocked):
            self.resolve()
        self.assert_held()

    def test_redirected_repository_metadata_must_match_numeric_repository_and_owner(self):
        for metadata in ({"id": 99, "owner": {"id": 97932676}, "full_name": "EigenformAI/example"},
                         {"id": 10, "owner": {"id": 99}, "full_name": "OtherOwner/renamed-public"},
                         {"id": "10", "owner": {"id": 97932676}, "full_name": "EigenformAI/example"},
                         {"id": 10, "full_name": "EigenformAI/example"}, None):
            with self.subTest(metadata=metadata):
                self.metadata.return_value = metadata
                with self.assertRaises(Blocked):
                    self.resolve()
                self.ops.publication.assert_not_called()
                self.assert_held()

    def test_metadata_network_failure_cannot_trust_git_redirect_or_release_ownership(self):
        for error in (Blocked("live read-only GitHub validation unavailable"), OSError("network unavailable"),
                      subprocess.TimeoutExpired("gh", 30)):
            with self.subTest(error=error):
                self.metadata.side_effect = error
                with self.assertRaises(Blocked):
                    self.resolve()
                self.ops.publication.assert_not_called()
                self.assert_held()

    def test_all_recorded_sessions_must_be_quiet_and_owned(self):
        self.quiet.side_effect = [True, False]
        with self.assertRaises(Blocked):
            self.resolve()
        self.assert_held()
        self.quiet.side_effect = None
        self.worker._update(self.attempt, session_id=None, review_session_id=None)
        with self.assertRaises(Blocked):
            self.resolve()
        self.assert_held()
        self.worker._update(self.attempt, session_id="ses_coding", review_session_id="ses_review")
        for details in ({"data": {}}, {"data": {"id": "ses_coding", "location": {"directory": "/elsewhere"}}},
                        {"data": {"id": "ses_coding", "location": {"directory": str(self.worker.checkout(self.spec))},
                                  "metadata": {"attempt_id": "other", "spec_hash": digest(self.spec)}}}):
            with self.subTest(details=details):
                self.details.side_effect = None
                self.details.return_value = details
                with self.assertRaises(Blocked):
                    self.resolve()
                self.assert_held()

    def test_recorded_process_group_requires_esrch_not_eperm_and_is_never_killed(self):
        self.worker._update(self.attempt, local_pgid=987654)
        for outcome in (None, PermissionError(errno.EPERM, "not permitted"), OSError(errno.EIO, "unknown")):
            with self.subTest(outcome=outcome), patch("pr_maintenance.worker.os.killpg", side_effect=outcome) as probe:
                with self.assertRaises(Blocked):
                    self.resolve()
                probe.assert_called_once_with(987654, 0)
                self.assert_held()
        with patch("pr_maintenance.worker.os.killpg", side_effect=ProcessLookupError(errno.ESRCH, "exited")) as probe:
            self.assertEqual(self.resolve()["state"], "blocked")
            probe.assert_called_once_with(987654, 0)

    def test_malformed_process_group_is_not_treated_as_absent(self):
        for pgid in (0, -1, True, "123"):
            with self.subTest(pgid=pgid):
                self.worker._update(self.attempt, local_pgid=pgid)
                with self.assertRaises(Blocked):
                    self.resolve()
                self.assert_held()

    def test_recovery_takes_all_execution_locks_in_order_and_honors_contention(self):
        names = ["pr-" + str(self.spec["pr_id"]), "branch-" + digest(self.worker.branch(self.spec)), "attempt-" + self.attempt]
        for name in names:
            with self.subTest(lock=name), self.worker.lock(name), self.assertRaises(Busy):
                self.resolve()
            self.assert_held()
        with patch.object(self.worker, "lock", wraps=self.worker.lock) as locks:
            self.resolve()
            self.assertEqual([call.args[0] for call in locks.call_args_list], names)

    def test_recovery_rereads_epoch_after_checks_and_revokes_newer_grant(self):
        def remote(*args):
            other = Worker(self.config)
            try:
                other._update(self.attempt, authorization_epoch=14, authorization={"new": "grant"})
            finally:
                other.close()
            return "unpublished"
        self.ops.publication.side_effect = remote
        result = self.resolve()
        self.assertEqual(result["authorization_epoch"], 15)
        self.assertIsNone(self.worker._row(self.attempt)[1]["authorization"])

    def test_changed_candidate_during_checks_is_not_released(self):
        self.ops.publication.side_effect = lambda *args: (self.worker._update(self.attempt, candidate_sha="d" * 40) and "unpublished")
        with self.assertRaises(Blocked):
            self.resolve()
        self.assert_held()

    def test_recovery_is_not_an_ssh_transport_operation_and_automatic_recovery_stays_held(self):
        with self.assertRaisesRegex(Blocked, "unsupported"):
            self.worker.transport({"operation": "resolve-interrupted", "attempt_id": self.attempt,
                                   "accept_best_effort": True, "reason": "inspected"})
        result = self.worker.reconcile_attempt(self.attempt)
        self.assertEqual(result["state"], "cancel_requested")
        self.assert_held()

    def test_local_cli_requires_flag_and_dispatches_recovery(self):
        config = self.worker.root / "config.json"
        config.write_text(json.dumps(self.config))
        argv = ["--config", str(config), "resolve-interrupted", self.attempt]
        with patch("pr_maintenance.worker.Worker", return_value=self.worker), patch.object(self.worker, "close"), \
             patch.object(self.worker, "resolve_interrupted", return_value={"state": "blocked"}, create=True) as resolve, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(argv + ["--accept-best-effort", "--reason", "inspected"]), 0)
            resolve.assert_called_once_with(self.attempt, accept_best_effort=True, accept_sessionless=False,
                                            accept_deleted_branch=False, reason="inspected")
            self.assertEqual(json.loads(output.getvalue()), {"state": "blocked"})
            resolve.reset_mock()
            self.assertEqual(main(argv + ["--accept-best-effort", "--accept-sessionless", "--reason", "inspected"]), 0)
            resolve.assert_called_once_with(self.attempt, accept_best_effort=True, accept_sessionless=True,
                                            accept_deleted_branch=False, reason="inspected")
            resolve.reset_mock()
            self.assertEqual(main(argv + ["--accept-best-effort", "--accept-deleted-branch", "--reason", "inspected"]), 0)
            resolve.assert_called_once_with(self.attempt, accept_best_effort=True, accept_sessionless=False,
                                            accept_deleted_branch=True, reason="inspected")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(argv + ["--reason", "inspected"])

    def test_jitter_parallel_recovery_commits_one_audit_and_never_schedules_or_publishes(self):
        def resolve(index):
            time.sleep(random.uniform(0, .02))
            worker = Worker(self.config)
            try:
                return worker.resolve_interrupted(self.attempt, accept_best_effort=True, reason=f"Inspected by operator {index}")
            except Busy:
                return None
            finally:
                worker.close()
        def details(method, path, *, cwd):
            time.sleep(random.uniform(0, .003))
            return self.session_details(method, path, cwd=cwd)
        with patch.object(Worker, "_stopped_unit_evidence", return_value=self.unit, create=True) as unit, \
             patch.object(Worker, "git", return_value=self.ops), \
             patch.object(Worker, "_github", return_value=self.metadata.return_value), \
             patch("pr_maintenance.runner.Runner.quiescent", return_value=True), \
             patch("pr_maintenance.runner.Runner.api", side_effect=details), \
             patch.object(Worker, "_schedule") as schedule, patch.object(Worker, "_published") as published, \
             patch("pr_maintenance.runner.Runner.run") as run:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = [result for result in pool.map(resolve, range(48)) if result is not None]
            final = resolve(99)
            self.assertTrue(results)
            self.assertEqual({json.dumps(result["operator_recovery"], sort_keys=True) for result in results + [final]},
                             {json.dumps(final["operator_recovery"], sort_keys=True)})
            self.assertEqual(final["authorization_epoch"], 2)
            unit.assert_called_once()
            schedule.assert_not_called()
            published.assert_not_called()
            run.assert_not_called()
            self.ops.publish.assert_not_called()


class RecoveryPublicationTests(Repository):
    def setUp(self):
        super().setUp()
        self.config = {"state_dir": str(self.root / "state"), "checkout_root": str(self.root / "checkouts"),
                       "quiescence_mode": "best_effort", "best_effort_acknowledged": True,
                       "runner_version": "pinned", "opencode_server": "http://localhost:1234"}
        self.worker = Worker(self.config)
        self.addCleanup(self.worker.close)
        self.spec = specification()
        self.attempt = self.spec["attempt_id"]
        self.spec["input_head_sha"] = self.spec["pr"]["head"]["sha"] = self.head
        self.spec["base_sha"] = self.spec["pr"]["base"]["sha"] = self.base
        self.worker.submit(self.spec)
        self.commit = self.candidate()
        self.worker._update(self.attempt, started=True, state="cancel_requested", quiescent=False,
                            publication_state="unpublished", candidate_sha=self.commit, session_id="ses_coding")
        checks = contextlib.ExitStack()
        self.addCleanup(checks.close)
        checks.enter_context(patch.object(self.worker, "git", return_value=self.ops))
        checks.enter_context(patch.object(self.worker, "_github", return_value={
            "id": 10, "full_name": "EigenformAI/renamed-public", "owner": {"id": 97932676}, "private": False}))
        checks.enter_context(patch.object(self.worker, "checkout", return_value=self.ops.path))
        checks.enter_context(patch.object(self.worker, "_stopped_unit_evidence", return_value={"unit": self.worker.unit(self.attempt)}))
        checks.enter_context(patch.object(self.worker.runner, "quiescent", return_value=True))
        checks.enter_context(patch.object(self.worker.runner, "api", return_value={"data": {
            "id": "ses_coding", "location": {"directory": str(self.ops.path)},
            "metadata": {"attempt_id": self.attempt, "spec_hash": digest(self.spec)}}}))
        self.live = checks.enter_context(patch.object(self.worker, "live", side_effect=AssertionError("no live PR eligibility check")))

    def resolve(self):
        return self.worker.resolve_interrupted(self.attempt, accept_best_effort=True, reason="Inspected retained work")

    def assert_remote_rejection(self):
        with self.assertRaises(Blocked):
            self.resolve()
        self.assertEqual(self.worker.status(self.attempt)["state"], "cancel_requested")
        self.assertFalse(self.worker.status(self.attempt)["quiescent"])
        self.assertNotIn("operator_recovery", self.worker.status(self.attempt))
        self.assertIsNotNone(self.worker.db.execute("SELECT 1 FROM worker_branches WHERE attempt_id=?", (self.attempt,)).fetchone())
        self.live.assert_not_called()

    def test_real_git_unpublished_recovery_retains_candidate_and_remote_head(self):
        self.assertEqual(self.resolve()["state"], "blocked")
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)
        self.assertEqual(self.ops.head(), self.commit)
        self.live.assert_not_called()

    def test_real_git_published_candidate_cannot_release_ownership(self):
        self.ops.publish("topic", self.head, self.commit)
        self.assert_remote_rejection()
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.commit)

    def test_real_git_advanced_head_cannot_release_ownership(self):
        (self.seed / "human.txt").write_text("human update\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "human")
        git(self.seed, "push", "origin", "topic")
        self.assert_remote_rejection()
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), git(self.seed, "rev-parse", "HEAD"))

    def test_real_git_retargeted_or_unreadable_remote_cannot_release_ownership(self):
        git(self.ops.path, "remote", "set-url", "origin", str(self.root / "missing.git"))
        self.assert_remote_rejection()
        # Even if the expected remote agrees, failed fetches remain inconclusive.
        self.ops.remote = str(self.root / "missing.git")
        self.assert_remote_rejection()


class UnitEvidenceTests(unittest.TestCase):
    def setUp(self):
        test_worker.WorkerTests.setUp(self)

    def test_explicit_stopped_unit_states_and_exact_command(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        for load, active, sub in (("loaded", "inactive", "dead"), ("loaded", "failed", "failed"),
                                  ("not-found", "inactive", "dead")):
            output = f"LoadState={load}\nActiveState={active}\nSubState={sub}\nMainPID=0\nControlPID=0\n"
            with self.subTest(load=load, active=active), patch("pr_maintenance.worker.subprocess.run", return_value=
                    subprocess.CompletedProcess([], 0, output, "")) as run:
                evidence = worker._stopped_unit_evidence(self.spec["attempt_id"])
                self.assertEqual(evidence["LoadState"], load)
                self.assertEqual(evidence["MainPID"], 0)
                run.assert_called_once_with(["systemctl", "--user", "show", worker.unit(self.spec["attempt_id"]),
                                             "--property=LoadState,ActiveState,SubState,MainPID,ControlPID"],
                                            capture_output=True, text=True, timeout=30)

    def test_error_active_unknown_or_malformed_unit_evidence_fails_closed(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        good = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
        cases = [(1, good, ""), (0, good, "error"), (0, "", ""), (0, good.replace("inactive", "activating"), ""),
                 (0, good.replace("loaded", "unknown"), ""), (0, good.replace("dead", "running"), ""),
                 (0, good.replace("MainPID=0", "MainPID=123"), ""), (0, good.replace("ControlPID=0", "ControlPID=123"), ""),
                 (0, good.replace("MainPID=0\n", ""), ""), (0, good + "MainPID=0\n", ""),
                 (0, good.replace("MainPID=0", "MainPID=-1"), ""), (0, good + "Unexpected=true\n", "")]
        for code, output, error in cases:
            with self.subTest(code=code, output=output, error=error), patch("pr_maintenance.worker.subprocess.run", return_value=
                    subprocess.CompletedProcess([], code, output, error)), self.assertRaises(Blocked):
                worker._stopped_unit_evidence(self.spec["attempt_id"])
        for error in (OSError("unavailable"), subprocess.TimeoutExpired("systemctl", 30)):
            with self.subTest(error=error), patch("pr_maintenance.worker.subprocess.run", side_effect=error), self.assertRaises(Blocked):
                worker._stopped_unit_evidence(self.spec["attempt_id"])
