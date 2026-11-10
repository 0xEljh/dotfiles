import pytest

from pr_maintenance.common import Blocked, canonical, check_secrets, digest
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


@pytest.mark.parametrize("status,retry_at,action", [
    (403, None, "permissions"),
    (503, 3600, "No immediate action"),
    (None, 3600, "No immediate action"),
])
def test_personal_inventory_access_failure_defers_without_cancelling_work(tmp_path, status, retry_at, action):
    from pr_maintenance.github import GitHubError

    c = coordinator(tmp_path)

    class GitHub:
        def repositories(self):
            raise GitHubError("GitHub inventory unavailable", status=status, retry_at=retry_at)

    outbox = Outbox()
    Service(c, GitHub(), None, outbox, now=lambda: 2000).tick()
    assert c.workflow(100)["lifecycle_state"] == "active"
    assert c.meta("api_deferred_at") == "2000"
    assert c.meta("last_tick") == "2000"
    message = next(iter(outbox.messages.values()))
    assert "Could not get the repository list from GitHub" in message
    assert "Reason: GitHub inventory unavailable" in message
    assert action in message
    assert "No new maintenance work or publication was authorized during this poll" in message
    assert "Existing attempts were not cancelled" in message
    if status:
        assert f"HTTP {status}" in message
    if retry_at:
        assert "no earlier than 1970-01-01 01:00:00 UTC" in message
    else:
        assert "Retry: Automatically on a scheduled poll" in message


@pytest.mark.parametrize("inventoried", [False, True])
@pytest.mark.parametrize("diagnostic,status,retry_at,reason,action", [
    ("GitHub API request deferred", 503, 3600, "HTTP 503", "No immediate action"),
    ("GitHub API transport failed", None, 3600, "Could not connect to GitHub", "network"),
    ("personal authentication or access denied", 403, None, "HTTP 403", "permissions"),
    ("GitHub API request failed (HTTP 404)", 404, None, "HTTP 404", "still exists"),
    ("GitHub API returned invalid JSON", None, None, "invalid JSON", "diagnostics"),
])
def test_scan_failure_explains_repository_reason_retry_and_impact(
    tmp_path, inventoried, diagnostic, status, retry_at, reason, action,
):
    from pr_maintenance.github import GitHubError

    c = coordinator(tmp_path)
    c.config["allow_repositories"] = ["EigenformAI/test"]
    if inventoried:
        c.db.execute("INSERT INTO inventories(repository_id,repository,inventoried,cursor,last_success) "
                     "VALUES (77,'EigenformAI/test',1,1500,1500)")
        c.db.execute("INSERT INTO meta VALUES ('baseline_complete','1500')")

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            yield pr(101, created=1600, number=2)
            raise GitHubError(diagnostic, status=status, retry_at=retry_at)

        def updated_prs(self, repo, since):
            assert since == 900
            return self.open_prs(repo)

    outbox = Outbox()
    refreshed = Service(c, GitHub(), None, outbox, now=lambda: 2000).scan()
    message = next(iter(outbox.messages.values()))
    assert "PR maintenance: EigenformAI/test\nhttps://github.com/EigenformAI/test" in message
    assert "Could not finish checking for new or updated PRs" in message
    assert "Reason:" in message and reason in message
    assert "Action:" in message and action in message
    assert "The next scan rechecks the unfinished period" in message
    if retry_at:
        assert "no earlier than 1970-01-01 01:00:00 UTC" in message
    else:
        assert "Retry: Automatically on a scheduled poll" in message
    if inventoried:
        assert "Discovery of new or updated PRs is delayed" in message
        assert "PRs refreshed separately can still proceed" in message
    else:
        assert "Initial PR inventory is incomplete" in message
        assert "new maintenance work will not start" in message
        assert not c.meta("baseline_complete")
    inventory = c.db.execute("SELECT * FROM inventories WHERE repository_id=77").fetchone()
    assert inventory["inventoried"] == inventoried
    assert inventory["cursor"] == inventory["last_success"] == (1500 if inventoried else None)
    assert 100 in refreshed and 101 in refreshed


@pytest.mark.parametrize("malformed", [False, True])
def test_live_pr_read_failure_identifies_pr_and_does_not_expose_arbitrary_exception(tmp_path, malformed):
    from pr_maintenance.github import GitHubError

    c = coordinator(tmp_path)
    c.config["allow_repositories"] = ["EigenformAI/test"]

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            if malformed:
                raise ValueError("private upstream response")
            raise GitHubError("GitHub API request failed (HTTP 404)", status=404)

        def open_prs(self, repo):
            return []

    outbox = Outbox()
    refreshed = Service(c, GitHub(), None, outbox, now=lambda: 2000).scan()
    message = next(iter(outbox.messages.values()))
    assert "PR maintenance: EigenformAI/test #1\n" + pr()["html_url"] in message
    assert "Could not refresh this PR from GitHub" in message
    assert "No new work or publication will be authorized for this PR without a fresh read" in message
    assert "Retry: Automatically on a scheduled poll" in message
    assert "private upstream response" not in message
    assert ("malformed PR data" if malformed else "HTTP 404") in message
    assert 100 not in refreshed


