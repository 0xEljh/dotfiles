import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.coordinator import Coordinator


def pr(pr_id=100, created=1000, draft=False, ref="feature", number=1):
    return {
        "id": pr_id, "number": number, "state": "open", "merged": False,
        "draft": draft, "created_at": created, "updated_at": created,
        "user": {"id": 34589654}, "html_url": f"https://github.com/EigenformAI/test/pull/{number}",
        "base": {"ref": "main", "sha": "b" * 40, "repo": {
            "id": 77, "full_name": "EigenformAI/test", "default_branch": "main",
            "owner": {"id": 97932676}, "clone_url": "https://github.com/EigenformAI/test.git"}},
        "head": {"ref": ref, "sha": "a" * 40, "repo": {
            "id": 77, "full_name": "EigenformAI/test",
            "clone_url": "https://github.com/EigenformAI/test.git"}},
    }


@pytest.fixture
def coordinator(tmp_path):
    return Coordinator(tmp_path / "state.sqlite", {
        "organization_id": 97932676, "author_id": 34589654,
        "worker_host": "contents-may-differ", "models": {s: "provider/model" for s in
            ("deep_modules", "cleanup", "feedback", "conflicts", "review")},
        "agents": {s: s for s in ("deep_modules", "cleanup", "feedback", "conflicts", "review")},
    }, now=lambda: 2000)


def test_baseline_draft_readiness_and_one_workflow(coordinator):
    c = coordinator
    c.enable(1500)
    c.observe(pr(created=1000), 1600)
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    with pytest.raises(Blocked, match="enroll"):
        c.control("retry-stage", 100, "deep_modules", "retry")
    c.observe(pr(101, draft=True), 1600)
    c.observe(pr(101, draft=False), 2100)
    assert c.workflow(101)["eligible_at"] == 2100
    assert c.workflow(101)["feedback_due_at"] == 3000
    c.observe(pr(101), 2500)
    assert c.workflow(101)["eligible_at"] == 2100
    assert len(c.stages(101)) == 4
    c.control("enroll", 100, reason="disposable pilot")
    assert c.workflow(100)["eligible_at"] == 2000


