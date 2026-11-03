import json
import subprocess
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
            "worker_config": "/fixed/worker.json"}


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