def test_scan_notice_delivers_context_once_without_rewriting_legacy_report(tmp_path):
    from pr_maintenance.github import GitHubError
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    c.config["allow_repositories"] = ["EigenformAI/test"]
    outbox = DurableOutbox(c.db)
    legacy = "PR maintenance: repository ID 77 scan deferred; cursor retained."
    outbox.enqueue("scan:77", None, legacy)
    assert outbox.deliver(lambda _: 1) == 1

    class GitHub:
        retry_at = 3600

        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            raise GitHubError("GitHub API transport failed", retry_at=self.retry_at)

    github = GitHub()
    service = Service(c, github, None, outbox, now=lambda: 2000)
    service.scan()
    github.retry_at = 7200
    service.scan()
    delivered = []
    assert outbox.deliver(lambda body: delivered.append(body) or 2) == 1
    assert "EigenformAI/test" in delivered[0]
    assert "Could not connect to GitHub" in delivered[0]
    assert "01:00:00 UTC" in delivered[0]
    assert c.db.execute("SELECT text FROM reports WHERE report_key='scan:77'").fetchone()[0] == legacy
    assert outbox.status()["sent_reports"] == 2


def test_scan_error_credential_match_is_withheld_before_delivery(tmp_path):
    from pr_maintenance.github import GitHubError
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    c.config["allow_repositories"] = ["EigenformAI/test"]
    token = "ghp_" + "x" * 30

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return pr()

        def open_prs(self, repo):
            raise GitHubError("x " * 200 + token, status=503)

    outbox = DurableOutbox(c.db)
    Service(c, GitHub(), None, outbox, now=lambda: 2000).scan()
    delivered = []
    assert outbox.deliver(lambda body: delivered.append(body) or 1) == 1
    assert "Notification withheld by outbound credential filter" in delivered[0]
    assert token not in delivered[0]
    assert token in next((tmp_path / "withheld-reports").glob("*.txt")).read_text()


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

    outbox = Outbox()
    refreshed = Service(c, GitHub(), None, outbox).scan()
    assert 102 in refreshed and 103 in refreshed
    assert c.workflow(101) is None
    assert c.db.execute("SELECT cursor FROM inventories WHERE repository_id=77").fetchone()[0] is None
    assert c.db.execute("SELECT inventoried FROM inventories WHERE repository_id=78").fetchone()[0] == 1
    assert c.meta("baseline_complete") is None
    message = next(iter(outbox.messages.values()))
    assert "PR maintenance: EigenformAI/test\nhttps://github.com/EigenformAI/test" in message
    assert "malformed PR data" in message
    assert "Retry: Automatically on a scheduled poll" in message
    assert "Action:" in message
    assert "cursor" not in message


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
    assert "Background processes may still be running: Cleanup." in summary


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
    assert all(value in message for value in ("Unexpected post-push observation", pr()["html_url"],
                                              "Background processes may still be running"))
    assert attempt["attempt_id"] not in message
    assert "Inspection:" not in message


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


def test_secret_notice_retains_exact_private_text_and_delivers_inspection_location(tmp_path, monkeypatch):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    monkeypatch.setattr("socket.gethostname", lambda: "coordinator-test")
    c = coordinator(tmp_path)
    outbox = DurableOutbox(c.db)
    service = Service(c, None, None, outbox)
    token = "1234567890:" + "A" * 35
    key = "notice:100"
    text = f"Private diagnostic\nDiagnostic contains {token}\nInspect this exact text"
    with pytest.raises(Blocked, match="possible secret"):
        outbox.enqueue(key, 100, text)
    for _ in range(2):
        service.notice(key, 100, text)

    reports = c.db.execute("SELECT report_key,text FROM reports").fetchall()
    assert len(reports) == 1
    report_key, body = reports[0]
    artifact = c.path.parent / "withheld-reports" / f"{digest(key)}.txt"
    assert artifact.is_file()
    assert artifact.read_text() == text
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert artifact.parent.stat().st_mode & 0o777 == 0o700
    assert all(value in body for value in ("EigenformAI/test #1", "coordinator-test", str(artifact),
                                           "Line: 2", "not a confirmed credential leak", "not a repository scan"))
    assert token not in report_key + body
    check_secrets(report_key + "\n" + body)
    delivered = []
    assert outbox.deliver(lambda part: delivered.append(part) or 123) == 1
    assert token not in delivered[0]
    service.notice(key, 100, f"A later {token}")
    assert artifact.read_text() == text
    assert c.db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    assert token not in "\n".join(c.db.iterdump())


