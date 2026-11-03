from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from .common import Blocked, canonical, digest


class WorkerOffline(RuntimeError):
    pass


class SSHWorker:
    """Only a fixed helper and config path enter the remote shell command."""

    def __init__(self, config: dict):
        self.config = config
        for key in ("worker_host", "worker_user"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", config[key]):
                raise Blocked(f"invalid configured {key}")
        for key in ("worker_executable", "worker_config"):
            if not Path(config[key]).is_absolute() or "\n" in config[key]:
                raise Blocked(f"{key} must be a fixed absolute path")
        hosts = config["known_hosts"]
        if not isinstance(hosts, str) or not Path(hosts).is_absolute() or any(c in hosts for c in "\r\n"):
            raise Blocked("known_hosts must be a fixed absolute path without line breaks")
        if not Path(hosts).is_file():
            raise Blocked("provision a strict SSH known-hosts file before dispatch")

    def request(self, operation: str, attempt_id=None, **values):
        if operation not in {"submit", "status", "authorize-publish", "cancel", "collect"}:
            raise ValueError("unsupported worker transport operation")
        request = {"operation": operation, **values}
        if attempt_id:
            request["attempt_id"] = attempt_id
        command = shlex.join([self.config["worker_executable"], "--config", self.config["worker_config"], "transport"])
        environment = {key: value for key, value in os.environ.items() if key in {
            "HOME", "PATH", "USER", "LOGNAME", "SSH_AUTH_SOCK", "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "TMPDIR",
        }}
        try:
            result = subprocess.run([
                "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=10", "-o", f"UserKnownHostsFile={self.config['known_hosts']}",
                "--", f"{self.config['worker_user']}@{self.config['worker_host']}", command,
            ], input=canonical(request), text=True, capture_output=True, timeout=30, check=False,
                env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkerOffline("worker transport unavailable; existing attempt retained") from error
        if result.returncode:
            # stderr can contain credential-helper diagnostics. Keep it out of reports.
            raise WorkerOffline(f"worker transport exit {result.returncode}; inspect private host journal")
        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            if operation == "collect":
                raise Blocked("worker ledger acknowledgement is not valid JSON") from error
            raise WorkerOffline("invalid worker acknowledgement; reconcile the same attempt") from error
        if not isinstance(response, dict):
            raise Blocked("worker acknowledgement is not an object; reconcile ledgers")
        if response.get("error") and not response.get("state"):
            raise Blocked("worker rejected request; inspect private worker status")
        return response

    def submit(self, spec: dict, feedback=None):
        values = {"spec": spec, "spec_hash": digest(spec)}
        if feedback is not None:
            values["feedback"] = feedback
        return self.request("submit", spec["attempt_id"], **values)
