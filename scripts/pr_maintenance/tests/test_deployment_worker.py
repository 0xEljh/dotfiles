import copy
from unittest.mock import Mock, patch

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.worker import Worker
from test_worker import specification


def worker_at(tmp_path, **settings):
    return Worker({"state_dir": str(tmp_path / "state"), "checkout_root": str(tmp_path / "checkouts"),
                   "execution_enabled": False, **settings})


def test_best_effort_status_discloses_assurance_and_error_cannot_reuse_cached_quiet(tmp_path):
    worker = worker_at(tmp_path, quiescence_mode="best_effort", best_effort_acknowledged=True)
    try:
        spec = specification()
        worker.submit(spec)
        status = worker._update(spec["attempt_id"], started=True, state="running", quiescent=True)
        assert status["execution_assurance"] == "best_effort"
        _, data = worker._row(spec["attempt_id"])
        assert not worker._interrupted_quiet(spec, data)
        assert not worker._interrupted_quiet(spec, data, require_host_proof=True)
    finally:
        worker.close()


def test_helper_uses_explicit_trusted_executable_not_checkout_path(tmp_path):
    worker = worker_at(tmp_path, config_file="/etc/pr-maintenance/worker.json", worker_executable="/trusted/bin/pr-worker")
    try:
        assert worker.helper() == ["/trusted/bin/pr-worker", "--config", "/etc/pr-maintenance/worker.json"]
    finally:
        worker.close()


@pytest.mark.parametrize("executable", [None, "pr-worker", "./pr-worker"])
def test_helper_missing_or_relative_executable_is_not_resolved_through_path(tmp_path, executable):
    settings = {"config_file": "/etc/pr-maintenance/worker.json"}
    if executable is not None:
        settings["worker_executable"] = executable
    worker = worker_at(tmp_path, **settings)
    try:
        with pytest.raises(Blocked, match="absolute"):
            worker.helper()
    finally:
        worker.close()


def test_private_scope_is_rechecked_at_worker_live_boundary(tmp_path):
    worker = worker_at(tmp_path, private_repositories_only=True)
    try:
        spec = specification()
        pr = spec["pr"]
        for side in ("base", "head"):
            pr[side]["repo"]["private"] = True
        repository = dict(pr["head"]["repo"], default_branch="main")
        current_base = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": spec["base_sha"]}}
        with patch.object(worker, "_github", side_effect=[repository, pr, current_base]):
            assert worker.live(spec)["id"] == spec["pr_id"]
        for field in ("base", "head"):
            live = specification()["pr"]
            for side in ("base", "head"):
                live[side]["repo"] = copy.deepcopy(live[side]["repo"])
                live[side]["repo"]["private"] = side != field
            with patch.object(worker, "_github", side_effect=[repository, live]):
                with pytest.raises(Blocked, match="private"):
                    worker.live(spec)
        with patch.object(worker, "_github", side_effect=[dict(repository, private=False), pr]):
            with pytest.raises(Blocked, match="private"):
                worker.live(spec)
    finally:
        worker.close()


@pytest.mark.parametrize("private_only", [False, True])
def test_live_uses_fresh_validated_base_ref_not_stale_pr_sha(tmp_path, private_only):
    worker = worker_at(tmp_path, private_repositories_only=private_only)
    try:
        spec = specification()
        for side in ("base", "head"):
            spec["pr"][side]["repo"]["private"] = True
        spec["pr"]["base"]["ref"] = "release/stable#1"
        live = copy.deepcopy(spec["pr"])
        repository = dict(live["head"]["repo"], default_branch="main")
        expected_ref = "repos/EigenformAI/example/git/ref/heads/release/stable%231"
        responses = {
            "repositories/10": repository, "repos/EigenformAI/example/pulls/1": live,
            expected_ref: {"ref": "refs/heads/release/stable#1", "object": {"type": "commit", "sha": "c" * 40}},
        }
        with patch.object(worker, "_github", side_effect=lambda endpoint: responses[endpoint]) as api:
            current = worker.live(spec)
        assert current["base"]["sha"] == "c" * 40
        assert live["base"]["sha"] == spec["base_sha"]
        assert api.call_args.args == (expected_ref,)
    finally:
        worker.close()


def test_stale_outside_org_spec_never_reads_pr_content(tmp_path):
    worker = worker_at(tmp_path, private_repositories_only=True)
    try:
        spec = specification()
        for side in ("head", "base"):
            spec["pr"][side]["repo"]["private"] = True
            spec["pr"][side]["repo"]["full_name"] = "outside/repo"
        with patch.object(worker, "_github") as api:
            with pytest.raises(Blocked, match="scope"):
                worker.live(spec)
            api.assert_not_called()
    finally:
        worker.close()


def test_repository_transfer_or_visibility_change_blocks_before_pr_read(tmp_path):
    worker = worker_at(tmp_path, private_repositories_only=True)
    try:
        spec = specification()
        for side in ("base", "head"):
            spec["pr"][side]["repo"]["private"] = True
        repository = dict(spec["pr"]["head"]["repo"], private=False, default_branch="main")
        with patch.object(worker, "_github", return_value=repository) as api:
            with pytest.raises(Blocked, match="private"):
                worker.live(spec)
            assert api.call_count == 1
            assert api.call_args.args[0].startswith("repositories/")
    finally:
        worker.close()


def test_best_effort_publication_rechecks_session_before_push(tmp_path):
    worker = worker_at(tmp_path, quiescence_mode="best_effort", best_effort_acknowledged=True)
    try:
        spec = specification()
        worker.submit(spec)
        with patch.object(worker, "_sessions_quiet", return_value=False), patch.object(worker, "git", return_value=Mock()) as git:
            with pytest.raises(Blocked, match="session"):
                worker._publish(spec)
            git.assert_not_called()
    finally:
        worker.close()