def test_generated_reference_match_delivers_safe_body_without_secret_alarm(tmp_path):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    outbox = DurableOutbox(c.db)
    service = Service(c, None, None, outbox)
    key = "queue:5138916024:eff4d3e8-6af3-4c94-9d8b-a5e49ecf6ec0"
    with pytest.raises(Blocked):
        check_secrets(key)
    for _ in range(2):
        service.notice(key, 100, "Waiting for the retained attempt; inspect PR #71")
    report_key, text = c.db.execute("SELECT report_key,text FROM reports").fetchone()
    assert key not in report_key
    assert text == "Waiting for the retained attempt; inspect PR #71"
    delivered = []
    assert outbox.deliver(lambda body: delivered.append(body) or 123) == 1
    check_secrets(delivered[0])
    assert not (c.path.parent / "withheld-reports").exists()


def test_summary_filter_retains_rendered_match_not_unused_inputs(tmp_path):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    service = Service(c, None, None, DurableOutbox(c.db))
    token = "ghp_" + "x" * 30
    workflow = c.workflow(100)
    workflow["pr"]["body"] = token
    stages = [{"kind": "cleanup", "state": "blocked", "result": {"logs": token}}]
    service.summary("unused-inputs", workflow, stages, False)
    text = c.db.execute("SELECT text FROM reports WHERE report_key='unused-inputs'").fetchone()[0]
    assert "Initial maintenance summary" in text
    assert token not in text
    assert not (c.path.parent / "withheld-reports").exists()

    stages[0]["result"]["error"] = token
    service.summary("rendered-match", workflow, stages, False)
    artifact = c.path.parent / "withheld-reports" / f"{digest('rendered-match')}.txt"
    assert token in artifact.read_text()
    report = c.db.execute("SELECT text FROM reports WHERE report_key=?", (f"secret:{digest('rendered-match')}",)).fetchone()[0]
    assert str(artifact) in report
    assert token not in report


def test_withheld_notice_storage_failure_and_unsafe_metadata_do_not_echo_secrets(tmp_path, monkeypatch):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    token = "ghp_" + "x" * 30
    c.db.execute("UPDATE workflows SET repository=? WHERE pr_id=100", (token,))
    monkeypatch.setattr("socket.gethostname", lambda: token)
    # Make the diagnostic directory unwritable even when tests run as root.
    (c.path.parent / "withheld-reports").write_text("not a directory")
    outbox = DurableOutbox(c.db)
    Service(c, None, None, outbox).notice("storage-failure", 100, token)
    body = c.db.execute("SELECT text FROM reports").fetchone()[0]
    assert "could not retain" in body.lower()
    assert "permissions" in body
    assert token not in body
    assert outbox.deliver(lambda _: 123) == 1
    (c.path.parent / "withheld-reports").unlink()
    Service(c, None, None, outbox).notice("storage-failure", 100, token)
    retained = c.path.parent / "withheld-reports" / f"{digest('storage-failure')}.txt"
    assert retained.read_text() == token
    delivered = []
    assert outbox.deliver(lambda body: delivered.append(body) or 124) == 1
    assert str(retained) in delivered[0]
    assert token not in delivered[0]


def test_retained_notification_points_to_earliest_match_across_patterns(tmp_path):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    token = "ghp_" + "x" * 30
    text = f"Heading\n{token}\nLater private key header\n-----BEGIN PRIVATE KEY-----"
    Service(c, None, None, DurableOutbox(c.db)).notice("multiple-patterns", 100, text)
    body = c.db.execute("SELECT text FROM reports").fetchone()[0]
    assert "Line: 2 " in body
    assert token not in body


def test_corrupted_retained_notification_does_not_abort_reporting(tmp_path):
    from pr_maintenance.reporting import Outbox as DurableOutbox

    c = coordinator(tmp_path)
    artifact = c.path.parent / "withheld-reports" / f"{digest('corrupted')}.txt"
    artifact.parent.mkdir(mode=0o700)
    artifact.write_bytes(b"\xff")
    outbox = DurableOutbox(c.db)
    Service(c, None, None, outbox).notice("corrupted", 100, "ghp_" + "x" * 30)
    body = c.db.execute("SELECT text FROM reports").fetchone()[0]
    assert "could not retain" in body.lower()
    assert "encoding" in body
    assert artifact.read_bytes() == b"\xff"
    assert outbox.deliver(lambda _: 123) == 1


