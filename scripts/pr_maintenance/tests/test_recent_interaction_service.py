import pytest

from pr_maintenance.common import digest
from pr_maintenance.coordinator import Coordinator
from pr_maintenance.github import GitHubError
from pr_maintenance.service import Service
from test_coordinator import pr
from test_service import Outbox, Worker


NOW = 1800000
REPOSITORY = "EigenformAI/test"


class ActivityGitHub:
    def __init__(self, *pull_requests, active=()):
        self.payloads = {value["number"]: value for value in pull_requests}
        self.repository = pr()["base"]["repo"] | {"updated_at": NOW}
        self.active = set(active)
        self.activity_error = None
        self.activity_calls = []
        self.fetches = []
        self.listings = []

    def repositories(self):
        return [self.repository]

    def recent_interactions(self, since, until):
        self.activity_calls.append((since, until))
        if self.activity_error:
            raise self.activity_error
        return self.active

    def get_pr(self, repository, number):
        self.fetches.append((repository, number))
        return self.payloads[number]

    def open_prs(self, repository):
        self.listings.append(("open", repository))
        return list(self.payloads.values())

    def updated_prs(self, repository, since):
        self.listings.append(("updated", repository, since))
        return list(self.payloads.values())


def coordinator(tmp_path, enabled_at=500):
    c = Coordinator(tmp_path / "state.sqlite", {
        "allow_repositories": [REPOSITORY], "recent_interaction_days": 7,
    })
    c.enable(enabled_at)
    return c


def inventories(c):
    return [dict(row) for row in c.db.execute("SELECT * FROM inventories ORDER BY repository_id")]


def completed_inventory(c):
    c.db.execute("INSERT INTO inventories VALUES (77,?,1,1500,1500)", (REPOSITORY,))
    c.db.execute("INSERT INTO meta VALUES ('baseline_complete','1500')")


@pytest.mark.parametrize("inventoried", [False, True])
def test_inactive_repository_does_not_discover_or_change_inventory(tmp_path, inventoried):
    c = coordinator(tmp_path)
    if inventoried:
        completed_inventory(c)
    before = inventories(c)
    # Repository-wide updates, including other people's work, are not author evidence.
    github = ActivityGitHub(pr())

    refreshed = Service(c, github, None, Outbox(), now=lambda: NOW).scan()

    assert github.listings == []
    assert inventories(c) == before
    assert c.workflow(100) is None
    assert refreshed == set()
    assert github.activity_calls == [(NOW - 7 * 86400, NOW)]
    assert c.meta("baseline_complete") == ("1500" if inventoried else str(NOW))


@pytest.mark.parametrize("source_fails", [False, True])
def test_tracked_inactive_workflow_refreshes_and_advances_on_tick(tmp_path, source_fails):
    c = coordinator(tmp_path)
    c.config["execution_enabled"] = True
    c.observe(pr(), 1000)
    completed_inventory(c)
    first = c.claim(100, at=1100)
    finished = {"attempt_id": first["attempt_id"], "spec_hash": digest(first),
                "state": "noop", "quiescent": True, "publication_state": "not_published"}
    c.accept_status(first["attempt_id"], finished)
    live = pr()
    live["head"]["sha"] = "c" * 40
    github = ActivityGitHub(live, pr(101, number=2))
    if source_fails:
        github.activity_error = GitHubError("GitHub API transport failed")

    class RecordingWorker(Worker):
        def __init__(self):
            super().__init__([finished])
            self.submitted = []

        def submit(self, spec):
            self.submitted.append(spec)
            return {"state": "running", "quiescent": False, "publication_state": "not_published"}

    worker = RecordingWorker()
    before = inventories(c)
    Service(c, github, worker, Outbox(), now=lambda: NOW).tick()

    assert c.workflow(100)["lifecycle_state"] == "active"
    assert c.workflow(100)["observed_at"] == NOW
    assert [(spec["pr_id"], spec["stage"], spec["input_head_sha"]) for spec in worker.submitted] == [
        (100, "cleanup", "c" * 40),
    ]
    assert github.listings == []
    assert inventories(c) == before
    assert c.workflow(101) is None
    assert c.meta("last_tick") == str(NOW)


