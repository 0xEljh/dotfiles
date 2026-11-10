import json
import stat
import subprocess
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.transport import SSHWorker, WorkerOffline


def settings(tmp_path):
    hosts = tmp_path / "known_hosts"
    hosts.write_text("provisioned-host-key")
    return {"worker_host": "contents-may-differ", "worker_user": "elijah",
            "known_hosts": str(hosts), "worker_executable": "/fixed/pr-worker",
            "worker_config": "/fixed/worker.json", "database": str(tmp_path / "coordinator.sqlite3")}


def test_untrusted_inputs_never_enter_remote_command(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "must-stay-on-coordinator")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", "/private/key")
    config = settings(tmp_path)
    worker = SSHWorker(config)
    title = "'; touch /home/elijah/UNSAFE; $(whoami)"
    with patch("pr_maintenance.transport.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"state":"accepted"}')) as run:
        worker.submit({"attempt_id": "stable-id", "pr": {"title": title}})
    argv = run.call_args.args[0]
    assert argv[-1] == "/fixed/pr-worker --config /fixed/worker.json transport"
    assert "StrictHostKeyChecking=yes" in argv
    assert "BatchMode=yes" in argv
    assert title not in " ".join(argv)
    assert json.loads(run.call_args.kwargs["input"])["spec"]["pr"]["title"] == title
    assert "TELEGRAM_BOT_TOKEN" not in run.call_args.kwargs["env"]
    assert "GITHUB_APP_PRIVATE_KEY_FILE" not in run.call_args.kwargs["env"]


def test_missing_host_key_and_arbitrary_operations_fail_before_transport(tmp_path):
    config = settings(tmp_path)
    worker = SSHWorker(config)
    with pytest.raises(ValueError, match="unsupported"):
        worker.request("shell")
    config["known_hosts"] = str(tmp_path / "missing")
    with pytest.raises(Blocked, match="known-hosts"):
        SSHWorker(config)


def test_known_hosts_requires_unambiguous_absolute_path(tmp_path, monkeypatch):
    config = settings(tmp_path)
    monkeypatch.chdir(tmp_path)
    config["known_hosts"] = "known_hosts"
    with pytest.raises(Blocked, match="absolute"):
        SSHWorker(config)


def test_ambiguous_timeout_does_not_return_failed_execution_or_leak_output(tmp_path):
    worker = SSHWorker(settings(tmp_path))
    with patch("pr_maintenance.transport.subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 30, output="secret")):
        with pytest.raises(WorkerOffline) as error:
            worker.request("status", "persisted-attempt")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(("result", "category", "exception"), [
    (SimpleNamespace(returncode=127, stdout="", stderr="ghp_private-credential"), "transport_exit", WorkerOffline),
    (SimpleNamespace(returncode=255, stdout='{"error":"ghp_private-credential"}', stderr=""), "transport_exit", WorkerOffline),
    (SimpleNamespace(returncode=0, stdout="ghp_private-credential", stderr=""), "transport_response", WorkerOffline),
    (SimpleNamespace(returncode=0, stdout="[]", stderr="ghp_private-credential"), "transport_response", WorkerOffline),
    (SimpleNamespace(returncode=1, stdout='{"error":"ghp_private-credential"}', stderr=""), "worker_rejection", Blocked),
    (SimpleNamespace(returncode=0, stdout='{"error":"ghp_private-credential"}', stderr=""), "worker_rejection", Blocked),
])
def test_failures_retain_private_diagnostics_without_public_secrets(tmp_path, caplog, result, category, exception):
    config = settings(tmp_path)
    with patch("pr_maintenance.transport.subprocess.run", return_value=result) as run:
        with pytest.raises(exception) as failure:
            SSHWorker(config).request("status", "stable-attempt", body="request-only-secret")
    paths = list((tmp_path / "diagnostics").glob("*.json"))
    assert len(paths) == 1
    path = paths[0]
    record = json.loads(path.read_text())
    assert record["category"] == category
    assert record["operation"] == "status"
    assert record["attempt_id"] == "stable-attempt"
    assert record["host"] == config["worker_host"]
    assert record["executable"] == config["worker_executable"]
    assert record["returncode"] == result.returncode
    assert "ghp_private-credential" in path.read_text()
    assert "request-only-secret" not in path.read_text()
    assert record["timestamp"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert str(path) in str(failure.value)
    assert "ghp_private-credential" not in str(failure.value) + caplog.text + "".join(traceback.format_exception(failure.value))
    assert run.call_count == 1
    assert json.loads(run.call_args.kwargs["input"])["attempt_id"] == "stable-attempt"


@pytest.mark.parametrize("failure", [
    OSError("ghp_private-credential"),
    subprocess.TimeoutExpired("ssh", 30, output=b"ghp_private-credential", stderr=b"stderr-private"),
])
def test_subprocess_exceptions_retained_privately(tmp_path, caplog, failure):
    with patch("pr_maintenance.transport.subprocess.run", side_effect=failure):
        with pytest.raises(WorkerOffline) as caught:
            SSHWorker(settings(tmp_path)).request("submit", "stable-attempt")
    paths = list((tmp_path / "diagnostics").glob("*.json"))
    assert len(paths) == 1
    assert "ghp_private-credential" in paths[0].read_text()
    public = str(caught.value) + caplog.text + "".join(traceback.format_exception(caught.value))
    assert "ghp_private-credential" not in public
    assert str(paths[0]) in str(caught.value)


@pytest.mark.parametrize("stdout", ["not json", "[]"])
def test_collect_invalid_response_remains_blocked(tmp_path, stdout):
    with patch("pr_maintenance.transport.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")):
        with pytest.raises(Blocked):
            SSHWorker(settings(tmp_path)).request("collect")
    assert len(list((tmp_path / "diagnostics").glob("*.json"))) == 1


@pytest.mark.parametrize("response", [{"state": "blocked", "error": "existing-status"}, {"state": "accepted"}])
def test_valid_status_does_not_retain_success_payload(tmp_path, response):
    with patch("pr_maintenance.transport.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")):
        assert SSHWorker(settings(tmp_path)).request("status", "stable-attempt") == response
    assert not (tmp_path / "diagnostics").exists()


def test_diagnostic_storage_failure_does_not_mask_transport_failure(tmp_path, caplog):
    (tmp_path / "diagnostics").symlink_to(tmp_path, target_is_directory=True)
    with patch("pr_maintenance.transport.subprocess.run", side_effect=OSError("ghp_private-credential")):
        with pytest.raises(WorkerOffline, match="not retained") as failure:
            SSHWorker(settings(tmp_path)).request("status", "stable-attempt")
    assert "ghp_private-credential" not in str(failure.value) + caplog.text
    assert not list(tmp_path.glob("diagnostic-*.json"))


def test_diagnostic_storage_oserror_preserves_original_failure(tmp_path, caplog):
    config = settings(tmp_path)
    with patch("pr_maintenance.transport.subprocess.run", side_effect=OSError("ghp_private-credential")):
        with patch("pr_maintenance.diagnostics.os.open", side_effect=OSError("storage-private-credential")):
            with pytest.raises(WorkerOffline, match="transport_os_error.*not retained") as failure:
                SSHWorker(config).request("status", "stable-attempt")
    assert "ghp_private-credential" not in str(failure.value) + caplog.text
    assert "storage-private-credential" not in str(failure.value) + caplog.text
