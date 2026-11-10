from __future__ import annotations

import json
import logging
import math
import time
import uuid
from datetime import datetime
from pathlib import Path

from .common import Blocked, STAGES, TERMINAL, canonical, database, digest, private_write, transaction


SCOPE_PAUSE = "outside configured immutable ID scope"


def timestamp(value) -> float:
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("timestamp needs an explicit timezone")
        result = instant.timestamp()
    if not math.isfinite(result):
        raise ValueError("timestamp must be finite")
    return result


def identity(pr: dict) -> tuple:
    return (pr["head"]["repo"]["id"], pr["head"]["ref"],
            pr["base"]["repo"]["id"], pr["base"]["ref"])


class Coordinator:
    def __init__(self, path: str | Path, config: dict, now=time.time, *, connection_timeout=30):
        self.path = Path(path)
        self.db = database(path, timeout=connection_timeout)
        self.config, self.now = config, now
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflows (
                pr_id INTEGER PRIMARY KEY, repository_id INTEGER NOT NULL,
                repository TEXT NOT NULL, number INTEGER NOT NULL,
                head_repository_id INTEGER, head_ref TEXT, pr TEXT NOT NULL,
                eligible_at REAL, feedback_due_at REAL,
                lifecycle_state TEXT NOT NULL, paused_reason TEXT,
                observed_at REAL NOT NULL, delay_notified INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS stages (
                pr_id INTEGER NOT NULL REFERENCES workflows(pr_id),
                kind TEXT NOT NULL, ordinal INTEGER NOT NULL, state TEXT NOT NULL,
                active_attempt_id TEXT, authorization_epoch INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (pr_id, kind)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, pr_id INTEGER NOT NULL, stage TEXT NOT NULL,
                spec TEXT NOT NULL, spec_hash TEXT NOT NULL, state TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
                last_contact REAL, submitted INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(pr_id, stage) REFERENCES stages(pr_id, kind)
            );
            CREATE TABLE IF NOT EXISTS branch_claims (
                head_repository_id INTEGER NOT NULL, head_ref TEXT NOT NULL,
                pr_id INTEGER NOT NULL, attempt_id TEXT NOT NULL,
                unresolved INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(head_repository_id, head_ref)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY, received_at REAL NOT NULL, event TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refreshes (
                repository TEXT NOT NULL, number INTEGER NOT NULL,
                PRIMARY KEY(repository, number)
            );
            CREATE TABLE IF NOT EXISTS ready_observations (
                pr_id INTEGER PRIMARY KEY, received_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventories (
                repository_id INTEGER PRIMARY KEY, repository TEXT NOT NULL,
                inventoried INTEGER NOT NULL DEFAULT 0, cursor REAL, last_success REAL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL,
                pr_id INTEGER, stage TEXT, reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incident_acknowledgements (
                attempt_id TEXT PRIMARY KEY REFERENCES attempts(id),
                epoch INTEGER NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS queue_waits (
                pr_id INTEGER PRIMARY KEY REFERENCES workflows(pr_id),
                blocker TEXT NOT NULL, observed_at REAL NOT NULL
            );
        """)

    def enable(self, at: float | None = None):
        with transaction(self.db):
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('enabled_at', ?)",
                            (str(self.now() if at is None else at),))

    def meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def workflow(self, pr_id: int):
        row = self.db.execute("SELECT * FROM workflows WHERE pr_id=?", (pr_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["pr"] = json.loads(result["pr"])
        result["has_attempts"] = self.db.execute("SELECT 1 FROM attempts WHERE pr_id=? LIMIT 1", (pr_id,)).fetchone() is not None
        wait = self.db.execute("SELECT blocker FROM queue_waits WHERE pr_id=?", (pr_id,)).fetchone()
        result["last_queue_blocker"] = json.loads(wait[0]) if wait else None
        return result

    def stages(self, pr_id: int):
        rows = self.db.execute("""SELECT s.*, a.result, a.spec, a.created_at
            FROM stages s LEFT JOIN attempts a ON a.id=s.active_attempt_id
            WHERE s.pr_id=? ORDER BY s.ordinal""", (pr_id,))
        return [dict(row) | {"result": json.loads(row["result"] or "{}"),
                             "spec": json.loads(row["spec"] or "{}")} for row in rows]

    def in_scope(self, pr: dict) -> bool:
        """Optional scope is exact numeric identity, independent of auth mode."""
        repository = pr.get("base", {}).get("repo", {})
        if self.config.get("private_repositories_only", False):
            head_repository = pr.get("head", {}).get("repo") or {}
            owner_id = repository.get("owner", {}).get("id")
            names = self.config.get("allow_repositories", [])
            if (repository.get("private") is not True or head_repository.get("private") is not True
                    or type(owner_id) is not int
                    or owner_id != self.config.get("organization_id", 97932676)
                    or (names and repository.get("full_name", "").casefold() not in {name.casefold() for name in names})):
                return False
        for field, value in (
            ("allow_pr_ids", pr.get("id")),
            ("allow_repository_ids", pr.get("base", {}).get("repo", {}).get("id")),
        ):
            allowed = self.config.get(field, [])
            if not isinstance(allowed, list):
                return False
            if allowed and (type(value) is not int or not any(type(item) is int and item == value for item in allowed)):
                return False
        return True

    def _pause_scope(self, pr_id: int):
        workflow = self.workflow(pr_id)
        if workflow["lifecycle_state"] in {"completed", "cancelled", "cancel_requested", "baselined_excluded"}:
            return
        reason = SCOPE_PAUSE
        if workflow["lifecycle_state"] != "paused" or workflow["paused_reason"] != reason:
            self._revoke(pr_id)
            self.db.execute("UPDATE workflows SET lifecycle_state='paused',paused_reason=? WHERE pr_id=?", (reason, pr_id))

    def observe(self, pr: dict, at: float | None = None):
        at = self.now() if at is None else at
        with transaction(self.db):
            existing = self.workflow(pr["id"])
            if (existing is None and self.config.get("private_repositories_only", False)
                    and pr["base"]["repo"].get("private") is not True):
                return
            matches = (pr["user"]["id"] == self.config.get("author_id", 34589654)
                       and pr["base"]["repo"]["owner"]["id"] == self.config.get("organization_id", 97932676))
            if not matches and existing is None:
                return
            if self.meta("enabled_at") is None:
                raise Blocked("persist enabled_at before inventory")
            eligible = existing["eligible_at"] if existing else None
            reason = existing["paused_reason"] if existing else None
            state = existing["lifecycle_state"] if existing else None
            cancelled = (not matches or pr["state"] != "open" or pr.get("merged", False)
                         or not pr.get("head", {}).get("repo"))
            preserved = state in {"completed", "cancelled", "baselined_excluded"}
            if cancelled and not preserved:
                state = "cancel_requested"
                reason = ("PR merged" if pr.get("merged", False) else "PR closed" if pr["state"] != "open"
                          else "closed, removed, or identity no longer eligible")
            elif state in {"cancel_requested", "cancelled", "completed", "baselined_excluded"}:
                pass
            elif pr.get("draft", False):
                if reason != SCOPE_PAUSE:
                    state, reason = ("waiting_draft" if eligible is None else "paused"), "draft"
            else:
                if eligible is None:
                    if existing and (existing["lifecycle_state"] == "waiting_draft" or
                                     (reason == SCOPE_PAUSE and existing["pr"].get("draft"))):
                        ready_event = self.db.execute("SELECT received_at FROM ready_observations WHERE pr_id=?", (pr["id"],)).fetchone()
                        eligible = ready_event[0] if ready_event else at
                    elif timestamp(pr["created_at"]) >= float(self.meta("enabled_at")):
                        eligible = timestamp(pr["created_at"])
                    else:
                        state, reason = "baselined_excluded", "pre-enablement ready PR; explicit enroll required"
                if eligible is not None and state != "baselined_excluded":
                    if reason == "draft":
                        state, reason = "active", None
                    elif state is None or state == "waiting_draft":
                        state, reason = "active", None
            head_repo = pr.get("head", {}).get("repo") or {}
            self.db.execute("""INSERT INTO workflows
                (pr_id,repository_id,repository,number,head_repository_id,head_ref,pr,
                 eligible_at,feedback_due_at,lifecycle_state,paused_reason,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(pr_id) DO UPDATE SET
                repository_id=excluded.repository_id,repository=excluded.repository,
                number=excluded.number,head_repository_id=excluded.head_repository_id,
                head_ref=excluded.head_ref,pr=excluded.pr,eligible_at=excluded.eligible_at,
                feedback_due_at=excluded.feedback_due_at,lifecycle_state=excluded.lifecycle_state,
                paused_reason=excluded.paused_reason,observed_at=excluded.observed_at""",
                (pr["id"], pr["base"]["repo"]["id"], pr["base"]["repo"]["full_name"],
                 pr["number"], head_repo.get("id"), pr.get("head", {}).get("ref"), canonical(pr),
                 eligible, eligible + self.config.get("feedback_delay", 900) if eligible is not None else None,
                 state or "cancel_requested", reason, at))
            for ordinal, kind in enumerate(STAGES):
                self.db.execute("INSERT OR IGNORE INTO stages (pr_id,kind,ordinal,state) VALUES (?,?,?,'pending')",
                                (pr["id"], kind, ordinal))
            # An active attempt pins its identities. Metadata refresh cannot silently retarget it.
            for stage in self.stages(pr["id"]):
                if stage["active_attempt_id"] and stage["state"] not in TERMINAL:
                    pinned = stage["spec"].get("pr")
                    changed = bool(pinned and head_repo and identity(pinned) != identity(pr))
                    if changed:
                        self.db.execute("UPDATE workflows SET lifecycle_state='paused',paused_reason='retargeted attempt' WHERE pr_id=?", (pr["id"],))
                    if cancelled or pr.get("draft") or changed:
                        self._revoke(pr["id"], stage["kind"])
            if cancelled and not preserved:
                self._finish_cancellation(pr["id"])
            elif not self.in_scope(pr):
                self._pause_scope(pr["id"])

    def _revoke(self, pr_id: int, stage: str | None = None):
        self.db.execute("UPDATE stages SET authorization_epoch=authorization_epoch+1 WHERE pr_id=?" +
                        (" AND kind=?" if stage else ""), (pr_id, stage) if stage else (pr_id,))

    def _finish_cancellation(self, pr_id: int):
        unknown = self.db.execute("SELECT 1 FROM stages WHERE pr_id=? AND active_attempt_id IS NOT NULL AND state NOT IN ('pushed','published_with_concern','noop','blocked','failed','cancelled')", (pr_id,)).fetchone()
        if unknown is None:
            self.db.execute("UPDATE stages SET state='cancelled' WHERE pr_id=? AND state='pending'", (pr_id,))
            self.db.execute("UPDATE workflows SET lifecycle_state='cancelled' WHERE pr_id=?", (pr_id,))

    def claim(self, pr_id: int, at: float | None = None):
        at = self.now() if at is None else at
        with transaction(self.db):
            workflow = self.workflow(pr_id)
            if not workflow:
                return None
            if not self.in_scope(workflow["pr"]):
                self._pause_scope(pr_id)
                return None
            if workflow["lifecycle_state"] != "active":
                return None
            if self.db.execute("SELECT 1 FROM incident_acknowledgements i JOIN attempts a ON a.id=i.attempt_id WHERE a.pr_id=? AND i.delivered=0", (pr_id,)).fetchone():
                return None
            stages = self.stages(pr_id)
            for stage in stages:
                if stage["state"] not in TERMINAL and stage["active_attempt_id"]:
                    if not self.in_scope(stage["spec"]["pr"]):
                        self._pause_scope(pr_id)
                        return None
                    return stage["spec"]
            next_stage = next((s for s in stages if s["state"] not in TERMINAL), None)
            if next_stage is None:
                self.db.execute("UPDATE workflows SET lifecycle_state='completed' WHERE pr_id=?", (pr_id,))
                return None
            if next_stage["ordinal"] >= 2 and at < workflow["feedback_due_at"]:
                return None
            if self.config.get("stale_after") and at - workflow["eligible_at"] > self.config["stale_after"]:
                self.db.execute("UPDATE workflows SET lifecycle_state='paused',paused_reason='stale work hold' WHERE pr_id=?", (pr_id,))
                return None
            head = (workflow["head_repository_id"], workflow["head_ref"])
            claim = self.db.execute("SELECT * FROM branch_claims WHERE head_repository_id=? AND head_ref=?", head).fetchone()
            other = self.db.execute("""SELECT pr_id FROM workflows WHERE head_repository_id=? AND head_ref=?
                AND pr_id<>? AND lifecycle_state NOT IN ('cancelled','baselined_excluded') ORDER BY pr_id LIMIT 1""",
                (*head, pr_id)).fetchone()
            if (claim and claim["pr_id"] != pr_id) or (not claim and other and other[0] < pr_id):
                self.db.execute("UPDATE workflows SET lifecycle_state='paused',paused_reason='waiting_same_branch' WHERE pr_id=?", (pr_id,))
                return None
            pr = workflow["pr"]
            attempt_id = str(uuid.uuid4())
            ordinal = next_stage["ordinal"]
            spec = {
                "attempt_id": attempt_id, "pr_id": pr_id, "stage": next_stage["kind"],
                "ordinal": ordinal, "pr": pr, "input_head_sha": pr["head"]["sha"],
                "base_sha": pr["base"]["sha"], "authorization_epoch": next_stage["authorization_epoch"],
                "config_hash": digest(self.config), "model": self.config.get("models", {}).get(next_stage["kind"]),
                "agent": self.config.get("agents", {}).get(next_stage["kind"]),
                "review_model": self.config.get("models", {}).get("review"),
                "review_agent": self.config.get("agents", {}).get("review"),
                "verification_profile": self.config.get("verification_profiles", {}).get(workflow["repository"]),
                "feedback": None, "limits": {
                    "stage_seconds": self.config.get("stage_deadline", 2700),
                    "verification_seconds": self.config.get("verification_deadline", 900),
                    "review_seconds": self.config.get("review_deadline", 900),
                },
                "worker_host": self.config.get("worker_host", "contents-may-differ"),
            }
            self.db.execute("INSERT INTO attempts (id,pr_id,stage,spec,spec_hash,state,created_at) VALUES (?,?,?,?,?,'waiting_worker',?)",
                            (attempt_id, pr_id, next_stage["kind"], canonical(spec), digest(spec), at))
            self.db.execute("UPDATE stages SET active_attempt_id=?,state='waiting_worker' WHERE pr_id=? AND kind=?",
                            (attempt_id, pr_id, next_stage["kind"]))
            self.db.execute("INSERT INTO branch_claims VALUES (?,?,?,?,1) ON CONFLICT(head_repository_id,head_ref) DO UPDATE SET pr_id=excluded.pr_id,attempt_id=excluded.attempt_id,unresolved=1",
                            (*head, pr_id, attempt_id))
            return spec

    def attach_feedback(self, attempt_id: str, snapshot: dict):
        # Fetch outside the transaction, then seal once before the first SSH call.
        with transaction(self.db):
            attempt = self.attempt(attempt_id)
            if attempt["submitted"] or attempt["spec"].get("feedback") is not None:
                return attempt["spec"]
            private_write(self.feedback_path(attempt_id), canonical(snapshot))
            reference = {"sha256": digest(snapshot), "started_at": snapshot.get("started_at"),
                         "finished_at": snapshot.get("finished_at")}
            spec = attempt["spec"] | {"feedback": reference}
            self.db.execute("UPDATE attempts SET spec=?,spec_hash=? WHERE id=?", (canonical(spec), digest(spec), attempt_id))
            return spec

    def feedback_path(self, attempt_id: str) -> Path:
        return self.path.parent / "feedback-spool" / f"{uuid.UUID(attempt_id)}.json"

    def attempt(self, attempt_id: str):
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            raise Blocked("unknown coordinator attempt; reconcile worker ledger before continuing")
        return dict(row) | {"spec": json.loads(row["spec"]), "result": json.loads(row["result"])}

    def mark_submitted(self, attempt_id: str):
        # Persist before transport: even a lost acknowledgement might mean work is running.
        self.db.execute("UPDATE attempts SET submitted=1 WHERE id=?", (attempt_id,))

    def accept_status(self, attempt_id: str, status: dict):
        if "execution_assurance" in status and status["execution_assurance"] not in ("strict", "best_effort"):
            raise Blocked("worker execution_assurance must be strict or best_effort")
        with transaction(self.db):
            attempt = self.attempt(attempt_id)
            if status.get("attempt_id", attempt_id) != attempt_id or status.get("spec_hash", attempt["spec_hash"]) != attempt["spec_hash"]:
                raise Blocked("worker acknowledgement identity or specification hash mismatch")
            state = status["state"]
            known = status.get("publication_state") in {"not_published", "published", "ready_to_publish"}
            if state in TERMINAL and not (status.get("quiescent") and known):
                state = "publication_unknown"
            self.db.execute("UPDATE attempts SET state=?,result=?,last_contact=? WHERE id=?",
                            (state, canonical(status), self.now(), attempt_id))
            self.db.execute("UPDATE stages SET state=? WHERE pr_id=? AND kind=? AND active_attempt_id=?",
                            (state, attempt["pr_id"], attempt["stage"], attempt_id))
            if state in TERMINAL:
                self.db.execute("UPDATE branch_claims SET unresolved=0 WHERE attempt_id=?", (attempt_id,))
            if state == "published_with_concern" and attempt["state"] != state:
                self.db.execute("UPDATE workflows SET lifecycle_state='paused',paused_reason='publication incident; acknowledgement required' WHERE pr_id=?", (attempt["pr_id"],))
                self._revoke(attempt["pr_id"])
            if self.workflow(attempt["pr_id"])["lifecycle_state"] == "cancel_requested":
                self._finish_cancellation(attempt["pr_id"])
        if attempt["state"] != state:
            logging.info(canonical({"event": "attempt_transition", "pr_id": attempt["pr_id"],
                                    "stage": attempt["stage"], "attempt_id": attempt_id, "state": state,
                                    "session_id": status.get("session_id"),
                                    "input_sha": attempt["spec"]["input_head_sha"],
                                    "candidate_sha": status.get("candidate_sha")}))

    def authorize(self, attempt_id: str, live: dict, at: float | None = None):
        at = self.now() if at is None else at
        with transaction(self.db):
            attempt = self.attempt(attempt_id)
            spec, result = attempt["spec"], attempt["result"]
            workflow = self.workflow(attempt["pr_id"])
            if not self.in_scope(workflow["pr"]) or not self.in_scope(spec["pr"]):
                self._pause_scope(attempt["pr_id"])
                return None
            if not self.in_scope(live):
                self._pause_scope(attempt["pr_id"])
                return None
            stage = self.db.execute("SELECT * FROM stages WHERE pr_id=? AND kind=?", (attempt["pr_id"], attempt["stage"])).fetchone()
            if workflow["lifecycle_state"] != "active" or stage["active_attempt_id"] != attempt_id:
                return None
            if result.get("publication_state") != "ready_to_publish" or not result.get("candidate_sha"):
                return None
            if (live["id"] != spec["pr_id"] or live["state"] != "open" or live.get("draft") or live.get("merged")
                    or not live["head"]["repo"] or identity(live) != identity(spec["pr"])
                    or live["head"]["sha"] != spec["input_head_sha"]
                    or live["user"]["id"] != self.config.get("author_id", 34589654)
                    or live["base"]["repo"]["owner"]["id"] != self.config.get("organization_id", 97932676)):
                return None
            return {"attempt_id": attempt_id, "candidate_sha": result["candidate_sha"],
                    "input_head_sha": spec["input_head_sha"], "head_repository_id": live["head"]["repo"]["id"],
                    "head_ref": live["head"]["ref"], "authorization_epoch": stage["authorization_epoch"],
                    "expires_at": at + self.config.get("publish_ttl", 60)}

    def control(self, action: str, pr_id: int, stage: str | None = None, reason: str = ""):
        if not reason.strip():
            raise Blocked("an audit reason is required")
        with transaction(self.db):
            workflow = self.workflow(pr_id)
            if workflow is None:
                raise Blocked("observe the PR before enrolling or controlling it")
            if action in {"enroll", "resume", "retry-stage"} and not self.in_scope(workflow["pr"]):
                raise Blocked("PR outside configured immutable ID scope")
            if action == "enroll":
                if workflow["lifecycle_state"] != "baselined_excluded":
                    raise Blocked("enroll only applies to a baselined excluded PR")
                at = self.now()
                if workflow["pr"].get("draft"):
                    self.db.execute("UPDATE workflows SET lifecycle_state='waiting_draft',paused_reason='draft',eligible_at=NULL,feedback_due_at=NULL WHERE pr_id=?", (pr_id,))
                else:
                    self.db.execute("UPDATE workflows SET lifecycle_state='active',paused_reason=NULL,eligible_at=?,feedback_due_at=? WHERE pr_id=?",
                                    (at, at + self.config.get("feedback_delay", 900), pr_id))
            elif action in {"pause", "cancel"}:
                self._revoke(pr_id)
                # Exclusion is an enrollment boundary, not a resumable pause.
                if workflow["lifecycle_state"] != "baselined_excluded":
                    self.db.execute("UPDATE workflows SET lifecycle_state=?,paused_reason=? WHERE pr_id=?",
                                    ("paused" if action == "pause" else "cancel_requested", reason, pr_id))
                    if action == "cancel":
                        self._finish_cancellation(pr_id)
            elif action == "resume":
                if workflow["lifecycle_state"] != "paused":
                    raise Blocked("only a paused workflow can resume")
                if workflow["pr"].get("draft") or workflow["pr"]["state"] != "open":
                    raise Blocked("PR is not open and ready")
                for incident in self.db.execute("SELECT s.active_attempt_id,s.authorization_epoch FROM stages s JOIN attempts a ON a.id=s.active_attempt_id WHERE s.pr_id=? AND s.state='published_with_concern'", (pr_id,)).fetchall():
                    attempt = self.attempt(incident["active_attempt_id"])
                    if attempt["result"].get("incident_acknowledged"):
                        continue
                    self._revoke(pr_id, attempt["stage"])
                    self.db.execute("INSERT OR REPLACE INTO incident_acknowledgements(attempt_id,epoch) VALUES (?,?)",
                                    (attempt["id"], incident["authorization_epoch"] + 1))
                if workflow["paused_reason"] == "waiting_same_branch":
                    claim = self.db.execute("SELECT * FROM branch_claims WHERE head_repository_id=? AND head_ref=?",
                                            (workflow["head_repository_id"], workflow["head_ref"])).fetchone()
                    if claim and claim["unresolved"]:
                        raise Blocked("shared branch owner still requires quiescence reconciliation")
                    if claim:
                        self.db.execute("UPDATE branch_claims SET pr_id=? WHERE head_repository_id=? AND head_ref=?",
                                        (pr_id, workflow["head_repository_id"], workflow["head_ref"]))
                self.db.execute("UPDATE workflows SET lifecycle_state='active',paused_reason=NULL WHERE pr_id=?", (pr_id,))
            elif action == "retry-stage":
                if workflow["lifecycle_state"] == "baselined_excluded":
                    raise Blocked("explicit enroll is required before retry")
                if stage not in STAGES:
                    raise Blocked("identify a valid stage")
                if workflow["pr"].get("draft") or workflow["pr"]["state"] != "open":
                    raise Blocked("PR is not open and ready")
                previous = next(s for s in self.stages(pr_id) if s["kind"] == stage)
                if previous["active_attempt_id"] and (previous["state"] not in TERMINAL or
                        not previous["result"].get("quiescent")):
                    raise Blocked("retry requires confirmed quiescence and known publication")
                if any(s["active_attempt_id"] and s["state"] not in TERMINAL for s in self.stages(pr_id)):
                    raise Blocked("another attempt still needs quiescence reconciliation")
                self._revoke(pr_id, stage)
                self.db.execute("UPDATE stages SET state='pending',active_attempt_id=NULL WHERE pr_id=? AND kind=?", (pr_id, stage))
                self.db.execute("UPDATE workflows SET lifecycle_state='active',paused_reason=NULL WHERE pr_id=?", (pr_id,))
            else:
                raise Blocked("unknown control action")
            self.db.execute("INSERT INTO audit(at,action,pr_id,stage,reason) VALUES (?,?,?,?,?)",
                            (self.now(), action, pr_id, stage, reason))

    def delivery(self, delivery_id: str, event: str, payload: dict, received_at: float):
        with transaction(self.db):
            if self.db.execute("SELECT 1 FROM deliveries WHERE delivery_id=?", (delivery_id,)).fetchone():
                return False
            self.db.execute("INSERT INTO deliveries VALUES (?,?,?)", (delivery_id, received_at, event))
            if event == "pull_request" and payload.get("repository", {}).get("full_name") and payload.get("number"):
                self.db.execute("INSERT OR IGNORE INTO refreshes VALUES (?,?)",
                                (payload["repository"]["full_name"], payload["number"]))
                if payload.get("action") == "ready_for_review" and payload.get("pull_request", {}).get("id"):
                    self.db.execute("INSERT OR IGNORE INTO ready_observations VALUES (?,?)", (payload["pull_request"]["id"], received_at))
            elif event in {"installation", "installation_repositories"}:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('inventory_required','1')")
            return True

    def pending_refreshes(self):
        return [tuple(row) for row in self.db.execute("SELECT repository,number FROM refreshes ORDER BY repository,number")]

    def status(self):
        workflows = [dict(row) for row in self.db.execute("SELECT pr_id,repository,number,lifecycle_state,paused_reason,eligible_at,feedback_due_at,observed_at FROM workflows")]
        attempts = []
        for row in self.db.execute("SELECT id,pr_id,stage,state,created_at,last_contact,result FROM attempts ORDER BY created_at"):
            value = dict(row)
            result = json.loads(value.pop("result"))
            value.update({key: result.get(key) for key in
                          ("session_id", "candidate_sha", "publication_state", "error", "quiescent", "operator_recovery")})
            attempts.append(value)
        active = [a for a in attempts if a["state"] not in TERMINAL]
        at = self.now()
        eligible = [w["eligible_at"] for w in workflows if w["lifecycle_state"] == "active" and w["eligible_at"] is not None]
        overdue = [at - w["feedback_due_at"] for w in workflows if w["lifecycle_state"] == "active" and w["feedback_due_at"] is not None]
        metrics = {"active_workflows": len(eligible), "oldest_eligible_age": max(0, at - min(eligible)) if eligible else None,
                   "feedback_overdue": max([0, *overdue]), "active_attempts": len(active),
                   "oldest_running_age": max([at - a["created_at"] for a in active] or [0]),
                   "worker_last_contact": max([a["last_contact"] for a in attempts if a["last_contact"] is not None] or [0]),
                   "ambiguous_publications": sum(a["state"] == "publication_unknown" for a in active)}
        return {"enabled_at": self.meta("enabled_at"), "baseline_complete": self.meta("baseline_complete"),
                "recovery_hold": self.meta("recovery_hold"), "last_tick": self.meta("last_tick"),
                "api_retry_at": self.meta("github_retry_at"), "worker_retry_at": self.meta("worker_retry_at"),
                "pending_incident_acknowledgements": [dict(row) for row in self.db.execute("SELECT * FROM incident_acknowledgements WHERE delivered=0")],
                "workflows": workflows, "attempts": attempts, "metrics": metrics,
                "inventories": [dict(row) for row in self.db.execute("SELECT * FROM inventories")]}