def test_unknown_webhook_cannot_bypass_inactivity_but_known_refresh_is_allowed(tmp_path):
    c = coordinator(tmp_path)
    c.observe(pr(), 1000)
    for number in (1, 2):
        c.delivery(f"delivery-{number}", "pull_request", {
            "action": "synchronize", "number": number,
            "repository": {"full_name": REPOSITORY},
        }, NOW - 1)
    github = ActivityGitHub(pr(), pr(101, number=2))

    refreshed = Service(c, github, None, Outbox(), now=lambda: NOW).scan()

    assert github.fetches and all(number == 1 for _, number in github.fetches)
    assert c.workflow(101) is None
    assert c.workflow(100)["lifecycle_state"] == "active"
    assert c.workflow(100)["observed_at"] == NOW
    assert refreshed == {100}
    assert (REPOSITORY, 1) not in c.pending_refreshes()
    assert github.listings == []


@pytest.mark.parametrize("inventoried", [False, True])
def test_reactivation_catches_up_without_moving_original_enablement_baseline(tmp_path, inventoried):
    c = coordinator(tmp_path, enabled_at=1500)
    if inventoried:
        completed_inventory(c)
    github = ActivityGitHub(pr(), pr(101, created=1600, number=2))
    service = Service(c, github, None, Outbox(), now=lambda: NOW)
    before = inventories(c)

    service.scan()
    assert github.listings == []
    assert inventories(c) == before
    initial_baseline = c.meta("baseline_complete")
    github.active = {77}
    service.now = lambda: NOW + 60
    refreshed = service.scan()

    expected = ("updated", REPOSITORY, 900) if inventoried else ("open", REPOSITORY)
    assert github.listings == [expected]
    assert refreshed == {100, 101}
    assert c.workflow(100)["lifecycle_state"] == "baselined_excluded"
    assert c.workflow(101)["lifecycle_state"] == "active"
    assert c.workflow(101)["eligible_at"] == 1600
    assert c.meta("enabled_at") == "1500"
    assert c.meta("baseline_complete") == initial_baseline
    assert inventories(c)[0]["cursor"] == inventories(c)[0]["last_success"] == NOW + 60


@pytest.mark.parametrize("inventoried", [False, True])
def test_activity_source_error_retains_known_refresh_and_never_falls_back_to_broad_scan(tmp_path, inventoried):
    c = coordinator(tmp_path)
    c.observe(pr(), 1000)
    if inventoried:
        completed_inventory(c)
    c.delivery("unknown-pr", "pull_request", {
        "action": "opened", "number": 2, "repository": {"full_name": REPOSITORY},
    }, NOW - 1)
    github = ActivityGitHub(pr(), pr(101, number=2))
    github.activity_error = GitHubError("GitHub API transport failed", status=503, retry_at=NOW + 3600)
    outbox = Outbox()
    before = inventories(c)

    refreshed = Service(c, github, None, outbox, now=lambda: NOW).scan()

    assert refreshed == {100}
    assert github.fetches and all(number == 1 for _, number in github.fetches)
    assert github.listings == []
    assert inventories(c) == before
    assert c.workflow(101) is None
    assert c.workflow(100)["lifecycle_state"] == "active"
    assert c.workflow(100)["observed_at"] == NOW
    assert c.meta("baseline_complete") == ("1500" if inventoried else None)
    message = "\n".join(outbox.messages.values())
    assert any(word in message.lower() for word in ("activity", "interaction", "contribution"))
    assert "Could not connect to GitHub" in message
    assert "HTTP 503" in message
    assert "Retry:" in message and "Action:" in message
    assert "Could not get the repository list" not in message


@pytest.mark.parametrize("setting", ["missing", None])
def test_missing_or_disabled_activity_filter_preserves_existing_discovery(tmp_path, setting):
    c = coordinator(tmp_path)
    if setting == "missing":
        del c.config["recent_interaction_days"]
    else:
        c.config["recent_interaction_days"] = setting
    github = ActivityGitHub(pr())
    github.activity_error = GitHubError("must not consult the disabled activity source")

    refreshed = Service(c, github, None, Outbox(), now=lambda: NOW).scan()

    assert github.activity_calls == []
    assert github.listings == [("open", REPOSITORY)]
    assert refreshed == {100}
    assert c.workflow(100)["lifecycle_state"] == "active"
    assert inventories(c)[0]["cursor"] == NOW
    assert c.meta("baseline_complete") == str(NOW)
