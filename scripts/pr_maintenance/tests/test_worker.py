import concurrent.futures
import contextlib
import copy
import json
import random
import sys
import threading
import tempfile
import time
import unittest
from unittest.mock import Mock
import uuid
from pathlib import Path
from unittest.mock import patch

from pr_maintenance.common import Blocked, digest
from pr_maintenance.worker import Worker
from test_git_ops import GitOps, Repository, git, pr_payload


def specification():
    return {"attempt_id": str(uuid.uuid4()), "pr_id": 77, "stage": "cleanup", "ordinal": 1,
            "pr": pr_payload(), "input_head_sha": "b" * 40, "base_sha": "a" * 40,
            "config_hash": "config", "authorization_epoch": 1, "model": "provider/model", "agent": "worker",
            "review_model": "provider/review", "review_agent": "reviewer", "verification_profile": [["true"]],
            "feedback": None, "limits": {}}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = {"state_dir": str(root / "state"), "checkout_root": str(root / "checkouts"),
                       "skills_dir": str(root / "skills"), "execution_enabled": False}
        self.spec = specification()

    def test_jitter_duplicate_submit_is_immutable_and_schedules_no_prompt(self):
        def submit(_):
            time.sleep(random.random() / 100)
            worker = Worker(self.config)
            try:
                return worker.transport({"operation": "submit", "attempt_id": self.spec["attempt_id"], "spec": self.spec, "spec_hash": digest(self.spec)})
            finally:
                worker.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(24)))
        self.assertEqual({result["attempt_id"] for result in results}, {self.spec["attempt_id"]})
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        collection = worker.transport({"operation": "collect"})
        self.assertEqual(len(collection["attempts"]), 1)
        uuid.UUID(collection["ledger_id"])
        self.assertEqual(collection["attempts"][0]["spec_hash"], digest(self.spec))
        altered = copy.deepcopy(self.spec)
        altered["model"] = "other/model"
        with self.assertRaisesRegex(Blocked, "immutable"):
            worker.transport({"operation": "submit", "attempt_id": altered["attempt_id"], "spec": altered})

    def test_unresolved_branch_owner_survives_restart(self):
        worker = Worker(self.config)
        worker.submit(self.spec)
        worker.close()
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        other = specification()
        with self.assertRaisesRegex(Blocked, "branch"):
            worker.submit(other)

    def test_unresolved_pr_owner_survives_retarget_and_restart(self):
        worker = Worker(self.config)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], state="inspection_required", started=True, quiescent=False)
        worker.close()
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        other = specification()
        other["pr"]["head"]["ref"] = "retargeted"
        with self.assertRaisesRegex(Blocked, "PR"):
            worker.submit(other)
        self.assertFalse(worker.status(self.spec["attempt_id"])["quiescent"])

    def test_jitter_same_pr_on_different_branches_accepts_one_owner(self):
        specs = []
        for index in range(24):
            spec = specification()
            spec["pr"]["head"]["ref"] = f"retarget-{index}"
            specs.append(spec)

        def submit(spec):
            time.sleep(random.uniform(0, .02))
            worker = Worker(self.config)
            try:
                return worker.submit(spec)["attempt_id"]
            except Blocked as error:
                self.assertIn("PR", str(error))
                return None
            finally:
                worker.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            owners = [owner for owner in pool.map(submit, specs) if owner is not None]
        self.assertEqual(len(owners), 1)
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        self.assertEqual(len(worker.transport({"operation": "collect"})["attempts"]), 1)

    def test_started_crash_is_held_without_second_prompt(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], started=True, quiescent=False, state="running")
        with patch.object(worker.runner, "create_session") as create:
            result = worker.execute(self.spec["attempt_id"])
            self.assertEqual(result["state"], "inspection_required")
            self.assertFalse(result["quiescent"])
            create.assert_not_called()

    def test_killing_unit_cannot_claim_shared_server_cancellation(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], started=True, session_id="ses_test123", quiescent=False, state="running")
        with patch.object(worker, "_systemctl", return_value=True), patch.object(worker.runner, "interrupt"), patch.object(worker.runner, "quiescent", return_value=False):
            result = worker.cancel(self.spec["attempt_id"], 2)
            self.assertEqual(result["state"], "cancel_requested")
            self.assertFalse(result["quiescent"])
        with self.assertRaisesRegex(Blocked, "epoch"):
            worker.authorize(self.spec["attempt_id"], {"authorization_epoch": 1})

    def test_execution_failure_without_host_descendant_proof_keeps_ownership(self):
        self.config.update(execution_enabled=True, rollout_acknowledged=True, min_free_bytes=0,
                           runner_version="test", opencode_server="http://localhost:1234",
                           runner_contract_verified=True, runner_quiescence_argv=["/trusted/probe"])
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        with patch.object(worker, "_schedule"):
            worker.submit(self.spec)
        with patch.object(worker.runner, "check_contract", side_effect=Blocked("unavailable runner")):
            result = worker.execute(self.spec["attempt_id"])
        self.assertEqual(result["state"], "inspection_required")
        self.assertFalse(result["quiescent"])
        with self.assertRaisesRegex(Blocked, "branch"):
            worker.submit(specification())

    def test_stale_or_unbound_grant_never_publishes(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], state="ready_to_publish", candidate_sha="c" * 40)
        grant = {"attempt_id": self.spec["attempt_id"], "candidate_sha": "c" * 40,
                 "input_head_sha": "b" * 40, "head_repository_id": 10, "head_ref": "topic",
                 "authorization_epoch": 1, "expires_at": time.time() - 1}
        with self.assertRaisesRegex(Blocked, "expired"):
            worker.authorize(self.spec["attempt_id"], grant)
        grant["expires_at"] = float("nan")
        with self.assertRaisesRegex(Blocked, "expired|finite"):
            worker.authorize(self.spec["attempt_id"], grant)

    def test_cancel_unstarted_is_terminal_and_releases_branch(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        self.assertEqual(worker.cancel(self.spec["attempt_id"], 2)["state"], "cancelled")
        self.assertEqual(worker.submit(specification())["state"], "accepted")

    def test_worker_ledger_identity_is_durable(self):
        worker = Worker(self.config)
        first = worker.transport({"operation": "collect"})["ledger_id"]
        worker.close()
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        self.assertEqual(worker.transport({"operation": "collect"})["ledger_id"], first)

    def test_incident_owner_releases_only_after_explicit_epoch_acknowledgement(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], state="published_with_concern", publication_state="published", quiescent=True)
        with self.assertRaisesRegex(Blocked, "branch"):
            worker.submit(specification())
        response = worker.transport({"operation": "cancel", "attempt_id": self.spec["attempt_id"],
                                     "epoch": 2, "acknowledge_incident": True})
        self.assertTrue(response["incident_acknowledged"])
        self.assertEqual(response["state"], "published_with_concern")
        self.assertEqual(worker.submit(specification())["state"], "accepted")

    def test_pause_retains_candidate_and_new_epoch_can_resume_publication(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], started=True, quiescent=True, state="ready_to_publish",
                       candidate_sha="c" * 40, publication_state="unpublished", review_sha256="evidence")
        with patch.object(worker, "reconcile_attempt", side_effect=lambda attempt: worker.status(attempt)):
            result = worker.transport({"operation": "cancel", "attempt_id": self.spec["attempt_id"], "epoch": 2, "pause": True})
        self.assertNotIn(result["state"], {"cancelled", "blocked"})
        self.assertEqual(result["candidate_sha"], "c" * 40)

    def test_ready_status_exposes_publication_handshake(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], state="ready_to_publish", candidate_sha="c" * 40)
        self.assertEqual(worker.status(self.spec["attempt_id"])["publication_state"], "ready_to_publish")
        worker._update(self.spec["attempt_id"], publication_state="push_in_flight", quiescent=False)
        self.assertEqual(worker.status(self.spec["attempt_id"])["publication_state"], "push_in_flight")

    def test_unprofiled_repository_records_selected_checks_before_edits(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        self.spec["verification_profile"] = None
        worker.submit(self.spec)
        worker._update(self.spec["attempt_id"], state="running", started=True)
        evidence = worker.artifacts(self.spec["attempt_id"]) / "manifest-request.json"
        command = [sys.executable, "-c", "print('selected repository check')"]
        evidence.write_text(json.dumps({"paths": {"file.py": "PR caller"}, "verification_profile": [command]}))
        ops = Mock()
        ops.head.return_value = self.spec["input_head_sha"]
        ops.clean.return_value = True
        with patch.object(worker, "git", return_value=ops):
            worker.record_manifest(self.spec["attempt_id"], evidence)
        with patch.object(worker, "checkout", return_value=Path(self.temp.name)):
            result = worker._verify(self.spec, "c" * 40)
        self.assertEqual(result["commands"][0]["argv"], command)
        self.assertEqual(result["commands"][0]["returncode"], 0)
        evidence.write_text(json.dumps({"paths": {"file.py": "PR caller"}, "verification_profile": [["different-check"]]}))
        with patch.object(worker, "git", return_value=ops), self.assertRaisesRegex(Blocked, "before edits|cannot change"):
            worker.record_manifest(self.spec["attempt_id"], evidence)

    def test_feedback_is_hash_bound_and_kept_out_of_ledger(self):
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        snapshot = {"comments": [{"id": 8, "body": "private-feedback-sentinel"}]}
        self.spec["stage"] = "feedback"
        self.spec["feedback"] = {"sha256": digest(snapshot), "started_at": 1, "finished_at": 2}
        with self.assertRaisesRegex(Blocked, "snapshot"):
            worker.submit(self.spec, feedback={"comments": []})
        worker.submit(self.spec, feedback=snapshot)
        row = worker.db.execute("SELECT spec,data FROM worker_attempts").fetchone()
        self.assertNotIn("private-feedback-sentinel", row["spec"] + row["data"])
        self.assertEqual(worker._prompt_spec(self.spec)["feedback"], snapshot)

    def test_competing_started_marker_observed_inside_lock_does_not_rerun(self):
        self.config.update(execution_enabled=True, rollout_acknowledged=True)
        worker = Worker(self.config)
        self.addCleanup(worker.close)
        with patch.object(worker, "_schedule"):
            worker.submit(self.spec)
        original_lock = worker.lock
        @contextlib.contextmanager
        def competing_lock(name):
            with original_lock(name):
                if name == "attempt-" + self.spec["attempt_id"]:
                    worker._update(self.spec["attempt_id"], state="running", started=True, quiescent=False)
                yield
        with patch.object(worker, "lock", side_effect=competing_lock), patch.object(worker.runner, "check_contract") as check:
            worker.execute(self.spec["attempt_id"])
            check.assert_not_called()


class WorkerPublicationTests(Repository):
    def setUp(self):
        super().setUp()
        self.config = {"state_dir": str(self.root / "state"), "checkout_root": str(self.root / "checkouts"),
                       "skills_dir": str(self.root / "skills"), "execution_enabled": False, "min_free_bytes": 0}
        self.worker = Worker(self.config)
        self.addCleanup(self.worker.close)
        self.spec = specification()
        self.spec["input_head_sha"] = self.head
        self.spec["base_sha"] = self.base
        self.spec["pr"]["head"]["sha"] = self.head
        self.spec["pr"]["base"]["sha"] = self.base
        self.spec["verification_profile"] = [[sys.executable, "-c", "from pathlib import Path; assert Path('topic.txt').read_text() == 'maintained\\n'"]]
        self.worker.submit(self.spec)
        self.worker._update(self.spec["attempt_id"], started=True, state="running", quiescent=True, session_id="ses_coding")
        self.git_patch = patch.object(self.worker, "git", return_value=self.ops)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.checkout_patch = patch.object(self.worker, "checkout", return_value=self.ops.path)
        self.checkout_patch.start()
        self.addCleanup(self.checkout_patch.stop)
        evidence = self.worker.artifacts(self.spec["attempt_id"]) / "manifest-request.json"
        evidence.write_text(json.dumps({"paths": {"topic.txt": "PR-related maintenance"}}))
        self.worker.record_manifest(self.spec["attempt_id"], evidence)
        self.commit = self.candidate()
        self.worker.record_candidate(self.spec["attempt_id"])
        (self.worker.artifacts(self.spec["attempt_id"]) / "result.json").write_text(json.dumps({
            "outcome": "changed", "blockers": [], "summary": "maintained topic", "changed_paths": ["topic.txt"],
        }))

    def approve(self):
        def review_run(cwd, session, model, agent, prompt, events, timeout):
            _, data = self.worker._row(self.spec["attempt_id"])
            events.write_text(json.dumps({"type": "text", "part": {"text": json.dumps({"decision": "approve", "candidate_sha": self.commit,
                                                                                      "manifest_hash": data["manifest_hash"], "findings": []})}}) + "\n")
        with patch.object(self.worker.runner, "create_session", side_effect=lambda cwd, title, agent, model, session, metadata: session), \
             patch.object(self.worker.runner, "run", side_effect=review_run), patch.object(self.worker.runner, "quiescent", return_value=True):
            return self.worker._finish_coding(self.spec)

    def grant(self):
        return {"attempt_id": self.spec["attempt_id"], "candidate_sha": self.commit, "input_head_sha": self.head,
                "head_repository_id": 10, "head_ref": "topic", "authorization_epoch": 1, "expires_at": time.time() + 30}

    def test_trusted_verification_review_and_fresh_grant_publish_real_git(self):
        self.assertEqual(self.approve()["state"], "ready_to_publish")
        self.worker.authorize(self.spec["attempt_id"], self.grant())
        live = copy.deepcopy(self.spec["pr"])
        with patch.object(self.worker, "live", return_value=live):
            self.assertEqual(self.worker._publish(self.spec)["state"], "pushed")
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.commit)

    def test_model_assertions_cannot_replace_failing_trusted_verification(self):
        self.spec["verification_profile"] = [[sys.executable, "-c", "raise SystemExit(7)"]]
        with patch.object(self.worker.runner, "create_session") as create:
            with self.assertRaisesRegex(Blocked, "verification failed"):
                self.worker._finish_coding(self.spec)
            create.assert_not_called()
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)

    def test_result_secret_rejection_identifies_existing_private_file_without_echo(self):
        result = self.worker.artifacts(self.spec["attempt_id"]) / "result.json"
        token = "ghp_" + "x" * 30
        result.write_text(json.dumps({"summary": token}))
        with self.assertRaises(Blocked) as error:
            self.worker._finish_coding(self.spec)
        self.assertIn(str(result), str(error.exception))
        self.assertIn("not a confirmed credential leak", str(error.exception))
        self.assertNotIn(token, str(error.exception))
        self.assertIn(token, result.read_text())
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)

    def test_verification_spawn_is_durably_nonquiescent_before_process_start(self):
        def interrupted_spawn(*args, **kwargs):
            self.assertFalse(self.worker.status(self.spec["attempt_id"])["quiescent"])
            raise OSError("simulated process-start failure")
        with patch("pr_maintenance.worker.subprocess.Popen", side_effect=interrupted_spawn):
            with self.assertRaises(OSError):
                self.worker._verify(self.spec, self.commit)

    def test_prepublication_identity_failure_does_not_push(self):
        self.approve()
        self.worker.authorize(self.spec["attempt_id"], self.grant())
        with patch.object(self.worker, "live", side_effect=Blocked("PR became draft")):
            with self.assertRaisesRegex(Blocked, "draft"):
                self.worker._publish(self.spec)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)

    def test_altered_verification_artifact_cannot_publish(self):
        self.approve()
        self.worker.authorize(self.spec["attempt_id"], self.grant())
        (self.worker.artifacts(self.spec["attempt_id"]) / "verification-0.log").write_text("replaced evidence")
        with patch.object(self.worker, "live", return_value=self.spec["pr"]):
            with self.assertRaisesRegex(Blocked, "verification.*changed"):
                self.worker._publish(self.spec)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)

    def test_postpublication_lifecycle_race_is_incident_and_keeps_owner(self):
        self.approve()
        self.worker.authorize(self.spec["attempt_id"], self.grant())
        with patch.object(self.worker, "live", side_effect=[self.spec["pr"], Blocked("PR became draft")]):
            result = self.worker._publish(self.spec)
        self.assertEqual(result["state"], "published_with_concern")
        self.assertEqual(result["publication_state"], "published")
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.commit)
        with self.assertRaisesRegex(Blocked, "branch"):
            self.worker.submit(specification())

    def test_verified_pause_preserves_candidate_for_fresh_epoch(self):
        self.approve()
        with patch.object(self.worker, "_systemctl", return_value=True), patch.object(self.worker.runner, "interrupt"):
            status = self.worker.cancel(self.spec["attempt_id"], 2, pause=True)
        self.assertEqual(status["state"], "ready_to_publish")
        grant = dict(self.grant(), authorization_epoch=3)
        self.assertEqual(self.worker.authorize(self.spec["attempt_id"], grant)["authorization_epoch"], 3)

    def test_lost_ack_candidate_reconciles_without_rerunning_agent(self):
        self.approve()
        self.ops.publish("topic", self.head, self.commit)
        with patch.object(self.worker, "live", return_value=self.spec["pr"]), patch.object(self.worker.runner, "run") as run:
            result = self.worker.reconcile_attempt(self.spec["attempt_id"])
        self.assertEqual(result["state"], "pushed")
        run.assert_not_called()

    def test_postpush_candidate_removed_from_remote_is_an_incident(self):
        self.approve()
        self.worker.authorize(self.spec["attempt_id"], self.grant())
        calls = []
        def live(spec):
            calls.append(1)
            if len(calls) == 2:
                # Simulate a human force-update after our confirmed normal push.
                git(self.origin, "update-ref", "refs/heads/topic", self.head)
            return self.spec["pr"]
        with patch.object(self.worker, "live", side_effect=live):
            result = self.worker._publish(self.spec)
        self.assertEqual(result["state"], "published_with_concern")


