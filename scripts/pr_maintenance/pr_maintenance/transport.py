from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from .common import Blocked, canonical, digest
from .diagnostics import retain_diagnostic


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
            category = "transport_timeout" if isinstance(error, subprocess.TimeoutExpired) else "transport_os_error"
            reference = self._diagnostic(category, operation, attempt_id, exception=str(error),
                                         exception_type=type(error).__name__, stdout=getattr(error, "stdout", None),
                                         stderr=getattr(error, "stderr", None))
            raise WorkerOffline(f"{category}; existing attempt retained; {reference}") from None
        details = {"returncode": result.returncode, "stdout": result.stdout, "stderr": getattr(result, "stderr", None)}
        # Exit 1 is the worker's structured application-error contract. Other
        # exits, especially SSH's 255, never establish a worker acknowledgement.
        if result.returncode not in {0, 1}:
            reference = self._diagnostic("transport_exit", operation, attempt_id, **details)
            raise WorkerOffline(f"transport_exit; existing attempt retained; {reference}") from None
        try:
            response = json.loads(result.stdout)
        except ValueError:
            response = None
        if isinstance(response, dict) and response.get("error") and not response.get("state"):
            reference = self._diagnostic("worker_rejection", operation, attempt_id, **details)
            raise Blocked(f"worker_rejection; existing attempt retained; {reference}") from None
        if result.returncode:
            reference = self._diagnostic("transport_exit", operation, attempt_id, **details)
            raise WorkerOffline(f"transport_exit; existing attempt retained; {reference}") from None
        if not isinstance(response, dict):
            reference = self._diagnostic("transport_response", operation, attempt_id, **details)
            exception = Blocked if operation == "collect" else WorkerOffline
            raise exception(f"transport_response; reconcile the same attempt; {reference}") from None
        return response

    def _diagnostic(self, category, operation, attempt_id, **details):
        path = retain_diagnostic(self.config["database"], category, operation=operation, attempt_id=attempt_id,
                                 host=self.config["worker_host"], executable=self.config["worker_executable"], **details)
        return f"private artifact: {path}" if path is not None else "private diagnostic not retained"

    def submit(self, spec: dict, feedback=None):
        values = {"spec": spec, "spec_hash": digest(spec)}
        if feedback is not None:
            values["feedback"] = feedback
        return self.request("submit", spec["attempt_id"], **values)
