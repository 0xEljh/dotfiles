"""Pinned V2 adapter with strict proof or explicitly acknowledged observations.

Best-effort completion observes registered work, not arbitrary detached descendants.
Strict mode still requires the host's trusted descendant/shell/PTY quiescence probe.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .common import Blocked, canonical


class Runner:
    def __init__(self, config: dict):
        self.config = config
        self.binary = config.get("opencode_binary", "opencode")
        self.server = config.get("opencode_server", "")
        self.connection = config.get("opencode_connection", "explicit")
        if self.connection not in {"explicit", "managed"}:
            raise Blocked("unknown OpenCode connection mode; no automatic fallback")

    def _managed_endpoint(self) -> str:
        try:
            result = subprocess.run([self.binary, "service", "status"], capture_output=True, timeout=15)
            endpoints = set(re.findall(r"http://(?:127\.0\.0\.1|localhost):[0-9]+", result.stdout.decode()))
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise Blocked("managed OpenCode endpoint cannot be established without changing services") from None
        if result.returncode or len(endpoints) != 1:
            raise Blocked("managed OpenCode endpoint cannot be established without changing services")
        return endpoints.pop()

    def _connection_args(self) -> list[str]:
        if self.connection == "managed":
            if not self.server or self._managed_endpoint() != self.server:
                raise Blocked("managed OpenCode endpoint changed; inspect before another invocation")
            return []
        return ["--server", self.server]

    def api(self, method: str, path: str, body: dict | None = None, cwd: Path | None = None) -> dict:
        argv = [self.binary, "api", method, path, *self._connection_args()]
        if body is not None:
            argv += ["--data", canonical(body)]
        result = subprocess.run(argv, cwd=cwd, env=dict(os.environ, PWD=str(cwd or Path.cwd())), capture_output=True, timeout=30)
        if result.returncode:
            raise Blocked("OpenCode API unavailable; inspect pinned runner and authentication")
        if self.connection == "managed":
            self._connection_args()
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise Blocked("OpenCode API returned an unverified response format") from error

    def _best_effort_configured(self) -> bool:
        version = self.config.get("runner_version")
        return (self.config.get("best_effort_acknowledged") is True
                and isinstance(version, str) and bool(version.strip())
                and isinstance(self.server, str) and bool(self.server.strip()))

    def check_configuration(self) -> None:
        """Validate local settings without invoking a runner or server."""
        mode = self.config.get("quiescence_mode", "strict")
        if mode not in ("strict", "best_effort"):
            raise Blocked("onboarding: unknown quiescence mode")
        if mode == "best_effort" and not self._best_effort_configured():
            raise Blocked("onboarding: best_effort_acknowledged must be true with pinned runner_version and nonempty server")
        if mode == "strict" and (not self.config.get("runner_contract_verified") or not self.config.get("runner_version")
                                  or not self.server or not self.config.get("runner_quiescence_argv")):
            raise Blocked("onboarding: pinned runner/session/descendant cancellation contract is not proved")
        if mode == "strict":
            probe = self.config["runner_quiescence_argv"]
            if not isinstance(probe, list) or not probe or not all(isinstance(item, str) for item in probe):
                raise Blocked("onboarding: invalid trusted quiescence probe")

    def check_contract(self, cwd: Path) -> None:
        self.check_configuration()
        version = subprocess.run([self.binary, "--version"], cwd=cwd, capture_output=True, timeout=15)
        if version.returncode or version.stdout.decode().strip() != self.config["runner_version"]:
            raise Blocked("onboarding: installed runner does not match pinned runner_version")
        server_version = self.config.get("runner_server_version", self.config["runner_version"].removeprefix("opencode v"))
        if self.api("get", "/api/info", cwd=cwd).get("version") != server_version:
            raise Blocked("onboarding: explicit shared server does not match pinned runner_version")
        help_result = subprocess.run([self.binary, "run", "--help"], cwd=cwd, capture_output=True, timeout=15)
        help_text = help_result.stdout.decode()
        if help_result.returncode or any(flag not in help_text for flag in ("--session", "--server", "--model", "--agent", "--format")):
            raise Blocked("onboarding: pinned runner flags are unavailable")

    @staticmethod
    def session_id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"ses[A-Za-z0-9_-]+", value):
            raise Blocked("runner returned an invalid explicit session ID")
        return value

    def create_session(self, cwd: Path, title: str, agent: str, model: str,
                       session_id: str | None = None, metadata: dict | None = None) -> str:
        provider, separator, model_id = model.partition("/")
        if not separator or not provider or not model_id:
            raise Blocked("explicit provider/model required")
        model_id, variant_separator, variant = model_id.partition("#")
        model_ref = {"providerID": provider, "id": model_id}
        if variant_separator:
            if not variant or "#" in variant:
                raise Blocked("invalid explicit model variant")
            model_ref["variant"] = variant
        session_id = self.session_id(session_id or "ses_" + str(uuid.uuid4()))
        metadata = metadata or {}
        data = self.api("post", "/api/session", {
            "id": session_id, "title": title, "agent": agent, "model": model_ref,
            "location": {"directory": str(cwd)}, "metadata": metadata,
        }, cwd=cwd)
        session = data.get("data", {})
        observed_model = session.get("model")
        if not variant_separator and isinstance(observed_model, dict) and observed_model.get("variant") == "default":
            observed_model = {key: value for key, value in observed_model.items() if key != "variant"}
        if (session.get("id") != session_id or session.get("location") != {"directory": str(cwd)}
                or session.get("agent") != agent or observed_model != model_ref
                or any(session.get("metadata", {}).get(key) != value for key, value in metadata.items())):
            raise Blocked("explicit session ownership/location/model/agent validation failed")
        return session_id

    def check_available(self, cwd: Path, models: list[str], agents: list[str]) -> None:
        # Location affects project-specific models, permissions, plugins and agents.
        suffix = "?" + urlencode({"location[directory]": str(cwd)})
        model_rows = self.api("get", "/api/model" + suffix, cwd=cwd).get("data", [])
        available = set()
        for row in model_rows:
            if not row.get("enabled"):
                continue
            name = row.get("providerID", "") + "/" + row.get("id", "")
            available.add(name)
            available.update(name + "#" + variant["id"] for variant in row.get("variants", []))
        agent_rows = self.api("get", "/api/agent" + suffix, cwd=cwd).get("data", [])
        available_agents = {row.get("id") for row in agent_rows}
        if not set(models) <= available or not set(agents) <= available_agents:
            raise Blocked("onboarding: configured model or stage/review agent unavailable; no substitution")

    def run(self, cwd: Path, session_id: str, model: str, agent: str, prompt: str,
            events: Path, timeout: float) -> None:
        events.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with events.open("wb") as output:
            os.chmod(events, 0o600)
            result = subprocess.run(
                [self.binary, "run", "--session", self.session_id(session_id), *self._connection_args(),
                 "--model", model, "--agent", agent, "--format", "json"],
                input=prompt.encode(), cwd=cwd, env=dict(os.environ, PWD=str(cwd)),
                stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
            )
            output.flush()
            os.fsync(output.fileno())
        if result.returncode:
            raise Blocked("OpenCode invocation failed; inspect retained session before any retry")
        if self.connection == "managed":
            self._connection_args()

    def interrupt(self, session_id: str, cwd: Path) -> None:
        self.api("post", "/api/session/" + self.session_id(session_id) + "/interrupt?resume=false", {}, cwd=cwd)

    def _foreground_idle(self, session_id: str, cwd: Path) -> bool:
        active = self.api("get", "/api/session/active", cwd=cwd)
        if not isinstance(active, dict) or not isinstance(active.get("data"), dict):
            return False
        if session_id in active["data"]:
            return False
        for key, value in active["data"].items():
            self.session_id(key)
            if not isinstance(value, dict) or value.get("type") != "running":
                return False
        inbox = self.api("get", f"/api/session/{session_id}/inbox", cwd=cwd)
        return isinstance(inbox, dict) and isinstance(inbox.get("data"), list) and not inbox["data"]

    @staticmethod
    def _exited_terminals(rows: object, session_id: str | None = None) -> bool:
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict) or row.get("status") != "exited":
                return False
            if not all(isinstance(row.get(key), str) for key in ("id", "title", "command", "cwd")):
                return False
            if not row["id"].startswith("pty") or not isinstance(row.get("args"), list):
                return False
            if not all(isinstance(arg, str) for arg in row["args"]):
                return False
            if type(row.get("pid")) is not int or row["pid"] < 0:
                return False
            if "exitCode" in row and (type(row["exitCode"]) is not int or row["exitCode"] < 0):
                return False
            if session_id is not None:
                if row.get("sessionID") != session_id or "foregroundProcess" not in row:
                    return False
                if row["foregroundProcess"] is not None and not isinstance(row["foregroundProcess"], str):
                    return False
                for key, fields, minimum in (("size", ("cols", "rows"), 1), ("output", ("head", "tail"), 0)):
                    values = row.get(key)
                    if not isinstance(values, dict) or not all(type(values.get(field)) is int and values[field] >= minimum for field in fields):
                        return False
        return True

    def _quiet_snapshot(self, session_id: str, cwd: Path) -> bool:
        if not self._foreground_idle(session_id, cwd):
            return False
        children = self.api("get", "/api/session?" + urlencode({"parentID": session_id, "limit": 1}), cwd=cwd)
        if not isinstance(children, dict) or not isinstance(children.get("data"), list) or children["data"]:
            return False
        cursor = children.get("cursor")
        if not isinstance(cursor, dict) or cursor.get("next") is not None:
            return False
        previous = cursor.get("previous")
        if previous is not None and (not isinstance(previous, str) or not previous):
            return False
        suffix = "?" + urlencode({"location[directory]": str(cwd)})
        for kind in ("shell", "pty"):
            inventory = self.api("get", f"/api/{kind}" + suffix, cwd=cwd)
            if (not isinstance(inventory, dict) or inventory.get("location") != {"directory": str(cwd)}
                    or not isinstance(inventory.get("data"), list)):
                return False
            # Pinned shell.list contains only running commands; ownership tags do not exempt any.
            if kind == "shell" and inventory["data"]:
                return False
            if kind == "pty" and not self._exited_terminals(inventory["data"]):
                return False
        terminals = self.api("get", f"/api/experimental/session/{session_id}/terminal", cwd=cwd)
        return isinstance(terminals, dict) and self._exited_terminals(terminals.get("data"), session_id)

    def observed_quiet(self, session_id: str, cwd: Path) -> bool:
        """Observe consecutive quiet snapshots, never proof of detached descendant exit."""
        try:
            session_id = self.session_id(session_id)
            return (self._quiet_snapshot(session_id, cwd) and self._quiet_snapshot(session_id, cwd)
                    and self._foreground_idle(session_id, cwd))
        except (Blocked, OSError, ValueError, TypeError, subprocess.SubprocessError):
            return False

    def quiescent(self, session_id: str, cwd: Path) -> bool:
        """Return acceptable completion under the selected assurance, strict by default."""
        mode = self.config.get("quiescence_mode", "strict")
        if mode == "best_effort":
            return self._best_effort_configured() and self.observed_quiet(session_id, cwd)
        if mode != "strict":
            return False
        probe = self.config.get("runner_quiescence_argv")
        if not probe or not self.config.get("runner_contract_verified"):
            return False
        try:
            argv = [arg.replace("{session_id}", self.session_id(session_id)).replace("{server}", self.server) for arg in probe]
            result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=30)
            evidence = json.loads(result.stdout)
            return (result.returncode == 0 and evidence.get("session_id") == session_id
                    and evidence.get("quiescent") is True and evidence.get("descendants_quiescent") is True)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def review_approved(events: Path, candidate: str, manifest_hash: str) -> bool:
        texts = []
        try:
            for line in events.read_text().splitlines():
                event = json.loads(line)
                if event.get("type") == "text" and isinstance(event.get("part", {}).get("text"), str):
                    texts.append(event["part"]["text"])
            for text in reversed(texts):
                try:
                    decision = json.loads(text.strip())
                except ValueError:
                    continue
                return (decision.get("decision") == "approve" and decision.get("candidate_sha") == candidate
                        and decision.get("manifest_hash") == manifest_hash and decision.get("findings") == [])
        except (OSError, ValueError, AttributeError):
            pass
        return False
