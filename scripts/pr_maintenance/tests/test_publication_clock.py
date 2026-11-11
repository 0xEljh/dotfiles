import copy
from types import SimpleNamespace

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.coordinator import Coordinator
from pr_maintenance.health import snapshot
from pr_maintenance.service import Service
from pr_maintenance.worker import Worker
from test_coordinator import pr
from test_service import Outbox


@pytest.fixture
def handshake(tmp_path, monkeypatch):
    clock = {"worker": 2000.0, "coordinator": 2004.0, "delivery_delay": 0}
    monkeypatch.setattr("pr_maintenance.worker.time", SimpleNamespace(time=lambda: clock["worker"]))
    stages = ("deep_modules", "cleanup", "feedback", "conflicts", "review")
    c = Coordinator(tmp_path / "coordinator.sqlite", {
        "execution_enabled": True, "models": {stage: "provider/model" for stage in stages},
        "agents": {stage: "build" for stage in stages},
    },
                    now=lambda: clock["coordinator"])
    worker = Worker({"state_dir": str(tmp_path / "worker"), "execution_enabled": False})
    c.enable(500)
    c.observe(pr(), 1000)
    c.db.execute("INSERT INTO meta VALUES ('baseline_complete','1000')")
    spec = c.claim(100)
    worker.submit(spec)
    worker._update(spec["attempt_id"], state="ready_to_publish", candidate_sha="c" * 40)
    operations, grants = [], []
    alter_status = lambda value: value

    class Transport:
        def request(self, operation, attempt_id=None, **values):
            operations.append(operation)
            if operation == "authorize-publish":
                grants.append(copy.deepcopy(values["authorization"]))
                clock["worker"] += clock["delivery_delay"]
            response = worker.transport({"operation": operation, "attempt_id": attempt_id, **values})
            return alter_status(response) if operation == "status" else response

    service = Service(c, SimpleNamespace(get_pr=lambda *args: pr()), Transport(), Outbox(),
                      now=lambda: clock["coordinator"])
    monkeypatch.setattr(service, "scan", lambda: {100})
    result = SimpleNamespace(c=c, worker=worker, spec=spec, service=service, clock=clock,
                             operations=operations, grants=grants)

    def set_status_transform(transform):
        nonlocal alter_status
        alter_status = transform

    result.set_status_transform = set_status_transform
    yield result
    worker.close()
    c.db.close()


@pytest.mark.parametrize("offset", [-300, -5, 0, 5, 300])
def test_publication_handshake_uses_worker_clock_without_extending_limit(handshake, offset):
    h = handshake
    h.clock["coordinator"] = h.clock["worker"] + offset
    h.service.tick()
    assert h.grants[0]["expires_at"] == 2060
    assert h.worker._row(h.spec["attempt_id"])[1]["authorization"] == h.grants[0]
    assert h.c.meta("last_tick") == str(h.clock["coordinator"])
    assert h.c.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [
    ("worker_time", None), ("worker_time", True), ("worker_time", "2000"),
    ("worker_time", float("nan")), ("worker_time", float("inf")),
    ("attempt_id", "different-attempt"), ("spec_hash", "f" * 64),
    ("state", None), ("quiescent", None), ("candidate_sha", "d" * 40),
    ("publication_state", "not_published"), ("quiescent", False),
])
def test_publication_refuses_missing_clock_or_mismatched_fresh_status(handshake, field, value):
    h = handshake
    h.set_status_transform(lambda status: dict(status, **{field: value}))
    h.service.tick()
    assert not h.grants
    assert h.worker._row(h.spec["attempt_id"])[1].get("authorization") is None
    health = snapshot(h.c, {"oldest_pending_age": None}, at=h.clock["coordinator"])
    assert health["live"] and not health["healthy"]
    assert health["issue_codes"] == ["publication_rejected"]


