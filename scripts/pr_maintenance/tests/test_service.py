import pytest

from pr_maintenance.common import Blocked, check_secrets, digest
from pr_maintenance.coordinator import Coordinator
from pr_maintenance.service import Service
from test_coordinator import pr


class Worker:
    def __init__(self, attempts=None, ledger_id="stable-ledger"):
        self.attempts = attempts or []
        self.ledger_id = ledger_id

    def request(self, operation):
        assert operation == "collect"
        return {"attempts": self.attempts, "ledger_id": self.ledger_id}


class Outbox:
    def __init__(self):
        self.messages = {}

    def enqueue(self, key, workflow_id, text):
        self.messages.setdefault(key, text)


def coordinator(tmp_path):
    c = Coordinator(tmp_path / "state.sqlite", {})
    c.enable(500)
    c.observe(pr(), 1000)
    return c


def test_unacknowledged_submit_can_reuse_identity_after_offline_recovery(tmp_path):
    c = coordinator(tmp_path)
    service = Service(c, None, Worker(), Outbox())
    assert service.reconcile()
    attempt = c.claim(100)
    c.mark_submitted(attempt["attempt_id"])
    # The SSH connection died before the worker accepted anything.
    assert service.reconcile()
    assert c.claim(100)["attempt_id"] == attempt["attempt_id"]


def test_worker_ledger_replacement_blocks_even_with_no_attempts(tmp_path):
    c = coordinator(tmp_path)
    service = Service(c, None, Worker(), Outbox())
    assert service.reconcile()
    service.worker = Worker(ledger_id="replaced-ledger")
    assert not service.reconcile()
    assert "ledger" in c.meta("recovery_hold")


def test_accepted_attempt_disappearing_is_not_resubmitted(tmp_path):
    c = coordinator(tmp_path)
    attempt = c.claim(100)
    c.mark_submitted(attempt["attempt_id"])
    c.accept_status(attempt["attempt_id"], {"state": "running", "quiescent": False,
                                          "publication_state": "not_published"})
    service = Service(c, None, Worker(), Outbox())
    assert not service.reconcile()


def test_unknown_worker_attempt_blocks_after_coordinator_restore(tmp_path):
    c = coordinator(tmp_path)
    service = Service(c, None, Worker([{"attempt_id": "not-in-backup", "state": "noop", "quiescent": True,
                                       "publication_state": "not_published", "spec_hash": "f" * 64}]), Outbox())
    assert not service.reconcile()
    assert service.outbox.messages


def test_malformed_collect_sets_durable_hold_without_partial_acceptance(tmp_path):
    for index, response in enumerate((None, [], {"ledger_id": "stable-ledger", "attempts": "invalid"},
                                      {"ledger_id": "stable-ledger", "attempts": [{"attempt_id": "missing-state"}]})):
        c = Coordinator(tmp_path / f"state-{index}.sqlite", {})

        class MalformedWorker:
            def request(self, operation):
                return response

        service = Service(c, None, MalformedWorker(), Outbox())
        assert not service.reconcile()
        assert c.meta("recovery_hold")


def test_partial_inventory_preserves_cursor_and_does_not_finish_baseline(tmp_path):
    c = coordinator(tmp_path)
    c.config["allow_repositories"] = ["EigenformAI/test"]

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            yield pr(101, created=1500, number=2)
            raise OSError("page two failed")

    service = Service(c, GitHub(), None, Outbox())
    try:
        service.scan()
    except OSError:
        pass
    assert not c.meta("baseline_complete")
    inventory = c.db.execute("SELECT * FROM inventories").fetchone()
    assert not inventory["inventoried"]
    assert inventory["cursor"] is None


def test_narrowed_numeric_scope_pauses_instead_of_cancelling_missing_inventory(tmp_path):
    c = coordinator(tmp_path)
    c.config.update(allow_repository_ids=[78], allow_repositories=["EigenformAI/other"])

    class GitHub:
        def repositories(self):
            return []

    Service(c, GitHub(), None, Outbox()).scan()
    assert c.workflow(100)["lifecycle_state"] == "paused"
    assert c.stages(100)[0]["state"] == "pending"


def test_personal_inventory_access_failure_defers_without_cancelling_work(tmp_path):
    from pr_maintenance.github import GitHubError

    c = coordinator(tmp_path)

    class GitHub:
        def repositories(self):
            raise GitHubError("personal authentication or access denied", status=403)

    outbox = Outbox()
    Service(c, GitHub(), None, outbox, now=lambda: 2000).tick()
    assert c.workflow(100)["lifecycle_state"] == "active"
    assert c.meta("api_deferred_at") == "2000"
    assert c.meta("last_tick") == "2000"
    assert outbox.messages


