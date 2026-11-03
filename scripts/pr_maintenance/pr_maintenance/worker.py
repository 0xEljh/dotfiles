"""Durable fixed-JSON worker and its local, evidence-recording agent helpers.

No transport operation accepts shell source, an executable, or a checkout path.
Unknown execution retains both persistent ownership and the owned checkout.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .common import Blocked, STAGES, TERMINAL, canonical, check_secrets, database, digest, private_write, transaction
from .git_ops import GitOps, ref, repository_url, sha, validate_live_pr
from .prompts import coding_prompt, load_skills, review_prompt
from .runner import Runner


class Busy(Blocked):
    pass


class GrantRejected(Blocked):
    pass


class Worker:
    def __init__(self, config: dict):
        self.config = config
        self.root = Path(config["state_dir"]).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.db = database(self.root / "worker.sqlite3")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS worker_attempts (
                id TEXT PRIMARY KEY, spec_hash TEXT NOT NULL, spec TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS worker_branches (
                branch TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS worker_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        with transaction(self.db):
            self.db.execute("INSERT OR IGNORE INTO worker_meta VALUES ('ledger_id', ?)", (str(uuid.uuid4()),))
        self.ledger_id = self.db.execute("SELECT value FROM worker_meta WHERE key='ledger_id'").fetchone()[0]
        self.runner = Runner(config)

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def identity(value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError) as error:
            raise Blocked("attempt_id must be a canonical UUID") from error
        if str(parsed) != value:
            raise Blocked("attempt_id must be a canonical UUID")
        return value

    def unit(self, attempt: str) -> str:
        return "pr-maintenance@" + self.identity(attempt) + ".service"

    def artifacts(self, attempt: str) -> Path:
        path = self.root / "attempts" / self.identity(attempt)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def checkout(self, spec: dict) -> Path:
        return (Path(self.config["checkout_root"]).resolve() / str(int(spec["pr"]["base"]["repo"]["id"]))
                / str(int(spec["pr"]["head"]["repo"]["id"])) / self.identity(spec["attempt_id"]))

    @staticmethod
    def branch(spec: dict) -> str:
        return canonical([spec["pr"]["head"]["repo"]["id"], spec["pr"]["head"]["ref"]])

    def _row(self, attempt: str) -> tuple[dict, dict]:
        row = self.db.execute("SELECT spec,data FROM worker_attempts WHERE id=?", (self.identity(attempt),)).fetchone()
        if row is None:
            raise Blocked("unknown attempt; reconcile ledgers before dispatch")
        return json.loads(row["spec"]), json.loads(row["data"])

    def _update(self, attempt: str, **changes) -> dict:
        with transaction(self.db):
            _, data = self._row(attempt)
            data.update(changes, updated_at=time.time())
            if data["state"] in TERMINAL and (not data["quiescent"] or data["publication_state"] in ("unknown", "push_in_flight")):
                raise Blocked("terminal outcome requires execution quiescence and known publication")
            self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(data), attempt))
            if data["state"] in TERMINAL and data["state"] != "published_with_concern":
                self.db.execute("DELETE FROM worker_branches WHERE attempt_id=?", (attempt,))
        return self.status(attempt)

    def status(self, attempt: str) -> dict:
        spec, data = self._row(attempt)
        keys = ("attempt_id", "state", "publication_state", "candidate_sha", "session_id", "review_session_id",
                "skill_hashes", "verification", "error", "quiescent", "authorization_epoch", "summary", "started_at", "updated_at",
                "incident_acknowledged", "retryable_reason", "execution_assurance")
        result = {key: data[key] for key in keys if key in data}
        result["spec_hash"] = digest(spec)
        result["worker_host"] = socket.gethostname()
        if data.get("started"):
            result["checkout"] = str(self.checkout(spec))
        if data["state"] == "ready_to_publish" and data["publication_state"] not in ("unknown", "push_in_flight"):
            result["publication_state"] = "ready_to_publish"
        elif data["publication_state"] in ("not_started", "unpublished", "candidate_recorded"):
            result["publication_state"] = "not_published"
        return result

    @contextlib.contextmanager
    def lock(self, name: str):
        directory = self.root / "locks"
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Busy("worker execution or branch is already active") from error
            yield
        finally:
            os.close(descriptor)

    def _systemctl(self, operation: str, attempt: str) -> bool:
        if operation not in ("start", "stop", "is-active"):
            raise ValueError("invalid unit operation")
        result = subprocess.run(["systemctl", "--user", operation, self.unit(attempt)], capture_output=True, timeout=30)
        return result.returncode == 0

    def _schedule(self, attempt: str) -> None:
        if self.config.get("execution_enabled"):
            try:
                if not self._systemctl("start", attempt):
                    self._update(attempt, error="supervisor start not acknowledged; reconcile same attempt")
            except (OSError, subprocess.TimeoutExpired):
                self._update(attempt, error="supervisor unavailable; accepted attempt retained")

    def _validate_spec(self, spec: dict) -> None:
        self.identity(spec["attempt_id"])
        required = ("pr_id", "stage", "ordinal", "pr", "input_head_sha", "base_sha", "config_hash",
                    "authorization_epoch", "model", "agent", "review_model", "review_agent", "verification_profile", "feedback", "limits")
        if any(key not in spec for key in required) or spec["stage"] not in STAGES:
            raise Blocked("incomplete immutable attempt specification")
        if not isinstance(spec["pr_id"], int) or spec["pr_id"] != spec["pr"]["id"]:
            raise Blocked("PR identity mismatch")
        if not isinstance(spec["authorization_epoch"], int) or spec["authorization_epoch"] < 0:
            raise Blocked("invalid authorization epoch")
        if (not isinstance(spec["ordinal"], int) or spec["ordinal"] < 0 or not isinstance(spec["limits"], dict)
                or any(not isinstance(spec[key], str) or not spec[key] for key in ("config_hash", "model", "agent", "review_model", "review_agent"))):
            raise Blocked("invalid immutable model, agent, ordinal or limits")
        sha(spec["input_head_sha"])
        sha(spec["base_sha"])
        for side in ("head", "base"):
            if not isinstance(spec["pr"][side]["repo"]["id"], int) or spec["pr"][side]["repo"]["id"] <= 0:
                raise Blocked("stable numeric repository ID required")
            ref(spec["pr"][side]["ref"])
            repository_url(spec["pr"][side]["repo"])
        if spec["pr"]["head"]["sha"] != spec["input_head_sha"] or spec["pr"]["base"]["sha"] != spec["base_sha"]:
            raise Blocked("specification SHA differs from its PR snapshot")
        profile = spec["verification_profile"]
        if profile is not None and (not isinstance(profile, list) or any(
            not isinstance(command, list) or not command or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            for command in profile
        )):
            raise Blocked("invalid trusted verification profile")
        feedback = spec.get("feedback")
        if feedback is not None and (not isinstance(feedback, dict) or not isinstance(feedback.get("sha256"), str)
                                     or len(feedback["sha256"]) != 64):
            raise Blocked("immutable feedback must be a snapshot hash descriptor, not raw feedback")
        if spec["stage"] == "feedback" and feedback is None:
            raise Blocked("feedback stage requires an immutable snapshot hash descriptor")
        check_secrets(canonical(spec))

    def submit(self, spec: dict, spec_hash: str | None = None, feedback: dict | None = None) -> dict:
        self._validate_spec(spec)
        attempt = spec["attempt_id"]
        hashed = digest(spec)
        if spec_hash is not None and spec_hash != hashed:
            raise Blocked("immutable specification hash mismatch")
        with transaction(self.db):
            existing = self.db.execute("SELECT spec_hash FROM worker_attempts WHERE id=?", (attempt,)).fetchone()
            if existing:
                if existing["spec_hash"] != hashed:
                    raise Blocked("immutable attempt ID/specification collision")
            else:
                owner = self.db.execute("SELECT attempt_id FROM worker_branches WHERE branch=?", (self.branch(spec),)).fetchone()
                if owner:
                    raise Blocked("branch has persistent unresolved ownership")
                if spec.get("feedback") is not None:
                    expected = spec["feedback"].get("sha256")
                    if not expected or feedback is None or digest(feedback) != expected:
                        raise Blocked("immutable feedback snapshot hash missing or mismatched")
                    check_secrets(canonical(feedback))
                    private_write(self.artifacts(attempt) / "feedback.json", canonical(feedback))
                data = {"attempt_id": attempt, "state": "accepted", "publication_state": "not_started", "started": False,
                        "quiescent": True, "authorization_epoch": spec["authorization_epoch"], "created_at": time.time(),
                        "execution_assurance": self.config.get("quiescence_mode", "strict")}
                self.db.execute("INSERT INTO worker_attempts VALUES (?,?,?,?)", (attempt, hashed, canonical(spec), canonical(data)))
                self.db.execute("INSERT INTO worker_branches VALUES (?,?)", (self.branch(spec), attempt))
        # The immutable ledger commit precedes any supervisor action or acknowledgement.
        if not existing:
            self._schedule(attempt)
        return self.status(attempt)

    def _grant(self, attempt: str, authorization: dict, data: dict, spec: dict) -> None:
        epoch = authorization.get("authorization_epoch", -1)
        if epoch != data["authorization_epoch"] or data["state"] == "cancel_requested":
            raise GrantRejected("stale or revoked authorization epoch")
        if (authorization.get("attempt_id") != attempt or authorization.get("candidate_sha") != data.get("candidate_sha")
                or authorization.get("input_head_sha") != spec["input_head_sha"]
                or authorization.get("head_repository_id") != spec["pr"]["head"]["repo"]["id"]
                or authorization.get("head_ref") != spec["pr"]["head"]["ref"]):
            raise GrantRejected("publication grant is not bound to this exact candidate and branch")
        expires = authorization.get("expires_at", 0)
        if not isinstance(expires, (float, int)) or not math.isfinite(expires) or expires <= time.time():
            raise GrantRejected("publication authorization expired")
        if expires - time.time() > self.config.get("authorization_max_seconds", 60):
            raise GrantRejected("publication authorization exceeds configured short lifetime")

    def authorize(self, attempt: str, authorization: dict) -> dict:
        with transaction(self.db):
            spec, data = self._row(attempt)
            new_epoch = authorization.get("authorization_epoch", -1)
            if not isinstance(new_epoch, int) or new_epoch < data["authorization_epoch"]:
                raise GrantRejected("stale authorization epoch")
            data["authorization_epoch"] = new_epoch
            self._grant(attempt, authorization, data, spec)
            if data["state"] != "ready_to_publish":
                raise Blocked("attempt is not ready for publication")
            data["authorization"] = authorization
            self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(data), attempt))
        self._schedule(attempt)
        return self.status(attempt)

    def _active_sessions(self, data: dict) -> list[str]:
        return [data[key] for key in ("session_id", "review_session_id") if data.get(key)]

    def _sessions_quiet(self, spec: dict, data: dict) -> bool:
        sessions = self._active_sessions(data)
        return bool(sessions) and all(self.runner.quiescent(session, self.checkout(spec)) for session in sessions)

    def cancel(self, attempt: str, epoch: int, pause: bool = False, acknowledge_incident: bool = False) -> dict:
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise Blocked("cancellation requires a nonnegative integer epoch")
        with transaction(self.db):
            spec, data = self._row(attempt)
            if epoch < data["authorization_epoch"]:
                raise Blocked("stale cancellation epoch")
            if acknowledge_incident:
                if (data["state"] != "published_with_concern" or not data["quiescent"]
                        or data["publication_state"] != "published"
                        or (epoch <= data["authorization_epoch"] and not data.get("incident_acknowledged"))):
                    raise Blocked("incident acknowledgement requires known publication, quiescence, and a fresh epoch")
                data.update(incident_acknowledged=True, authorization_epoch=epoch)
                self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(data), attempt))
                self.db.execute("DELETE FROM worker_branches WHERE attempt_id=?", (attempt,))
                return self.status(attempt)
            if data["state"] in TERMINAL:
                return self.status(attempt)
            data.update(state="cancel_requested", authorization_epoch=epoch, authorization=None, pause_requested=pause,
                        execution_interrupted=bool(data.get("started")))
            self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(data), attempt))
        if not data.get("started"):
            if pause:
                return self._update(attempt, state="inspection_required", quiescent=True, publication_state="unpublished",
                                    error="paused before execution; explicit operator retry required")
            # An already queued unit must read cancelled before its started marker.
            return self._update(attempt, state="cancelled", quiescent=True, publication_state="unpublished")
        for session in self._active_sessions(data):
            try:
                self.runner.interrupt(session, self.checkout(spec))
            except (Blocked, OSError, subprocess.TimeoutExpired):
                pass
        try:
            self._systemctl("stop", attempt)
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Unit termination does not cover shared-server descendants. Recover only
        # with the separate host proof, never from kill/CLI return alone.
        return self.reconcile_attempt(attempt)

    def transport(self, request: dict):
        if not isinstance(request, dict):
            raise Blocked("transport requires a JSON object")
        operation = request.get("operation")
        if operation == "collect" and not request.get("attempt_id"):
            return {"attempts": [self.status(row["id"]) for row in self.db.execute("SELECT id FROM worker_attempts ORDER BY id")],
                    "ledger_id": self.ledger_id}
        attempt = self.identity(request.get("attempt_id"))
        if operation == "submit":
            spec = request.get("spec", {})
            if not isinstance(spec, dict) or spec.get("attempt_id") != attempt:
                raise Blocked("transport/spec attempt identity mismatch")
            return self.submit(spec, request.get("spec_hash"), request.get("feedback"))
        if operation in ("status", "collect"):
            return self.status(attempt)
        if operation == "authorize-publish":
            authorization = request.get("authorization", {})
            if not isinstance(authorization, dict):
                raise Blocked("authorization must be a JSON object")
            return self.authorize(attempt, authorization)
        if operation == "cancel":
            return self.cancel(attempt, request["epoch"], request.get("pause", False), request.get("acknowledge_incident", False))
        raise Blocked("unsupported fixed transport operation")

    def _github(self, endpoint: str) -> dict:
        result = subprocess.run(["gh", "api", "--method", "GET", endpoint], capture_output=True, timeout=30)
        if result.returncode:
            raise Blocked("live read-only GitHub validation unavailable")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise Blocked("invalid live GitHub response") from error

    def live(self, spec: dict) -> dict:
        repository = spec["pr"]["base"]["repo"]["full_name"]
        if self.config.get("private_repositories_only"):
            organization = self.config.get("organization", "EigenformAI")
            expected = spec["pr"]
            names = self.config.get("allow_repositories", [])
            ids = self.config.get("allow_repository_ids", [])
            prs = self.config.get("allow_pr_ids", [])
            repository_url(expected["base"]["repo"])
            if (repository.split("/")[0].casefold() != organization.casefold()
                    or expected["base"]["repo"]["owner"]["id"] != self.config.get("organization_id", 97932676)
                    or expected["user"]["id"] != self.config.get("author_id", 34589654)
                    or expected["head"]["repo"]["id"] != expected["base"]["repo"]["id"]
                    or (names and repository.casefold() not in {name.casefold() for name in names})
                    or (ids and expected["base"]["repo"]["id"] not in ids)
                    or (prs and expected["id"] not in prs)):
                raise Blocked("stored attempt is outside configured repository scope")
            if any(expected[side]["repo"].get("private") is not True for side in ("base", "head")):
                raise Blocked("stored attempt lacks private repository scope")
            # An immutable-ID metadata lookup is necessary to detect transfer or
            # visibility changes. Validate it before reading any PR content.
            repo = self._github("repositories/" + str(expected["head"]["repo"]["id"]))
            if (repo.get("private") is not True or repo.get("full_name") != repository
                    or repo.get("owner", {}).get("id") != self.config.get("organization_id", 97932676)):
                raise Blocked("private repository identity or visibility changed before PR read")
            validate_live_pr(expected, expected, repo, self.config.get("author_id", 34589654), self.config.get("organization_id", 97932676))
            live = self._github(f"repos/{repository}/pulls/{int(expected['number'])}")
        else:
            live = self._github(f"repos/{repository}/pulls/{int(spec['pr']['number'])}")
            repo = self._github("repositories/" + str(spec["pr"]["head"]["repo"]["id"]))
        validate_live_pr(spec["pr"], live, repo, self.config.get("author_id", 34589654), self.config.get("organization_id", 97932676))
        if self.config.get("private_repositories_only") and (repo.get("private") is not True or any(
                live[side]["repo"].get("private") is not True for side in ("base", "head"))):
            raise Blocked("private repository scope changed; publication and execution blocked")
        return live

    def git(self, spec: dict) -> GitOps:
        return GitOps(self.checkout(spec), repository_url(spec["pr"]["head"]["repo"]))

    def free_space(self) -> None:
        if shutil.disk_usage(self.root).free < self.config.get("min_free_bytes", 1024 * 1024 * 1024):
            raise Blocked("onboarding: insufficient free space; preserve existing work")

    def helper(self) -> list[str]:
        config_file = self.config.get("config_file")
        if not config_file:
            raise Blocked("onboarding: fixed worker config_file required for evidence helpers")
        executable = self.config.get("worker_executable")
        if not isinstance(executable, str) or not Path(executable).is_absolute():
            raise Blocked("fixed worker_executable must be an absolute trusted path")
        return [executable, "--config", config_file]

    def _create_session(self, spec: dict, key: str, title: str, agent: str, model: str) -> str:
        # Persist a caller-chosen ID before the API request. A lost create response
        # cannot leave an undiscoverable session or authorize a second session.
        session = "ses_" + str(uuid.uuid4())
        self._update(spec["attempt_id"], **{key: session}, quiescent=False)
        return self.runner.create_session(self.checkout(spec), title, agent, model, session,
                                          {"attempt_id": spec["attempt_id"], "spec_hash": digest(spec)})

    def _prompt_spec(self, spec: dict) -> dict:
        prepared = dict(spec)
        sources = self.config.get("dependency_sources", {})
        if not isinstance(sources, dict):
            raise Blocked("onboarding: dependency_sources must map repository names to absolute paths")
        repository = spec["pr"]["head"]["repo"]["full_name"]
        if repository in sources:
            source = sources[repository]
            if not isinstance(source, str) or "\0" in source or not Path(source).is_absolute():
                raise Blocked("onboarding: dependency source must be an absolute path")
            prepared["dependency_source"] = source
        if spec.get("feedback") is None:
            return prepared
        path = self.artifacts(spec["attempt_id"]) / "feedback.json"
        snapshot = json.loads(path.read_text())
        if digest(snapshot) != spec["feedback"]["sha256"]:
            raise Blocked("private feedback artifact no longer matches immutable snapshot hash")
        check_secrets(canonical(snapshot))
        return dict(prepared, feedback=snapshot)

    def record_manifest(self, attempt: str, evidence: Path) -> dict:
        spec, data = self._row(attempt)
        if data["state"] != "running":
            raise Blocked("scope manifest only accepted for the running attempt")
        if evidence.resolve().parent != self.artifacts(attempt).resolve():
            raise Blocked("manifest input must be a private attempt artifact")
        value = json.loads(evidence.read_text())
        paths = value.get("paths")
        if not isinstance(paths, dict) or not paths:
            raise Blocked("path manifest with per-path PR relationship is required")
        for path, reason in paths.items():
            if (not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts
                    or ".git" in Path(path).parts or "\0" in path or not isinstance(reason, str) or not reason.strip()):
                raise Blocked("invalid path manifest or missing rationale")
        check_secrets(canonical(value))
        selected = data.get("selected_verification")
        if spec["verification_profile"] is None:
            proposed = value.get("verification_profile", selected)
            self._validate_profile(proposed)
            if selected is not None and proposed != selected:
                raise Blocked("selected verification cannot change after its pre-edit recording")
            selected = proposed
        if not data.get("manifest"):
            git = self.git(spec)
            # Conflict stage starts with a trusted prepared merge. Other stages
            # must record the initial manifest while the original head is clean.
            if git.head() != spec["input_head_sha"] or (spec["stage"] != "conflicts" and not git.clean()):
                raise Blocked("initial scope manifest must be recorded before edits")
        history = data.get("manifest_history", []) + [{"hash": digest(value), "recorded_at": time.time(), "paths": paths}]
        private_write(self.artifacts(attempt) / "manifest.json", canonical(value))
        return self._update(attempt, manifest=value, manifest_history=history, manifest_hash=digest(value), selected_verification=selected)

    @staticmethod
    def _validate_profile(profile):
        if not isinstance(profile, list) or not profile or any(
            not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
            for command in profile
        ):
            raise Blocked("onboarding: select existing verification commands before edits; missing dependencies block execution")

    def _profile(self, spec, data):
        profile = spec["verification_profile"] if spec["verification_profile"] is not None else data.get("selected_verification")
        self._validate_profile(profile)
        return profile

    def record_candidate(self, attempt: str) -> dict:
        spec, data = self._row(attempt)
        if data["state"] != "running" or not data.get("manifest"):
            raise Blocked("running attempt with pre-edit manifest required")
        git = self.git(spec)
        candidate = git.head()
        git.check_candidate(spec["input_head_sha"], candidate, spec["base_sha"] if spec["stage"] == "conflicts" else None)
        return self._update(attempt, candidate_sha=candidate, publication_state="candidate_recorded")

    def _check_scope(self, spec: dict, data: dict, git: GitOps) -> list[str]:
        if not data.get("manifest"):
            raise Blocked("missing pre-edit path manifest")
        paths = git.check_candidate(spec["input_head_sha"], data["candidate_sha"], spec["base_sha"] if spec["stage"] == "conflicts" else None)
        if not set(paths) <= set(data["manifest"]["paths"]):
            raise Blocked("candidate paths exceed the recorded scope manifest")
        return paths

    def _verify(self, spec: dict, candidate: str) -> dict:
        _, data = self._row(spec["attempt_id"])
        profile = self._profile(spec, data)
        self.free_space()
        outcomes = []
        attempt = spec["attempt_id"]
        # Persist before spawning: a crash between Popen and recording the PID
        # must not leave a ledger that claims verification descendants are quiet.
        self._update(attempt, quiescent=False)
        for index, command in enumerate(profile):
            log = self.artifacts(attempt) / f"verification-{index}.log"
            with log.open("wb") as output:
                os.chmod(log, 0o600)
                process = subprocess.Popen(command, cwd=self.checkout(spec), stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                self._update(attempt, local_pgid=process.pid)
                try:
                    code = process.wait(timeout=spec.get("limits", {}).get("verification_seconds", 900))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise Blocked("verification deadline exceeded; inspect private log")
                finally:
                    # A trusted check which leaves background work is not done.
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        self._update(attempt, local_pgid=None)
                    else:
                        raise Blocked("verification left active descendants; inspection required")
            outcomes.append({"argv": command, "returncode": code, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
            evidence = {"candidate_sha": candidate, "commands": outcomes}
            private_write(self.artifacts(attempt) / "verification.json", canonical(evidence))
            self._update(attempt, verification=evidence)
            if code:
                raise Blocked("trusted verification failed; inspect private log")
        return {"candidate_sha": candidate, "commands": outcomes}

    def _finish_coding(self, spec: dict) -> dict:
        attempt = spec["attempt_id"]
        _, data = self._row(attempt)
        git = self.git(spec)
        result_path = self.artifacts(attempt) / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        if not isinstance(result, dict):
            raise Blocked("private result must be a JSON object")
        check_secrets(canonical(result))
        dispositions = result.get("feedback_dispositions", [])
        if spec["stage"] == "feedback":
            snapshot = self._prompt_spec(spec)["feedback"]
            ids = {item["id"] for surface in ("conversation", "reviews", "inline") for item in snapshot.get(surface, [])}
            allowed = {"fixed", "already addressed", "not applicable", "needs decision"}
            if (not isinstance(dispositions, list) or any(not isinstance(item, dict) or item.get("disposition") not in allowed
                    or not isinstance(item.get("reason"), str) or not item["reason"].strip() for item in dispositions)
                    or not ids <= {item.get("id") for item in dispositions}):
                raise Blocked("feedback result needs dispositions and reasons for the immutable snapshot")
        summary = {"summary": result.get("summary", ""), "feedback_dispositions": dispositions,
                   "unresolved_decisions": result.get("unresolved_decisions", []), "result_hash": digest(result),
                   "execution_assurance": data.get("execution_assurance", "strict")}
        if git.head() == spec["input_head_sha"] and git.clean():
            if data["state"] == "cancel_requested":
                state = "inspection_required" if data.get("pause_requested") else "cancelled"
            else:
                state = "noop"
            return self._update(attempt, state=state, quiescent=True, publication_state="unpublished", summary=summary)
        if not data.get("candidate_sha"):
            raise Blocked("interrupted edits or missing candidate helper; work retained, not delivered")
        paths = self._check_scope(spec, data, git)
        if not paths and spec["stage"] != "conflicts":
            raise Blocked("candidate has no content changes; empty commit publication forbidden")
        if not result_path.exists():
            raise Blocked("missing private result evidence")
        verification = self._verify(spec, data["candidate_sha"])
        self._check_scope(spec, data, git)
        prompt = review_prompt(spec, data["candidate_sha"], data["manifest_hash"], data["manifest"],
                               git.diff(spec["input_head_sha"], data["candidate_sha"]), verification, result)
        private_write(self.artifacts(attempt) / "review-prompt.txt", prompt)
        review = self._create_session(spec, "review_session_id", f"PR {spec['pr_id']} · read-only review · {attempt}", spec["review_agent"], spec["review_model"])
        events = self.artifacts(attempt) / "review-events.jsonl"
        self.runner.run(git.path, review, spec["review_model"], spec["review_agent"], prompt, events, spec.get("limits", {}).get("review_seconds", 900))
        _, current = self._row(attempt)
        if not self._sessions_quiet(spec, current):
            raise Blocked("review or descendants not proved stopped; inspection required")
        if not Runner.review_approved(events, data["candidate_sha"], data["manifest_hash"]):
            raise Blocked("read-only implementation review did not approve exact candidate/scope")
        self._check_scope(spec, data, git)
        # Cancellation can arrive during verification/review. Never overwrite it.
        with transaction(self.db):
            _, current = self._row(attempt)
            if current["state"] == "cancel_requested":
                raise Blocked("cancellation requested; candidate retained, not delivered")
            current.update(state="ready_to_publish", quiescent=True, publication_state="unpublished", verification=verification,
                           review_sha256=hashlib.sha256(events.read_bytes()).hexdigest(),
                           summary=dict(summary, changed_paths=paths, deleted_paths=[os.fsdecode(path) for path in git.run(
                               "diff", "--name-only", "--diff-filter=D", "--no-renames", "-z", spec["input_head_sha"], data["candidate_sha"], "--"
                           ).stdout.split(b"\0") if path]))
            self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(current), attempt))
        return self.status(attempt)

    def _publish(self, spec: dict) -> dict:
        attempt = spec["attempt_id"]
        _, data = self._row(attempt)
        if data.get("execution_assurance") == "best_effort" and not self._sessions_quiet(spec, data):
            raise Blocked("session completion observations changed before publication; inspect retained work")
        git = self.git(spec)
        self._check_scope(spec, data, git)
        verification = data.get("verification", {})
        try:
            stored = json.loads((self.artifacts(attempt) / "verification.json").read_text())
            if stored != verification or [item["argv"] for item in verification.get("commands", [])] != self._profile(spec, data):
                raise Blocked("stored verification evidence changed")
            for index, item in enumerate(verification["commands"]):
                if hashlib.sha256((self.artifacts(attempt) / f"verification-{index}.log").read_bytes()).hexdigest() != item["log_sha256"]:
                    raise Blocked("stored verification log changed")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise Blocked("stored verification artifacts missing or changed") from error
        evidence_path = self.artifacts(attempt) / "review-events.jsonl"
        if (data.get("verification", {}).get("candidate_sha") != data["candidate_sha"]
                or not data["verification"].get("commands")
                or any(item["returncode"] for item in data["verification"]["commands"])
                or not Runner.review_approved(evidence_path, data["candidate_sha"], data["manifest_hash"])
                or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != data.get("review_sha256")):
            raise Blocked("stored trusted verification/review evidence is missing or changed")
        # Reconcile a durable remote update before deciding an advanced head is
        # somebody else's work (e.g. a restored pre-push ledger snapshot).
        if git.publication(spec["pr"]["head"]["ref"], spec["input_head_sha"], data["candidate_sha"]) == "already_published":
            return self._published(spec)
        live = self.live(spec)
        if live["head"]["sha"] != spec["input_head_sha"]:
            raise Blocked("head advanced before publication")
        if spec["stage"] == "conflicts" and live["base"]["sha"] != spec["base_sha"]:
            self._update(attempt, retryable_reason="base_advanced")
            raise Blocked("base advanced; retain candidate and recompute within the configured attempt budget")
        with transaction(self.db):
            _, current = self._row(attempt)
            self._grant(attempt, current.get("authorization") or {}, current, spec)
            if not current["quiescent"] or current["state"] != "ready_to_publish":
                raise Blocked("publication requires quiescent exact ready candidate")
            current.update(state="publishing", publication_state="push_in_flight", quiescent=False)
            self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(current), attempt))
        try:
            git.publish(spec["pr"]["head"]["ref"], spec["input_head_sha"], data["candidate_sha"], prechecked=True)
        except BaseException:
            self._update(attempt, state="publication_unknown", publication_state="unknown", quiescent=False,
                         error="push outcome requires remote and execution reconciliation")
            raise
        return self._published(spec)

    def _published(self, spec: dict) -> dict:
        concern = None
        try:
            live = self.live(spec)
            if spec["stage"] == "conflicts" and live["base"]["sha"] != spec["base_sha"]:
                concern = "base advanced after conflict publication"
            _, data = self._row(spec["attempt_id"])
            git = self.git(spec)
            observed = git.observed_head(spec["pr"]["head"]["ref"])
            if not git.ancestor(data["candidate_sha"], observed):
                concern = "confirmed published candidate no longer reachable from head; inspect concurrent update"
        except (Blocked, OSError, subprocess.TimeoutExpired):
            concern = "post-publication lifecycle/identity check failed; inspect published candidate"
        return self._update(spec["attempt_id"], state="published_with_concern" if concern else "pushed",
                            publication_state="published", quiescent=True, error=concern)

    def _interrupted_quiet(self, spec: dict, data: dict, *, require_host_proof: bool = False) -> bool:
        if data.get("execution_assurance") == "best_effort":
            # Only a successfully completed candidate can resume its grant
            # handshake. Idle observations cannot release interrupted ownership.
            return (not require_host_proof and data["state"] == "ready_to_publish"
                    and data.get("quiescent") is True and not data.get("execution_interrupted")
                    and not data.get("local_pgid") and self._sessions_quiet(spec, data))
        if data.get("quiescent") and not require_host_proof and not data.get("local_pgid"):
            return True
        if self._active_sessions(data) and not self._sessions_quiet(spec, data):
            return False
        # This trusted host-side probe additionally proves local unit descendants,
        # including an orphaned Git/verification process, stopped. No default probe.
        argv = self.config.get("worker_quiescence_argv")
        if not argv:
            return False
        try:
            result = subprocess.run([arg.replace("{attempt_id}", spec["attempt_id"]).replace("{unit}", self.unit(spec["attempt_id"])) for arg in argv],
                                    cwd=self.root, capture_output=True, timeout=30)
            proof = json.loads(result.stdout)
            return result.returncode == 0 and proof.get("attempt_id") == spec["attempt_id"] and proof.get("descendants_quiescent") is True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def reconcile_attempt(self, attempt: str) -> dict:
        try:
            with self.lock("attempt-" + self.identity(attempt)):
                spec, data = self._row(attempt)
                if data["state"] in TERMINAL or not data.get("started"):
                    return self.status(attempt)
                if not self._interrupted_quiet(spec, data):
                    state = "cancel_requested" if data["state"] == "cancel_requested" else (
                        "publication_unknown" if data["publication_state"] in ("unknown", "push_in_flight") else "inspection_required")
                    return self._update(attempt, state=state, quiescent=False, error="execution/descendants not proved stopped; no automatic prompt retry")
                if data.get("candidate_sha"):
                    try:
                        publication = self.git(spec).publication(spec["pr"]["head"]["ref"], spec["input_head_sha"], data["candidate_sha"])
                    except (Blocked, OSError, subprocess.TimeoutExpired) as error:
                        if isinstance(error, Blocked) and "head advanced" in str(error) and data["publication_state"] not in ("unknown", "push_in_flight"):
                            return self._update(attempt, state="blocked", publication_state="unpublished", quiescent=True,
                                                error="head advanced; retain candidate for explicit fresh attempt")
                        return self._update(attempt, state="publication_unknown", publication_state="unknown", quiescent=False,
                                            error="candidate retained; remote publication not established")
                    if publication == "already_published":
                        return self._published(spec)
                    if data["state"] == "cancel_requested":
                        if data.get("pause_requested") and data.get("review_sha256"):
                            return self._update(attempt, state="ready_to_publish", publication_state="unpublished", quiescent=True, authorization=None,
                                                error="paused candidate retained; fresh epoch authorization required on resume")
                        if data.get("pause_requested"):
                            return self._update(attempt, state="inspection_required", publication_state="unpublished", quiescent=True,
                                                error="paused incomplete evidence; retained candidate requires inspection")
                        return self._update(attempt, state="cancelled", publication_state="unpublished", quiescent=True)
                    if data.get("review_sha256"):
                        try:
                            live = self.live(spec)
                            if live["head"]["sha"] != spec["input_head_sha"]:
                                raise Blocked("head advanced")
                            if spec["stage"] == "conflicts" and live["base"]["sha"] != spec["base_sha"]:
                                self._update(attempt, retryable_reason="base_advanced")
                                raise Blocked("base advanced")
                            self._check_scope(spec, data, self.git(spec))
                        except (Blocked, OSError) as error:
                            return self._update(attempt, state="blocked", publication_state="unpublished", quiescent=True,
                                                error="candidate retained; explicit fresh attempt required: " + str(error))
                        return self._update(attempt, state="ready_to_publish", publication_state="unpublished", quiescent=True, authorization=None,
                                            error="candidate recovered; fresh authorization required")
                state = "cancelled" if data["state"] == "cancel_requested" and not data.get("pause_requested") else "blocked"
                if data.get("pause_requested"):
                    state = "inspection_required"
                return self._update(attempt, state=state, publication_state="unpublished", quiescent=True,
                                    error="interrupted edits or inconclusive execution evidence; work retained, no prompt retry")
        except Busy:
            return self.status(attempt)

    def reconcile(self) -> list[dict]:
        results = []
        for row in self.db.execute("SELECT id FROM worker_attempts ORDER BY id").fetchall():
            attempt = row["id"]
            _, data = self._row(attempt)
            if (not data.get("started") and data["state"] == "accepted") or (data["state"] == "ready_to_publish" and data.get("authorization")):
                try:
                    active = self._systemctl("is-active", attempt)
                except (OSError, subprocess.TimeoutExpired):
                    active = True
                if not active:
                    self._schedule(attempt)
                results.append(self.status(attempt))
            else:
                results.append(self.reconcile_attempt(attempt))
        return results

    def execute(self, attempt: str) -> dict:
        spec, data = self._row(attempt)
        if data["state"] in TERMINAL or data["state"] == "cancel_requested":
            return self.status(attempt)
        if data.get("started") and data["state"] != "ready_to_publish":
            return self.reconcile_attempt(attempt)
        try:
            with self.lock("execution"), self.lock("branch-" + digest(self.branch(spec))), self.lock("attempt-" + self.identity(attempt)):
                # Persistent unknown execution on another branch blocks a second
                # writer even after reboot released all kernel locks.
                for row in self.db.execute("SELECT id,data FROM worker_attempts WHERE id!=?", (attempt,)):
                    other = json.loads(row["data"])
                    if other.get("started") and not other["quiescent"]:
                        raise Busy("another attempt has unresolved execution ownership")
                spec, data = self._row(attempt)
                if data["state"] in TERMINAL or data["state"] == "cancel_requested":
                    return self.status(attempt)
                if not self.config.get("execution_enabled") or not self.config.get("rollout_acknowledged"):
                    raise Blocked("onboarding: execution and shared-account rollout acknowledgement are required")
                if data.get("execution_assurance", "strict") != self.config.get("quiescence_mode", "strict"):
                    raise Blocked("execution assurance changed for an accepted attempt; inspect before continuing")
                if data["state"] == "ready_to_publish":
                    if not data.get("authorization"):
                        return self.status(attempt)
                    return self._publish(spec)
                # CAS the one-way marker against cancellation and a competing
                # invocation that started since this invocation's initial read.
                with transaction(self.db):
                    _, current = self._row(attempt)
                    if current["state"] != "accepted" or current.get("started"):
                        return self.status(attempt)
                    current.update(started=True, started_at=time.time(), state="running", quiescent=False)
                    self.db.execute("UPDATE worker_attempts SET data=? WHERE id=?", (canonical(current), attempt))
                self.free_space()
                self.runner.check_contract(self.root)
                live = self.live(spec)
                if live["head"]["sha"] != spec["input_head_sha"]:
                    raise Blocked("head advanced before execution")
                if live["base"]["sha"] != spec["base_sha"]:
                    if spec["stage"] == "conflicts":
                        self._update(attempt, retryable_reason="base_advanced")
                    raise Blocked("base advanced before execution")
                skills, skill_text = load_skills(spec["stage"], Path(self.config["skills_dir"]))
                self._update(attempt, skill_hashes=skills, runner_version=self.config["runner_version"])
                git = self.git(spec)
                # Private numeric parents; never derive filesystem names from refs.
                for parent in (git.path.parent.parent.parent, git.path.parent.parent, git.path.parent):
                    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.chmod(parent, 0o700)
                git.prepare(spec["pr"]["head"]["ref"], spec["input_head_sha"], spec["pr"]["base"]["ref"], spec["base_sha"])
                if spec["stage"] == "conflicts" and not git.begin_conflict_merge(spec["input_head_sha"], spec["base_sha"]):
                    return self._update(attempt, state="noop", quiescent=True, publication_state="unpublished")
                self.runner.check_available(git.path, [spec["model"], spec["review_model"]], [spec["agent"], spec["review_agent"]])
                # Triple-dot captures the full PR contribution, not base-only changes.
                merge_base = git.run("merge-base", spec["base_sha"], spec["input_head_sha"]).stdout.decode().strip()
                prompt = coding_prompt(self._prompt_spec(spec), git.diff(merge_base, spec["input_head_sha"]), skill_text, self.artifacts(attempt), self.helper())
                private_write(self.artifacts(attempt) / "prompt.txt", prompt)
                self._update(attempt, prompt_hash=digest(prompt))
                session = self._create_session(spec, "session_id", f"{spec['pr']['base']['repo']['full_name']} #{spec['pr']['number']} · {spec['stage']} · {attempt}", spec["agent"], spec["model"])
                self.runner.run(git.path, session, spec["model"], spec["agent"], prompt, self.artifacts(attempt) / "events.jsonl", spec.get("limits", {}).get("stage_seconds", 2700))
                _, current = self._row(attempt)
                if not self._sessions_quiet(spec, current):
                    raise Blocked("session or descendants not proved stopped; inspection required")
                if current["state"] == "cancel_requested":
                    if current.get("pause_requested"):
                        return self._update(attempt, state="inspection_required", publication_state="unpublished", quiescent=True,
                                            error="paused interrupted stage; preserve work for inspection")
                    return self._update(attempt, state="cancelled", publication_state="unpublished", quiescent=True)
                self._update(attempt, quiescent=True)
                return self._finish_coding(spec)
        except Busy:
            return self.status(attempt)
        except (Blocked, OSError, ValueError, subprocess.SubprocessError) as error:
            _, current = self._row(attempt)
            if current["publication_state"] in ("unknown", "push_in_flight"):
                return self.status(attempt)
            if isinstance(error, subprocess.TimeoutExpired):
                for session in self._active_sessions(current):
                    try:
                        self.runner.interrupt(session, self.checkout(spec))
                    except (Blocked, OSError, subprocess.TimeoutExpired):
                        pass
            quiet = self._interrupted_quiet(spec, current, require_host_proof=True)
            state = "blocked" if quiet else "inspection_required"
            if isinstance(error, GrantRejected) and current["state"] == "ready_to_publish":
                return self._update(attempt, state="ready_to_publish", quiescent=True, authorization=None, error=str(error))
            if current["state"] == "cancel_requested":
                state = "cancelled" if quiet else "cancel_requested"
                if current.get("pause_requested"):
                    state = "inspection_required" if quiet else "cancel_requested"
            if isinstance(error, subprocess.TimeoutExpired) and not quiet:
                state = "deadline_exceeded"
            # Exception strings from tooling can contain secrets; only our own
            # content-free Blocked messages are reportable.
            reason = str(error) if isinstance(error, Blocked) else "worker tool failure; inspect private owned artifacts"
            return self._update(attempt, state=state, quiescent=quiet, publication_state="unpublished", error=reason,
                                execution_interrupted=True)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("transport")
    sub.add_parser("reconcile")
    execute = sub.add_parser("execute")
    execute.add_argument("attempt_id")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("attempt_id")
    prepare.add_argument("--evidence", type=Path, required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("attempt_id")
    args = parser.parse_args(argv)
    worker = None
    try:
        config_file = str(Path(args.config).resolve())
        config = json.loads(Path(config_file).read_text())
        config["config_file"] = config_file
        worker = Worker(config)
        if args.command == "transport":
            raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise Blocked("transport request exceeds configured input bound")
            result = worker.transport(json.loads(raw))
        elif args.command == "execute":
            result = worker.execute(args.attempt_id)
        elif args.command == "reconcile":
            result = worker.reconcile()
        elif args.command == "prepare":
            result = worker.record_manifest(args.attempt_id, args.evidence)
        else:
            result = worker.record_candidate(args.attempt_id)
        print(canonical(result))
        return 0
    except (Blocked, OSError, ValueError, KeyError, TypeError) as error:
        print(canonical({"error": str(error) if isinstance(error, Blocked) else "invalid worker request/configuration; inspect local setup"}))
        return 1
    finally:
        if worker is not None:
            worker.close()


if __name__ == "__main__":
    raise SystemExit(main())