def test_delivery_delay_consumes_window_and_replay_cannot_renew_it(handshake):
    h = handshake
    h.clock["delivery_delay"] = 60
    h.service.tick()
    assert h.grants[0]["expires_at"] == 2060
    h.clock["worker"] = 2061
    restarted = Worker(h.worker.config)
    try:
        with pytest.raises(Blocked, match="expired"):
            restarted.authorize(h.spec["attempt_id"], h.grants[0])
        assert restarted._row(h.spec["attempt_id"])[1].get("authorization") is None
    finally:
        restarted.close()
    assert snapshot(h.c, {"oldest_pending_age": None}, at=2004)["issue_codes"] == ["publication_rejected"]
    h.clock["delivery_delay"] = 0
    h.service.tick()
    assert h.grants[-1]["expires_at"] == 2121
    assert snapshot(h.c, {"oldest_pending_age": None}, at=2004)["healthy"]


@pytest.mark.parametrize("maximum", [None, True, "60", 0, -1, float("nan"), float("inf")])
def test_invalid_worker_lifetime_limit_never_authorizes(handshake, maximum):
    h = handshake
    h.worker.config["authorization_max_seconds"] = maximum
    h.service.tick()
    assert h.worker._row(h.spec["attempt_id"])[1].get("authorization") is None
    assert snapshot(h.c, {"oldest_pending_age": None}, at=2004)["issue_codes"] == ["publication_rejected"]


def test_oversized_grant_cannot_advance_epoch_or_replace_authorization(handshake):
    h = handshake
    h.clock["coordinator"] = h.clock["worker"]
    h.service.tick()
    before = h.worker._row(h.spec["attempt_id"])[1]
    oversized = dict(h.grants[0], expires_at=2060.001, authorization_epoch=10)
    with pytest.raises(Blocked, match="short lifetime"):
        h.worker.authorize(h.spec["attempt_id"], oversized)
    assert h.worker._row(h.spec["attempt_id"])[1] == before


@pytest.mark.parametrize("expires", [None, True, "2060", float("nan"), float("inf")])
def test_invalid_expiry_cannot_advance_epoch(handshake, expires):
    h = handshake
    h.clock["worker"] = 0.5
    h.worker._update(h.spec["attempt_id"], authorization_epoch=1)
    before = h.worker._row(h.spec["attempt_id"])[1]
    grant = {"attempt_id": h.spec["attempt_id"], "candidate_sha": "c" * 40,
             "input_head_sha": h.spec["input_head_sha"], "head_repository_id": 77, "head_ref": "feature",
             "authorization_epoch": 10, "expires_at": expires}
    with pytest.raises(Blocked, match="expired"):
        h.worker.authorize(h.spec["attempt_id"], grant)
    assert h.worker._row(h.spec["attempt_id"])[1] == before


def test_clock_sample_follows_live_read_not_tick_start_collection(handshake):
    h = handshake

    def slow_live_read(*args):
        h.clock["worker"] += 10
        return pr()

    h.service.github.get_pr = slow_live_read
    h.service.tick()
    assert h.grants[0]["expires_at"] == 2070


def test_revocation_during_clock_handshake_prevents_grant(handshake):
    h = handshake

    def pause(status):
        h.c.control("pause", 100, reason="Human paused during clock read")
        return status

    h.set_status_transform(pause)
    h.service.tick()
    assert not h.grants
    assert h.worker._row(h.spec["attempt_id"])[1].get("authorization") is None


def test_rejected_publication_does_not_block_independent_candidate(handshake, monkeypatch):
    h = handshake
    other_pr = pr(101, ref="independent", number=2)
    h.c.observe(other_pr, 1000)
    spec = h.c.claim(101)
    h.worker.submit(spec)
    h.worker._update(spec["attempt_id"], state="ready_to_publish", candidate_sha="d" * 40)
    monkeypatch.setattr(h.service, "scan", lambda: {100, 101})
    h.service.github.get_pr = lambda repo, number: pr() if number == 1 else other_pr
    h.set_status_transform(lambda status: dict(status, worker_time=None)
                           if status["attempt_id"] == h.spec["attempt_id"] else status)
    h.service.tick()
    assert [grant["attempt_id"] for grant in h.grants] == [spec["attempt_id"]]
    assert h.c.meta("last_tick") == "2004.0"
    assert snapshot(h.c, {"oldest_pending_age": None}, at=2004)["issue_codes"] == ["publication_rejected"]