def test_malformed_pr_does_not_stall_other_repositories_or_advance_bad_cursor(tmp_path):
    c = coordinator(tmp_path)
    c.config["organization_wide"] = True
    other_repo = dict(pr()["base"]["repo"], id=78, full_name="EigenformAI/other")
    good = pr(103, created=1600)
    good["base"]["repo"] = other_repo
    good["head"]["repo"] = other_repo

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"], other_repo]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            if repo == "EigenformAI/test":
                yield pr(101, created="not-a-timestamp")
                yield pr(102, created=1500)
            else:
                yield good

    refreshed = Service(c, GitHub(), None, Outbox()).scan()
    assert 102 in refreshed and 103 in refreshed
    assert c.workflow(101) is None
    assert c.db.execute("SELECT cursor FROM inventories WHERE repository_id=77").fetchone()[0] is None
    assert c.db.execute("SELECT inventoried FROM inventories WHERE repository_id=78").fetchone()[0] == 1
    assert c.meta("baseline_complete") is None


def test_lost_submit_ack_reconciles_same_attempt_without_resubmission(tmp_path):
    from pr_maintenance.transport import WorkerOffline

    c = coordinator(tmp_path)
    c.config.update({"execution_enabled": True, "allow_repositories": ["EigenformAI/test"]})

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            return [pr()]

        def updated_prs(self, repo, since):
            return []

    class LostAckWorker(Worker):
        submitted = []

        def submit(self, spec):
            self.submitted.append(spec["attempt_id"])
            self.attempts = [{"attempt_id": spec["attempt_id"], "spec_hash": digest(spec), "state": "running", "quiescent": False,
                              "publication_state": "not_published"}]
            raise WorkerOffline("ack lost")

    worker = LostAckWorker()
    service = Service(c, GitHub(), worker, Outbox(), now=lambda: 2000)
    service.tick()
    c.db.execute("DELETE FROM meta WHERE key='worker_retry_at'")
    Service(c, GitHub(), worker, service.outbox, now=lambda: 2001).tick()
    assert len(worker.submitted) == 1
    assert c.stages(100)[0]["state"] == "running"


def test_feedback_spool_is_hashed_not_copied_into_coordinator_ledger(tmp_path):
    c = coordinator(tmp_path)
    first = c.claim(100)
    c.accept_status(first["attempt_id"], {"state": "noop", "quiescent": True, "publication_state": "not_published"})
    second = c.claim(100)
    c.accept_status(second["attempt_id"], {"state": "noop", "quiescent": True, "publication_state": "not_published"})
    attempt = c.claim(100)
    snapshot = {"conversation": [{"body": "private review content"}], "started_at": 2000, "finished_at": 2001}
    c.attach_feedback(attempt["attempt_id"], snapshot)
    assert "private review content" not in c.db.execute("SELECT spec FROM attempts WHERE id=?", (attempt["attempt_id"],)).fetchone()[0]
    assert "private review content" in c.feedback_path(attempt["attempt_id"]).read_text()


def test_next_stage_refreshes_head_after_previous_push_is_reconciled(tmp_path):
    c = coordinator(tmp_path)
    c.config.update({"execution_enabled": True, "allow_repositories": ["EigenformAI/test"]})
    first = c.claim(100)
    c.mark_submitted(first["attempt_id"])

    class GitHub:
        head = "a" * 40

        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            value = pr()
            value["head"]["sha"] = self.head
            return value

        def open_prs(self, repo):
            return []

    github = GitHub()

    class PublishedWorker(Worker):
        submissions = []

        def request(self, operation):
            github.head = "c" * 40
            return {"ledger_id": self.ledger_id, "attempts": [{"attempt_id": first["attempt_id"], "spec_hash": digest(first),
                    "state": "pushed", "quiescent": True, "publication_state": "published"}]}

        def submit(self, spec):
            self.submissions.append(spec)
            return {"state": "running", "quiescent": False, "publication_state": "not_published"}

    worker = PublishedWorker()
    Service(c, github, worker, Outbox(), now=lambda: 2000).tick()
    assert worker.submissions[0]["stage"] == "cleanup"
    assert worker.submissions[0]["input_head_sha"] == "c" * 40