class ExecutionStressTests(Repository):
    def test_jitter_independent_prs_overlap_despite_held_owner_without_duplicate_prompts(self):
        config = {"state_dir": str(self.root / "state"), "checkout_root": str(self.root / "checkouts"),
                  "skills_dir": str(self.root / "skills"), "execution_enabled": True, "rollout_acknowledged": True,
                  "runner_version": "test", "opencode_server": "http://localhost:1234", "runner_contract_verified": True,
                  "runner_quiescence_argv": ["/trusted/probe"], "config_file": "/fixed/config.json",
                  "worker_executable": "/fixed/bin/pr-worker", "min_free_bytes": 0}
        skill = self.root / "skills" / "cleanup-basic"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Keep scope narrow.")
        specs = []
        worker = Worker(config)
        held_attempts = []
        for index, (state, publication) in enumerate((("cancel_requested", "unpublished"),
                                                      ("inspection_required", "unpublished"),
                                                      ("deadline_exceeded", "unpublished"),
                                                      ("publication_unknown", "push_in_flight"))):
            held = specification()
            held["pr_id"] = held["pr"]["id"] = 200 + index
            held["pr"]["head"]["ref"] = f"held-{index}"
            with patch.object(worker, "_schedule"):
                worker.submit(held)
            worker._update(held["attempt_id"], state=state, started=True,
                           quiescent=False, publication_state=publication)
            held_attempts.append((held, state))
        for index in range(6):
            branch = f"work-{index}"
            git(self.seed, "branch", branch, self.head)
            git(self.seed, "push", "origin", branch)
            spec = specification()
            spec["pr_id"] = spec["pr"]["id"] = 100 + index
            spec["pr"]["head"]["ref"] = branch
            spec["input_head_sha"] = spec["pr"]["head"]["sha"] = self.head
            spec["base_sha"] = spec["pr"]["base"]["sha"] = self.base
            with patch.object(worker, "_schedule"):
                worker.submit(spec)
            self.assertNotIn("started_at", worker.status(spec["attempt_id"]))
            specs.append(spec)
        worker.close()
        state = {"active": 0, "maximum": 0, "sessions": []}
        mutex = threading.Lock()
        overlap = threading.Barrier(len(specs))
        def run(cwd, session, model, agent, prompt, events, timeout):
            with mutex:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
                state["sessions"].append(session)
            overlap.wait(timeout=10)
            time.sleep(random.uniform(.015, .05))
            attempt = Path(cwd).name
            (Path(config["state_dir"]) / "attempts" / attempt / "result.json").write_text(json.dumps({
                "outcome": "no_change", "blockers": [], "summary": "No PR-related cleanup needed",
            }))
            with mutex:
                state["active"] -= 1
        def execute(spec):
            time.sleep(random.random() / 50)
            instance = Worker(config)
            try:
                return instance.execute(spec["attempt_id"])
            finally:
                instance.close()
        with patch("pr_maintenance.worker.Worker.live", side_effect=lambda spec: spec["pr"]), \
             patch("pr_maintenance.worker.Worker.git", side_effect=lambda spec: GitOps(Path(config["checkout_root"]) / "10" / "10" / spec["attempt_id"], str(self.origin))), \
             patch("pr_maintenance.runner.Runner.check_contract"), patch("pr_maintenance.runner.Runner.check_available"), \
             patch("pr_maintenance.runner.Runner.create_session", side_effect=lambda cwd, title, agent, model, session, metadata: session), \
             patch("pr_maintenance.runner.Runner.run", side_effect=run), patch("pr_maintenance.runner.Runner.quiescent", return_value=True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=18) as pool:
                list(pool.map(execute, specs * 3))
            for spec in specs:
                result = execute(spec)
                self.assertEqual(result["state"], "noop")
                self.assertIsInstance(result["started_at"], float)
                self.assertLessEqual(result["started_at"], time.time())
        self.assertEqual(state["maximum"], len(specs))
        self.assertEqual(len(state["sessions"]), len(specs))
        self.assertEqual(len(set(state["sessions"])), len(specs))
        worker = Worker(config)
        self.addCleanup(worker.close)
        for held, state in held_attempts:
            self.assertEqual(worker.status(held["attempt_id"])["state"], state)
            self.assertFalse(worker.status(held["attempt_id"])["quiescent"])