def test_tick_dispatches_all_independent_prs_while_owner_is_held(tmp_path):
    c = coordinator(tmp_path)
    c.config.update(execution_enabled=True, organization_wide=True)
    c.db.execute("INSERT INTO meta VALUES ('baseline_complete','1000')")
    owner = c.claim(100)
    held = {"attempt_id": owner["attempt_id"], "spec_hash": digest(owner),
            "state": "publication_unknown", "quiescent": False, "publication_state": "unknown"}
    payloads = {1: pr()}
    for number in range(2, 22):
        payloads[number] = pr(99 + number, ref=f"feature-{number}", number=number)
        c.observe(payloads[number], 1001)

    class GitHub:
        def repositories(self):
            return [pr()["base"]["repo"]]

        def get_pr(self, repo, number):
            return payloads[number]

        def open_prs(self, repo):
            return list(payloads.values())

    class ParallelWorker(Worker):
        def __init__(self):
            super().__init__([held])
            self.submitted = []

        def submit(self, spec):
            self.submitted.append(spec)
            return {"attempt_id": spec["attempt_id"], "spec_hash": digest(spec),
                    "state": "accepted", "quiescent": True, "publication_state": "not_published"}

    worker = ParallelWorker()
    Service(c, GitHub(), worker, Outbox(), now=lambda: 2000).tick()
    assert {spec["pr_id"] for spec in worker.submitted} == set(range(101, 121))
    assert all(spec["stage"] == "deep_modules" for spec in worker.submitted)
    assert c.attempt(owner["attempt_id"])["state"] == "publication_unknown"


def test_merged_unattempted_workflow_reports_once_with_earlier_queue_blocker(tmp_path):
    c = coordinator(tmp_path)
    owner = c.claim(100)
    c.observe(pr(101, ref="other", number=2), 1001)
    # Historical evidence from the former serialized dispatcher stays readable.
    c.db.execute("INSERT INTO queue_waits VALUES (?,?,?)", (101,
                 canonical({"repository": "EigenformAI/test", "number": 1,
                            "stage": "deep_modules", "attempt_id": owner["attempt_id"]}), 1001))
    # Keep the reason for the earlier wait even after its owner is resolved.
    c.accept_status(owner["attempt_id"], {"state": "blocked", "quiescent": True,
                    "publication_state": "not_published"})
    c.observe(pr(101, ref="other", number=2) | {"state": "closed", "merged": True}, 2200)
    outbox = Outbox()
    Service(c, None, None, outbox).reports()
    messages = [(key, text) for key, text in outbox.messages.items() if key.startswith(("initial:101:", "final:101:"))]
    assert len(messages) == 1
    key, text = messages[0]
    assert key.startswith("final:101:")
    assert "Maintenance never started" in text
    assert "PR merged" in text
    assert "Previously waiting" in text and pr()["html_url"] in text
    assert "Deep modules: Not started" in text
    assert "Verification: not recorded" not in text
    assert "Session: not recorded" not in text


def test_cancelled_workflow_with_execution_keeps_initial_summary_and_evidence(tmp_path):
    c = coordinator(tmp_path)
    attempt = c.claim(100)
    c.accept_status(attempt["attempt_id"], {"state": "cancelled", "quiescent": True,
                    "publication_state": "not_published", "session_id": "ses_real"})
    c.observe(pr() | {"state": "closed", "merged": True}, 2200)
    outbox = Outbox()
    Service(c, None, None, outbox).reports()
    assert len(outbox.messages) == 2
    final = next(text for key, text in outbox.messages.items() if key.startswith("final:"))
    assert "Deep modules: Cancelled" in final
    assert "Verification: not recorded" in final
    assert "Maintenance never started" not in final


def test_worker_recovery_reconciles_cancelled_owner_without_delaying_other_pr(tmp_path):
    c = coordinator(tmp_path)
    owner = c.claim(100)
    c.accept_status(owner["attempt_id"], {"state": "cancel_requested", "quiescent": False,
                    "publication_state": "not_published"})
    c.observe(pr(101, ref="other", number=2), 1001)
    c.control("cancel", 100, reason="Out-of-scope repository; retain candidate")
    assert c.workflow(100)["lifecycle_state"] == "cancel_requested"
    independent = c.claim(101)
    assert independent["stage"] == "deep_modules"
    recovery = {"attempt_id": owner["attempt_id"], "spec_hash": digest(owner), "state": "blocked",
                "quiescent": True, "publication_state": "not_published",
                "operator_recovery": {"reason": "Inspected; accept best-effort limitation"}}
    assert Service(c, None, Worker([recovery]), Outbox()).reconcile()
    assert c.workflow(100)["lifecycle_state"] == "cancelled"
    assert c.attempt(owner["attempt_id"])["result"]["operator_recovery"] == recovery["operator_recovery"]
    assert c.claim(101)["attempt_id"] == independent["attempt_id"]