def test_final_base_recompute_is_bounded_and_preserves_prior_attempt(tmp_path):
    c = coordinator(tmp_path)
    c.config.update({"execution_enabled": True, "allow_repositories": ["EigenformAI/test"], "correction_rounds": 2})
    prior = []
    for _ in range(3):
        attempt = c.claim(100)
        status = {"attempt_id": attempt["attempt_id"], "spec_hash": digest(attempt), "state": "noop", "quiescent": True, "publication_state": "not_published"}
        c.accept_status(attempt["attempt_id"], status)
        prior.append(status)
    first = c.claim(100)
    assert first["stage"] == "conflicts"

    class GitHub:
        base = "e" * 40

        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            value = pr()
            value["base"]["sha"] = self.base
            return value

        def open_prs(self, repo):
            return []

        def updated_prs(self, repo, since):
            return []

    class BaseMovedWorker(Worker):
        submitted = []

        def submit(self, spec):
            self.submitted.append(spec)
            return {"state": "running", "quiescent": False, "publication_state": "not_published"}

    def blocked(attempt):
        return {"attempt_id": attempt["attempt_id"], "spec_hash": digest(attempt), "state": "blocked", "quiescent": True,
                "publication_state": "not_published", "retryable_reason": "base_advanced"}

    github = GitHub()
    worker = BaseMovedWorker([*prior, blocked(first)])
    service = Service(c, github, worker, Outbox(), now=lambda: 2000)
    service.tick()
    assert len(worker.submitted) == 1
    second = worker.submitted[0]
    assert second["attempt_id"] != first["attempt_id"]
    assert second["base_sha"] == "e" * 40
    assert c.attempt(first["attempt_id"])["state"] == "blocked"
    github.base = "f" * 40
    worker.attempts = [*prior, blocked(first), blocked(second)]
    service.tick()
    assert len(worker.submitted) == 1


def test_operator_incident_acknowledgement_crosses_both_ledgers(tmp_path):
    from pr_maintenance.common import STAGES
    from pr_maintenance.worker import Worker as LocalWorker

    c = coordinator(tmp_path)
    c.config.update({"execution_enabled": True, "allow_repositories": ["EigenformAI/test"],
                     "models": {s: "provider/model" for s in (*STAGES, "review")},
                     "agents": {s: "worker" for s in (*STAGES, "review")}})
    local = LocalWorker({"state_dir": str(tmp_path / "worker"), "checkout_root": str(tmp_path / "checkouts")})

    class Transport:
        def request(self, operation, attempt_id=None, **fields):
            return local.transport({"operation": operation, "attempt_id": attempt_id, **fields})

        def submit(self, spec):
            return local.submit(spec)

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            return []

    try:
        first = c.claim(100)
        local.submit(first)
        incident = local._update(first["attempt_id"], state="published_with_concern", publication_state="published", quiescent=True)
        c.accept_status(first["attempt_id"], incident)
        c.control("resume", 100, reason="publication incident inspected and accepted")
        assert c.claim(100) is None
        Service(c, GitHub(), Transport(), Outbox()).tick()
        assert c.stages(100)[1]["state"] == "accepted"
        assert local.status(first["attempt_id"])["incident_acknowledged"]
        assert local.status(first["attempt_id"])["state"] == "published_with_concern"
    finally:
        local.close()


def test_private_inventory_public_or_missing_repository_pauses_not_cancels(tmp_path):
    for index, inventory_public in enumerate((False, True)):
        c = Coordinator(tmp_path / f"private-{index}.sqlite", {
            "private_repositories_only": True, "organization_wide": True})
        c.enable(500)
        value = pr()
        value["base"]["repo"]["private"] = True
        value["head"]["repo"]["private"] = True
        c.observe(value)

        class GitHub:
            def repositories(self):
                return [value["base"]["repo"] | {"private": False}] if inventory_public else []

            def get_pr(self, repo, number):
                raise AssertionError("public content must not be read")

        Service(c, GitHub(), None, Outbox()).scan()
        assert c.workflow(100)["lifecycle_state"] == "paused"
        assert c.stages(100)[0]["state"] == "pending"
        assert c.claim(100) is None


def test_private_service_scope_requires_exact_visibility_owner_and_optional_names(tmp_path):
    c = Coordinator(tmp_path / "state.sqlite", {"organization_wide": True, "private_repositories_only": True})
    service = Service(c, None, None, Outbox())
    repository = pr()["base"]["repo"] | {"private": True}
    assert service.allowed(repository)
    assert not service.allowed(repository | {"private": False})
    assert not service.allowed(repository | {"private": None})
    assert not service.allowed(repository | {"owner": {"id": 42}})
    c.config["allow_repositories"] = ["EigenformAI/other"]
    assert not service.allowed(repository)