def test_feedback_waits_for_initial_terminal_and_due_time(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    first = c.claim(100, at=1100)
    assert first["stage"] == "deep_modules"
    assert c.claim(100, at=2000)["attempt_id"] == first["attempt_id"]
    c.accept_status(first["attempt_id"], {"state": "failed", "quiescent": True, "publication_state": "not_published"})
    second = c.claim(100, at=1101)
    assert second["stage"] == "cleanup"
    c.accept_status(second["attempt_id"], {"state": "noop", "quiescent": True, "publication_state": "not_published"})
    assert c.claim(100, at=1899) is None
    assert c.claim(100, at=1900)["stage"] == "feedback"


def test_unknown_execution_retains_branch_and_attempt(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    first = c.claim(100)
    c.accept_status(first["attempt_id"], {"state": "failed", "quiescent": False, "publication_state": "unknown"})
    assert c.claim(100)["attempt_id"] == first["attempt_id"]
    with pytest.raises(Blocked, match="quiescence"):
        c.control("retry-stage", 100, "deep_modules", "network timeout")


def test_draft_resume_and_close_do_not_repeat_stages(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    first = c.claim(100)
    c.accept_status(first["attempt_id"], {"state": "noop", "quiescent": True, "publication_state": "not_published"})
    c.observe(pr(draft=True), 2200)
    assert c.claim(100) is None
    c.observe(pr(), 2300)
    assert c.workflow(100)["feedback_due_at"] == 1900
    assert c.claim(100)["stage"] == "cleanup"
    closed = pr()
    closed["state"] = "closed"
    c.observe(closed, 2400)
    assert c.workflow(100)["lifecycle_state"] == "cancel_requested"
    c.observe(pr(), 2500)
    assert c.workflow(100)["lifecycle_state"] == "cancel_requested"


def test_shared_branch_requires_operator_even_after_owner_finishes(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    c.observe(pr(101, number=2), 1001)
    assert c.claim(100)
    assert c.claim(101) is None
    assert c.workflow(101)["paused_reason"] == "waiting_same_branch"


def test_delivery_is_atomic_deduplicated_and_only_queues_refresh(coordinator):
    c = coordinator
    payload = {"action": "opened", "repository": {"full_name": "EigenformAI/test"}, "number": 1}
    assert c.delivery("delivery-1", "pull_request", payload, 2000)
    assert not c.delivery("delivery-1", "pull_request", payload, 2001)
    assert c.pending_refreshes() == [("EigenformAI/test", 1)]
    assert c.workflow(100) is None


def test_jitter_duplicate_claims_restarts_keep_one_identity(tmp_path):
    path = tmp_path / "state.sqlite"
    config = {"organization_id": 97932676, "author_id": 34589654}
    c = Coordinator(path, config)
    c.enable(500)
    c.observe(pr(), 1000)
    barrier = threading.Barrier(8)

    def claim(_):
        worker = Coordinator(path, config)
        barrier.wait()
        time.sleep(random.random() / 100)
        return worker.claim(100)["attempt_id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        identities = list(executor.map(claim, range(8)))
    assert len(set(identities)) == 1


def test_grants_bind_epoch_candidate_and_live_identity(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    attempt = c.claim(100)
    status = {"state": "ready_to_publish", "quiescent": True,
              "publication_state": "ready_to_publish", "candidate_sha": "c" * 40}
    c.accept_status(attempt["attempt_id"], status)
    grant = c.authorize(attempt["attempt_id"], pr(), at=2000)
    assert grant["expires_at"] == 2060
    assert grant["input_head_sha"] == "a" * 40
    c.control("pause", 100, reason="inspect candidate")
    assert c.authorize(attempt["attempt_id"], pr(), at=2001) is None
    c.control("resume", 100, reason="continue")
    assert c.authorize(attempt["attempt_id"], pr(), at=2002)["authorization_epoch"] > grant["authorization_epoch"]
    moved = pr()
    moved["head"]["sha"] = "d" * 40
    assert c.authorize(attempt["attempt_id"], moved, at=2003) is None


def test_incident_holds_same_branch_but_not_other_branches(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    first = c.claim(100)
    c.accept_status(first["attempt_id"], {"state": "published_with_concern", "quiescent": True,
                                         "publication_state": "published"})
    assert c.claim(100) is None
    c.observe(pr(102, ref="other", number=3), 1001)
    assert c.claim(102)


def test_ready_webhook_uses_receipt_time_but_requires_live_confirmation(coordinator):
    c = coordinator
    c.enable(1500)
    c.observe(pr(draft=True), 1600)
    c.delivery("ready-1", "pull_request", {"action": "ready_for_review", "number": 1,
               "repository": {"full_name": "EigenformAI/test"}, "pull_request": {"id": 100}}, 1700)
    assert c.workflow(100)["eligible_at"] is None
    c.observe(pr(), 1800)
    assert c.workflow(100)["eligible_at"] == 1700
    assert c.workflow(100)["feedback_due_at"] == 2600


def test_acknowledged_incident_does_not_repause_on_duplicate_status(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    attempt = c.claim(100)
    status = {"state": "published_with_concern", "quiescent": True, "publication_state": "published"}
    c.accept_status(attempt["attempt_id"], status)
    c.control("resume", 100, reason="incident examined and accepted")
    c.accept_status(attempt["attempt_id"], status)
    assert c.workflow(100)["lifecycle_state"] == "active"


def test_retarget_and_author_change_revoke_publication(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {"state": "ready_to_publish", "quiescent": True,
                    "publication_state": "ready_to_publish", "candidate_sha": "c" * 40})
    retargeted = pr()
    retargeted["base"]["ref"] = "release"
    c.observe(retargeted, 2100)
    assert c.workflow(100)["lifecycle_state"] == "paused"
    assert c.authorize(attempt["attempt_id"], retargeted) is None
    c.observe(pr() | {"user": {"id": 42}}, 2200)
    assert c.workflow(100)["lifecycle_state"] == "cancel_requested"


def test_worker_acknowledgement_cannot_replace_attempt_identity(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    attempt = c.claim(100)
    with pytest.raises(Blocked, match="mismatch"):
        c.accept_status(attempt["attempt_id"], {"attempt_id": "other", "state": "noop"})
    assert c.claim(100)["attempt_id"] == attempt["attempt_id"]


def test_full_workflow_stops_after_one_feedback_and_conflict_pass(coordinator):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    kinds = []
    for _ in range(4):
        attempt = c.claim(100, at=2000)
        kinds.append(attempt["stage"])
        c.accept_status(attempt["attempt_id"], {"state": "noop", "quiescent": True,
                                               "publication_state": "not_published"})
    assert kinds == ["deep_modules", "cleanup", "feedback", "conflicts"]
    assert c.claim(100, at=2001) is None
    updated = pr()
    updated["head"]["sha"] = "f" * 40
    c.observe(updated, 3000)
    assert c.claim(100, at=3000) is None
    assert c.workflow(100)["lifecycle_state"] == "completed"


def test_closed_excluded_pr_remains_excluded_and_cannot_bypass_enrollment(coordinator):
    c = coordinator
    c.enable(1500)
    c.observe(pr(), 1600)
    closed = pr()
    closed["state"] = "closed"
    c.observe(closed, 1700)
    c.observe(pr(), 1800)
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    with pytest.raises(Blocked, match="enroll"):
        c.control("retry-stage", 100, "cleanup", "not a backfill")
    c.control("enroll", 100, reason="approved backfill")
    assert c.claim(100)


def test_nondefault_timeouts_are_part_of_immutable_worker_spec(coordinator):
    c = coordinator
    c.config.update(stage_deadline=3600, review_deadline=300, verification_deadline=600)
    c.enable(500)
    c.observe(pr(), 1000)
    attempt = c.claim(100)
    assert attempt["limits"] == {"stage_seconds": 3600, "review_seconds": 300, "verification_seconds": 600}


@pytest.mark.parametrize("scope", [
    {"allow_pr_ids": [1]}, {"allow_repository_ids": [78]},
    {"allow_pr_ids": ["100"]}, {"allow_repository_ids": ["77"]},
])
def test_claim_scope_uses_exact_immutable_ids_and_exclusion_is_resumable(coordinator, scope):
    c = coordinator
    c.config.update(scope)
    c.enable(500)
    c.observe(pr(), 1000)
    assert c.claim(100) is None
    assert c.workflow(100)["lifecycle_state"] == "paused"
    assert all(stage["state"] == "pending" for stage in c.stages(100))
    with pytest.raises(Blocked, match="scope"):
        c.control("resume", 100, reason="cannot bypass configured scope")
    with pytest.raises(Blocked, match="scope"):
        c.control("retry-stage", 100, "deep_modules", reason="cannot bypass configured scope")
    c.config.update(allow_pr_ids=[100], allow_repository_ids=[77])
    c.observe(pr(), 2100)
    assert c.claim(100) is None  # Scope changes never implicitly resume a hold.
    c.control("resume", 100, reason="explicitly expanded supervised pilot")
    assert c.claim(100)["pr_id"] == 100


@pytest.mark.parametrize("scope", [{"allow_pr_ids": [101]}, {"allow_repository_ids": [78]}])
def test_changed_scope_revokes_existing_attempt_and_publication_grants(coordinator, scope):
    c = coordinator
    c.enable(500)
    c.observe(pr(), 1000)
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {"state": "ready_to_publish", "quiescent": True,
                    "publication_state": "ready_to_publish", "candidate_sha": "c" * 40})
    grant = c.authorize(attempt["attempt_id"], pr())
    c.config.update(scope)
    assert c.authorize(attempt["attempt_id"], pr()) is None
    assert c.claim(100) is None
    assert c.workflow(100)["lifecycle_state"] == "paused"
    c.config.update(allow_pr_ids=[100], allow_repository_ids=[77])
    c.control("resume", 100, reason="restore exact pilot enrollment")
    assert c.claim(100)["attempt_id"] == attempt["attempt_id"]
    assert c.authorize(attempt["attempt_id"], pr())["authorization_epoch"] > grant["authorization_epoch"]


def test_baseline_scope_exclusion_requires_permitted_explicit_enrollment(coordinator):
    c = coordinator
    c.config.update(allow_pr_ids=[1], allow_repository_ids=[77])
    c.enable(1500)
    c.observe(pr(), 1600)
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    assert c.claim(100) is None
    with pytest.raises(Blocked, match="scope"):
        c.control("enroll", 100, reason="wrong PR ID is not enrollment")
    c.config["allow_pr_ids"] = [100]
    c.observe(pr(), 1800)
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    with pytest.raises(Blocked, match="enroll"):
        c.control("retry-stage", 100, "deep_modules", reason="not explicit enrollment")
    c.control("enroll", 100, reason="approved exact immutable pilot PR")
    assert c.claim(100)


def test_scoped_draft_hold_keeps_real_ready_time_and_requires_operator_resume(coordinator):
    c = coordinator
    c.config.update(allow_pr_ids=[101], allow_repository_ids=[77])
    c.enable(500)
    c.observe(pr(draft=True), 1100)
    assert c.workflow(100)["lifecycle_state"] == "paused"
    c.observe(pr(draft=True), 1200)
    assert c.workflow(100)["eligible_at"] is None
    c.config["allow_pr_ids"] = [100]
    c.observe(pr(), 2500)
    assert c.workflow(100)["eligible_at"] == 2500
    assert c.workflow(100)["feedback_due_at"] == 3400
    assert c.claim(100) is None
    c.control("resume", 100, reason="explicitly allow ready pilot PR")
    assert c.claim(100)


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_scoped_baseline_cannot_be_laundered_through_other_controls(coordinator, action):
    c = coordinator
    c.config.update(allow_pr_ids=[101], allow_repository_ids=[77])
    c.enable(1500)
    c.observe(pr(), 1600)
    c.control(action, 100, reason="hold excluded pilot PR")
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    c.config["allow_pr_ids"] = [100]
    with pytest.raises(Blocked):
        c.control("resume", 100, reason="scope changes are not explicit enrollment")
    with pytest.raises(Blocked, match="enroll"):
        c.control("retry-stage", 100, "deep_modules", reason="retry is not enrollment")
    c.control("enroll", 100, reason="explicitly enroll permitted pilot PR")
    assert c.claim(100)


@pytest.mark.parametrize("visibility", [False, None, 1])
def test_private_scope_skips_public_and_missing_visibility(coordinator, visibility):
    c = coordinator
    c.config["private_repositories_only"] = True
    c.enable(500)
    value = pr()
    value["base"]["repo"]["private"] = visibility
    c.observe(value)
    assert c.workflow(100) is None
    assert c.claim(100) is None


def test_private_visibility_drift_revokes_grant_pauses_and_never_auto_resumes(coordinator):
    c = coordinator
    c.config["private_repositories_only"] = True
    c.enable(500)
    value = pr()
    value["base"]["repo"]["private"] = True
    value["head"]["repo"]["private"] = True
    c.observe(value)
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {"state": "ready_to_publish", "quiescent": True,
                    "publication_state": "ready_to_publish", "candidate_sha": "c" * 40})
    grant = c.authorize(attempt["attempt_id"], value)
    assert grant
    public = pr()
    public["base"]["repo"]["private"] = False
    assert c.authorize(attempt["attempt_id"], public) is None
    c.observe(public)
    assert c.workflow(100)["lifecycle_state"] == "paused"
    assert c.claim(100) is None
    with pytest.raises(Blocked, match="scope"):
        c.control("resume", 100, reason="public is not eligible")
    c.observe(value)
    assert c.claim(100) is None
    c.control("resume", 100, reason="private visibility restored and inspected")
    assert c.claim(100)["attempt_id"] == attempt["attempt_id"]
    assert c.authorize(attempt["attempt_id"], value)["authorization_epoch"] > grant["authorization_epoch"]


def test_private_scope_keeps_startup_baseline_exclusion(coordinator):
    c = coordinator
    c.config["private_repositories_only"] = True
    c.enable(1500)
    value = pr()
    value["base"]["repo"]["private"] = True
    value["head"]["repo"]["private"] = True
    c.observe(value)
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    assert c.claim(100) is None


@pytest.mark.parametrize("private", [False, None, 1])
def test_private_scope_requires_private_head_for_claims_controls_and_grants(coordinator, private):
    c = coordinator
    c.config["private_repositories_only"] = True
    c.enable(500)
    value = pr()
    value["base"]["repo"]["private"] = True
    value["head"]["repo"]["private"] = True
    c.observe(value)
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {"state": "ready_to_publish", "quiescent": True,
                    "publication_state": "ready_to_publish", "candidate_sha": "c" * 40})
    value["head"]["repo"]["private"] = private
    assert c.authorize(attempt["attempt_id"], value) is None
    c.observe(value)
    assert c.workflow(100)["lifecycle_state"] == "paused"
    assert c.claim(100) is None
    with pytest.raises(Blocked, match="scope"):
        c.control("resume", 100, reason="public head cannot be resumed")


@pytest.mark.parametrize("assurance", [None, True, 1, [], {}, "unknown"])
def test_worker_execution_assurance_rejects_malformed_status_without_mutation(coordinator, assurance):
    c = coordinator
    c.enable(500)
    c.observe(pr())
    attempt = c.claim(100)
    with pytest.raises(Blocked, match="assurance"):
        c.accept_status(attempt["attempt_id"], {"state": "noop", "quiescent": True,
                        "publication_state": "not_published", "execution_assurance": assurance})
    assert c.attempt(attempt["attempt_id"])["state"] == "waiting_worker"


@pytest.mark.parametrize("assurance", ["strict", "best_effort"])
def test_worker_execution_assurance_is_preserved_in_attempt_result(coordinator, assurance):
    c = coordinator
    c.enable(500)
    c.observe(pr())
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {"state": "noop", "quiescent": True,
                    "publication_state": "not_published", "execution_assurance": assurance})
    assert c.attempt(attempt["attempt_id"])["result"]["execution_assurance"] == assurance