def test_private_reports_name_best_effort_and_disclaim_descendant_proof(tmp_path):
    c = coordinator(tmp_path)
    for assurance in ("strict", "best_effort"):
        attempt = c.claim(100)
        c.accept_status(attempt["attempt_id"], {"state": "noop", "quiescent": True,
                        "publication_state": "not_published", "execution_assurance": assurance})
    outbox = Outbox()
    Service(c, None, None, outbox).reports()
    summary = next(text for key, text in outbox.messages.items() if key.startswith("initial:"))
    assert "Deep modules: strict" in summary
    assert "Cleanup: best effort" in summary
    assert "does not prove descendants stopped" in summary


def test_recovered_readiness_notice_uses_explicit_utc_and_preserves_polling_delay_caveat(tmp_path):
    c = Coordinator(tmp_path / "state.sqlite", {"allow_repositories": ["EigenformAI/test"]})
    c.enable(500)
    c.observe(pr(draft=True), 1000)

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            return []

    outbox = Outbox()
    Service(c, GitHub(), None, outbox, now=lambda: 1793361600).scan()
    message = outbox.messages["recovered-ready:100"]
    assert message.startswith("PR maintenance: EigenformAI/test #1\n")
    assert "2026-10-30 12:00:00 UTC" in message
    assert "recovered by polling" in message
    assert "15-minute delay starts at this observation" in message
    assert "transition may have happened earlier" in message
    assert "1793361600" not in message


def test_published_with_concern_notice_requires_acknowledgement_without_claiming_unpublished(tmp_path):
    c = coordinator(tmp_path)
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {
        "state": "published_with_concern", "publication_state": "published", "quiescent": True,
        "error": "Unexpected post-push observation", "execution_assurance": "best_effort",
    })
    outbox = Outbox()
    Service(c, None, None, outbox).reports()
    message = outbox.messages[f"stage:{attempt['attempt_id']}:published_with_concern"]
    assert "Deep modules: Published with concern" in message
    assert "Changes were published; incident requires acknowledgement" in message
    assert "unpublished" not in message.lower()
    assert all(value in message for value in ("Unexpected post-push observation", attempt["attempt_id"],
                                              "best effort", "does not prove descendants stopped"))
    assert "\n\nInspection:\n- Host:" in message


def test_paused_private_workflow_reports_complete_ticks_without_replaying_attempt(tmp_path):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    pr_id = 5115037271
    c = Coordinator(tmp_path / "state.sqlite", {
        "private_repositories_only": True, "organization_wide": True,
        "execution_enabled": False,
    })
    c.enable(500)
    value = pr(pr_id)
    value["base"]["repo"]["private"] = True
    value["head"]["repo"]["private"] = True
    c.observe(value, 1000)
    spec = c.claim(pr_id, at=1100)
    c.accept_status(spec["attempt_id"], {
        "state": "cancel_requested", "quiescent": False,
        "publication_state": "not_published", "candidate_sha": "c" * 40,
    })
    retained = c.attempt(spec["attempt_id"])

    class GitHub:
        def repositories(self):
            return []  # The repository is no longer in the private inventory.

    outbox = DurableOutbox(c.db)
    service = Service(c, GitHub(), None, outbox, now=lambda: 2000)
    service.tick()
    assert c.meta("last_tick") == "2000"
    assert c.workflow(pr_id)["lifecycle_state"] == "paused"
    assert c.attempt(spec["attempt_id"]) == retained
    assert c.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
    report = c.db.execute("SELECT text FROM reports").fetchone()[0]
    assert "paused: repository absent from private inventory" in report
    assert "possible secret" not in report

    service.now = lambda: 2001
    service.tick()
    assert c.meta("last_tick") == "2001"
    assert c.db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    delivered = []
    assert outbox.deliver(lambda text: delivered.append(text) or 123) == 1
    assert "paused: repository absent from private inventory" in delivered[0]


@pytest.mark.parametrize("secret_in_key", [False, True])
def test_secret_notice_fallback_is_safe_deduplicated_and_deliverable(tmp_path, secret_in_key):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    outbox = DurableOutbox(c.db)
    service = Service(c, None, None, outbox)
    token = "1234567890:" + "A" * 35
    key = token if secret_in_key else "notice:100"
    text = "Private diagnostic" if secret_in_key else f"Diagnostic contains {token}"
    with pytest.raises(Blocked, match="possible secret"):
        outbox.enqueue(key, 100, text)
    for _ in range(2):
        service.notice(key, 100, text)

    reports = c.db.execute("SELECT report_key,text FROM reports").fetchall()
    assert len(reports) == 1
    report_key, body = reports[0]
    assert "possible secret detected" in body
    assert token not in report_key + body
    check_secrets(report_key + "\n" + body)
    delivered = []
    assert outbox.deliver(lambda part: delivered.append(part) or 123) == 1
    assert token not in delivered[0]
