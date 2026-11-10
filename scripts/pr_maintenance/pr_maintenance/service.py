from __future__ import annotations

import json
import re
import socket
import time
from datetime import datetime, timezone

from .common import Blocked, TERMINAL, canonical, check_secrets, digest, first_secret, private_write, transaction
from .coordinator import Coordinator, identity
from .reporting import brief, stage_title, state_title
from .transport import WorkerOffline


def execution_assurance(stages: list[dict]) -> str:
    affected = [stage_title(stage["kind"]) for stage in stages
                if stage["result"].get("execution_assurance") == "best_effort"]
    if not affected:
        return ""
    return f"\n\nBackground processes may still be running: {', '.join(affected)}."


def github_failure_details(error, at: float) -> str:
    """Explain a sanitized GitHubError, never an arbitrary upstream exception."""
    message = str(error)
    reason = brief({
        "GitHub API transport failed": "Could not connect to GitHub",
        "GitHub API request deferred": "GitHub could not complete the request",
    }.get(message, message)).rstrip(".")
    if error.status is not None and f"HTTP {error.status}" not in reason:
        reason += f" (HTTP {error.status})"
    retry = "Automatically on a scheduled poll."
    if error.retry_at is not None and error.retry_at > at:
        try:
            earliest = datetime.fromtimestamp(error.retry_at, timezone.utc)
            retry = f"Automatically on a scheduled poll, no earlier than {earliest:%Y-%m-%d %H:%M:%S UTC}."
        except (OverflowError, OSError, ValueError):
            pass
    if error.status in (401, 403):
        action = "Check the maintenance GitHub credentials and repository permissions."
    elif error.status == 404:
        action = "Check that the repository or PR still exists and that maintenance has permission to read it."
    elif error.retry_at is not None or error.status == 408 or (error.status is not None and error.status >= 500):
        action = "No immediate action. If this repeats, check GitHub availability and the coordinator's network connection."
    else:
        action = "If this repeats, inspect the coordinator's private GitHub diagnostics."
    return f"Reason: {reason}.\nRetry: {retry}\nAction: {action}"


class Service:
    def __init__(self, coordinator: Coordinator, github, worker, outbox, now=time.time):
        self.c, self.github, self.worker, self.outbox, self.now = coordinator, github, worker, outbox, now

    def notice(self, key: str, workflow_id: int | None, text: str):
        if first_secret(text):
            self.withheld_notice(key, workflow_id, text)
            return
        try:
            check_secrets(key)
        except Blocked:
            # An internal-key match is not evidence of a credential in the body.
            key = f"report:{digest(key)}"
        self.outbox.enqueue(key, workflow_id, text)

    def withheld_notice(self, key: str, workflow_id: int | None, text: str):
        def safe(value):
            value = str(value).replace("\r", " ").replace("\n", " ")
            return "[withheld]" if first_secret(value) else value

        workflow = self.c.workflow(workflow_id) if workflow_id is not None else None
        identity = (f"{workflow['repository']} #{workflow['number']}" if workflow else f"workflow {workflow_id}")
        lines = [f"PR maintenance: {safe(identity)}", "", "Notification withheld by outbound credential filter.",
                 "This is not a confirmed credential leak and not a repository scan.",
                 "", f"Host: {safe(socket.gethostname())}"]
        path = self.c.path.resolve().parent / "withheld-reports" / f"{digest(key)}.txt"
        report_key = f"secret:{digest(key)}"
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8")
            else:
                private_write(path, text)
            match = first_secret(text)
            line = text.count("\n", 0, match.start()) + 1 if match else 1
            lines.extend([f"File: {safe(path)}", f"Line: {line} of the retained notification (not a repository file)",
                          "", "Inspect that private file. If the match is a real credential, revoke or rotate it "
                          "and remove it from its original source. Do not rerun coding to recover this report."])
        except (OSError, UnicodeError):
            report_key = f"secret-storage:{digest(key)}"
            lines.extend(["Could not retain the rejected notification.", f"Directory: {safe(path.parent)}",
                          "Check free space, directory permissions, and retained-file encoding. No flagged text was sent."])
        self.outbox.enqueue(report_key, workflow_id, "\n".join(lines))

    def allowed(self, repository: dict):
        config = self.c.config
        if config.get("private_repositories_only", False):
            owner_id = repository.get("owner", {}).get("id")
            names = config.get("allow_repositories", [])
            ids = config.get("allow_repository_ids", [])
            return (repository.get("private") is True and type(owner_id) is int
                    and owner_id == config.get("organization_id", 97932676)
                    and (not names or repository.get("full_name", "").casefold() in {name.casefold() for name in names})
                    and (not ids or repository.get("id") in ids)
                    and (config.get("organization_wide", False) or bool(names)))
        return (repository["owner"]["id"] == config.get("organization_id", 97932676) and
                (config.get("organization_wide", False) or
                 repository["full_name"] in config.get("allow_repositories", [])))

    def scan(self):
        """Cursors advance only after every page succeeds; known work has priority."""
        from .github import GitHubError, IntegrationRemoved, RequestBudgetExceeded

        c, at = self.c, self.now()
        refreshed = set()
        repositories = list(self.github.repositories())
        accessible = {repo["id"]: repo for repo in repositories}

        def record(pr, repository):
            try:
                c.observe(pr, at)
                refreshed.add(pr["id"])
                return True
            except (KeyError, TypeError, ValueError, AttributeError):
                self.notice(f"invalid-pr:{repository['id']}:details", None,
                            f"PR maintenance: {repository['full_name']}\nhttps://github.com/{repository['full_name']}\n\n"
                            "Could not read a PR record.\n"
                            + github_failure_details(GitHubError("GitHub returned malformed PR data"), at)
                            + "\nImpact: This PR record was skipped; other valid records can still be checked.")
                return False

        def fetch(repository, number):
            try:
                return self.github.get_pr(repository["full_name"], number)
            except (IntegrationRemoved, RequestBudgetExceeded):
                raise
            except (GitHubError, KeyError, TypeError, ValueError) as error:
                if not isinstance(error, GitHubError):
                    error = GitHubError("GitHub returned malformed PR data")
                self.notice(f"pr-read:{repository['id']}:{number}:details", None,
                            f"PR maintenance: {repository['full_name']} #{number}\n"
                            f"https://github.com/{repository['full_name']}/pull/{number}\n\n"
                            "Could not refresh this PR from GitHub.\n"
                            + github_failure_details(error, at)
                            + "\nImpact: No new work or publication will be authorized for this PR without a fresh read.")
                return None
        # A successful complete inventory is needed before treating removal as authoritative.
        for row in list(c.db.execute("SELECT pr_id FROM workflows WHERE lifecycle_state NOT IN ('completed','cancelled','baselined_excluded')")):
            workflow = c.workflow(row[0])
            if not c.in_scope(workflow["pr"]) or not self.allowed(workflow["pr"]["base"]["repo"]):
                if workflow["lifecycle_state"] != "paused" or workflow["paused_reason"] != "workflow outside configured discovery scope":
                    c.control("pause", row[0], reason="workflow outside configured discovery scope")
                continue
            if workflow["repository_id"] not in accessible:
                if c.config.get("private_repositories_only", False):
                    c.control("pause", row[0], reason="repository absent from private inventory; inspect visibility and access")
                else:
                    c.control("cancel", row[0], reason="integration access removed or repository transferred")
                continue
            repository = accessible[workflow["repository_id"]]
            if not self.allowed(repository):
                c.control("pause", row[0], reason="repository outside configured discovery scope")
                continue
            live = fetch(repository, workflow["number"])
            if live is None or not record(live, repository):
                continue
            if workflow["lifecycle_state"] == "waiting_draft" and not live.get("draft") and live["state"] == "open":
                if not c.db.execute("SELECT 1 FROM ready_observations WHERE pr_id=?", (live["id"],)).fetchone():
                    self.notice(f"recovered-ready:{live['id']}", live["id"],
                                f"PR maintenance: {workflow['repository']} #{workflow['number']}\n\n"
                                "Draft readiness recovered by polling\n"
                                f"Observed: {datetime.fromtimestamp(at, timezone.utc):%Y-%m-%d %H:%M:%S UTC}\n\n"
                                "The 15-minute delay starts at this observation; the transition may have happened earlier.")
        for repository, number in c.pending_refreshes():
            repo = next((repo for repo in repositories if repo["full_name"] == repository and self.allowed(repo)), None)
            if repo is None:
                c.db.execute("DELETE FROM refreshes WHERE repository=? AND number=?", (repository, number))
                continue
            live = fetch(repo, number)
            if live is not None and record(live, repo):
                c.db.execute("DELETE FROM refreshes WHERE repository=? AND number=?", (repository, number))
        reset = c.meta("inventory_required") == "1"
        if reset:
            with transaction(c.db):
                c.db.execute("UPDATE inventories SET inventoried=0")
                c.db.execute("DELETE FROM meta WHERE key='inventory_required'")
        for repo in repositories:
            if not self.allowed(repo):
                continue
            c.db.execute("INSERT INTO inventories(repository_id,repository) VALUES (?,?) ON CONFLICT(repository_id) DO UPDATE SET repository=excluded.repository", (repo["id"], repo["full_name"]))
            inventory = c.db.execute("SELECT * FROM inventories WHERE repository_id=?", (repo["id"],)).fetchone()
            if inventory["inventoried"] and not c.meta("baseline_complete"):
                continue
            valid = True
            try:
                since = (inventory["cursor"] or float(c.meta("enabled_at"))) - c.config.get("scan_overlap", 600)
                items = self.github.updated_prs(repo["full_name"], since) if inventory["inventoried"] else self.github.open_prs(repo["full_name"])
                for pr in items:
                    valid = record(pr, repo) and valid
            except (IntegrationRemoved, RequestBudgetExceeded):
                raise
            except GitHubError as error:
                valid = False
                impact = ("Discovery of new or updated PRs is delayed; PRs refreshed separately can still proceed."
                          if c.meta("baseline_complete") else
                          "Initial PR inventory is incomplete; new maintenance work will not start until it finishes.")
                self.notice(f"scan:{repo['id']}:details", None,
                            f"PR maintenance: {repo['full_name']}\nhttps://github.com/{repo['full_name']}\n\n"
                            "Could not finish checking for new or updated PRs.\n"
                            + github_failure_details(error, at)
                            + f"\nImpact: {impact}\nThe next scan rechecks the unfinished period.")
            if valid:
                c.db.execute("UPDATE inventories SET inventoried=1,cursor=?,last_success=? WHERE repository_id=?", (at, at, repo["id"]))
        if all(c.db.execute("SELECT inventoried FROM inventories WHERE repository_id=?", (repo["id"],)).fetchone()[0]
               for repo in repositories if self.allowed(repo)):
            c.db.execute("INSERT OR IGNORE INTO meta VALUES ('baseline_complete',?)", (str(at),))
        return refreshed

    def reconcile(self):
        """Inspect worker-owned attempts before new authorization, including restored state."""
        c = self.c
        def hold(reason):
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('recovery_hold',?)", (reason,))
            self.notice("restore:invalid-worker-ledger", None,
                        "PR maintenance paused: worker ledger response is malformed or inconsistent. Back up both ledgers and reconcile them before dispatch.")
            return False

        try:
            response = self.worker.request("collect")
        except Blocked:
            return hold("worker rejected ledger collection or returned invalid JSON")
        if not isinstance(response, dict):
            return hold("worker ledger response is not an object")
        ledger_id = response.get("ledger_id")
        previous_ledger = c.meta("worker_ledger_id")
        if not isinstance(ledger_id, str) or not ledger_id or (previous_ledger and ledger_id != previous_ledger):
            return hold("worker ledger identity missing or changed; reconcile backups")
        if not previous_ledger:
            c.db.execute("INSERT INTO meta VALUES ('worker_ledger_id',?)", (ledger_id,))
        statuses = response.get("attempts")
        if isinstance(statuses, dict):
            statuses = list(statuses.values())
        if not isinstance(statuses, list) or any(
            not isinstance(status, dict) or not isinstance(status.get("attempt_id"), str)
            or not isinstance(status.get("state"), str) or not isinstance(status.get("publication_state"), str)
            or not isinstance(status.get("spec_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", status["spec_hash"])
            or type(status.get("quiescent")) is not bool for status in statuses
        ):
            return hold("worker ledger contains malformed attempt statuses")
        if len({status["attempt_id"] for status in statuses}) != len(statuses):
            return hold("worker ledger contains duplicate attempt identities")
        worker_ids = set()
        for status in statuses:
            attempt_id = status["attempt_id"]
            worker_ids.add(attempt_id)
            try:
                attempt = c.attempt(attempt_id)
            except Blocked:
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('recovery_hold',?)", ("worker has unknown attempts; reconcile ledgers and backups",))
                self.notice("restore:unknown-worker-attempt", None, "PR maintenance paused: worker ledger contains attempts missing from coordinator. Back up both ledgers and reconcile them before dispatch.")
                continue
            if status.get("spec_hash") and status["spec_hash"] != attempt["spec_hash"]:
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('recovery_hold','same ID, different immutable specification')")
                continue
            try:
                c.accept_status(attempt_id, status)
            except (Blocked, KeyError, TypeError, ValueError):
                return hold("worker attempt integrity validation failed")
            if attempt["spec"]["stage"] == "feedback":
                spool = c.feedback_path(attempt_id)
                if spool.exists():
                    spool.unlink()
        for attempt in c.db.execute("SELECT id FROM attempts WHERE last_contact IS NOT NULL"):
            if attempt[0] not in worker_ids:
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('recovery_hold','coordinator attempt absent from worker ledger; inspect restore')")
        return not c.meta("recovery_hold")

    def reports(self):
        for row in list(self.c.db.execute("SELECT pr_id FROM workflows")):
            workflow, stages = self.c.workflow(row[0]), self.c.stages(row[0])
            generation = digest([s["active_attempt_id"] for s in stages])[:12]
            unattempted_cancellation = workflow["lifecycle_state"] == "cancelled" and not workflow["has_attempts"]
            if all(s["state"] in TERMINAL for s in stages[:2]) and not unattempted_cancellation:
                initial_generation = digest([s["active_attempt_id"] for s in stages[:2]])[:12]
                self.summary(f"initial:{row[0]}:{initial_generation}", workflow, stages[:2], False)
            if all(s["state"] in TERMINAL for s in stages):
                self.summary(f"final:{row[0]}:{generation}", workflow, stages, True)
            if workflow["paused_reason"] and workflow["lifecycle_state"] == "paused":
                self.notice(f"paused:{row[0]}:reason:{digest(workflow['paused_reason'])}", row[0],
                            f"PR maintenance {workflow['repository']} #{workflow['number']} paused: {brief(workflow['paused_reason'])}\n{workflow['pr']['html_url']}" + execution_assurance(stages))
            for stage in stages:
                if stage["state"] in {"failed", "blocked", "published_with_concern"}:
                    result = stage["result"]
                    reason = result.get("error") or stage["state"]
                    publication = ("Changes were published; incident requires acknowledgement."
                                   if stage["state"] == "published_with_concern"
                                   else "Local attempt artifacts are retained; publication is not confirmed.")
                    self.notice(f"stage:{stage['active_attempt_id']}:{stage['state']}", row[0],
                                f"PR maintenance: {workflow['repository']} #{workflow['number']}\n"
                                f"{workflow['pr']['html_url']}\n\n"
                                f"{stage_title(stage['kind'])}: {state_title(stage['state'])}\n"
                                f"{publication}\n{brief(reason)}" + execution_assurance([stage]))

    def summary(self, key, workflow, stages, final):
        from .reporting import render_summary

        text = render_summary(workflow, stages, final) + execution_assurance(stages)
        self.notice(key, workflow["pr_id"], text)

    def tick(self):
        c, at = self.c, self.now()
        c.enable(at)
        c.db.execute("INSERT OR REPLACE INTO meta VALUES ('tick_started',?)", (str(at),))
        from .github import GitHubError, IntegrationRemoved, RequestBudgetExceeded

        try:
            refreshed = self.scan()
        except RequestBudgetExceeded:
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('api_deferred_at',?)", (str(at),))
            self.reports()
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_tick',?)", (str(at),))
            return
        except IntegrationRemoved:
            for row in list(c.db.execute("SELECT pr_id FROM workflows WHERE lifecycle_state NOT IN ('completed','cancelled','baselined_excluded')")):
                c.control("cancel", row[0], reason="GitHub integration unavailable or removed")
            self.notice("integration-removed", None, "PR maintenance paused: GitHub integration is unavailable. Future authorization is revoked; unreachable worker cancellation remains pending.")
            if self.worker:
                try:
                    for row in list(c.db.execute("SELECT id FROM attempts WHERE state NOT IN ('pushed','published_with_concern','noop','blocked','failed','cancelled')")):
                        attempt = c.attempt(row[0])
                        stage = next(s for s in c.stages(attempt["pr_id"]) if s["kind"] == attempt["stage"])
                        c.accept_status(row[0], self.worker.request("cancel", row[0], epoch=stage["authorization_epoch"]))
                except WorkerOffline:
                    pass
            self.reports()
            return
        except GitHubError as error:
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('api_deferred_at',?)", (str(at),))
            self.notice(f"inventory-deferred:{int(at // 3600)}", None,
                        "PR maintenance: repository discovery delayed\n\n"
                        "Could not get the repository list from GitHub.\n"
                        + github_failure_details(error, at)
                        + "\nImpact: No new maintenance work or publication was authorized during this poll. "
                        "Existing attempts were not cancelled.")
            self.reports()
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_tick',?)", (str(at),))
            return
        if not c.meta("baseline_complete"):
            self.reports()
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_tick',?)", (str(at),))
            return
        if not c.config.get("execution_enabled"):
            self.reports()
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_tick',?)", (str(at),))
            return
        if float(c.meta("worker_retry_at", "0")) > at:
            self.reports()
            c.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_tick',?)", (str(at),))
            return
        try:
            if not self.reconcile():
                return
            c.db.execute("DELETE FROM meta WHERE key IN ('worker_retry_at','worker_connect_failures')")
            for ack in c.db.execute("SELECT attempt_id,epoch FROM incident_acknowledgements WHERE delivered=0").fetchall():
                response = self.worker.request("cancel", ack["attempt_id"], epoch=ack["epoch"], acknowledge_incident=True)
                c.accept_status(ack["attempt_id"], response)
                if response.get("incident_acknowledged"):
                    c.db.execute("UPDATE incident_acknowledgements SET delivered=1 WHERE attempt_id=?", (ack["attempt_id"],))
            # Revoke/interruption requests are not successful cancellation acknowledgements.
            for row in list(c.db.execute("SELECT id FROM attempts WHERE state NOT IN ('pushed','published_with_concern','noop','blocked','failed','cancelled')")):
                attempt = c.attempt(row[0])
                workflow = c.workflow(attempt["pr_id"])
                stage = next(s for s in c.stages(attempt["pr_id"]) if s["kind"] == attempt["stage"])
                state = attempt["state"]
                if workflow["lifecycle_state"] in {"paused", "waiting_draft"}:
                    # Revocation is delivered even while paused. A retained candidate may resume
                    # with a fresh grant, so pause does not discard it or request a new prompt.
                    self.worker.request("cancel", row[0], epoch=stage["authorization_epoch"], pause=True)
                    continue
                if workflow["lifecycle_state"] == "cancel_requested":
                    c.accept_status(row[0], self.worker.request("cancel", row[0], epoch=stage["authorization_epoch"]))
                    continue
                started_at = attempt["result"].get("started_at")
                if state == "running" and started_at is not None and at - started_at > c.config.get("stage_deadline", 2700):
                    with transaction(c.db):
                        c._revoke(attempt["pr_id"], attempt["stage"])
                        c.db.execute("UPDATE workflows SET lifecycle_state='paused',paused_reason='deadline_exceeded; inspection required' WHERE pr_id=?", (attempt["pr_id"],))
                        c.db.execute("UPDATE stages SET state='deadline_exceeded' WHERE pr_id=? AND kind=?", (attempt["pr_id"], attempt["stage"]))
                    self.worker.request("cancel", row[0], epoch=stage["authorization_epoch"] + 1)
                    continue
                if state == "ready_to_publish" and attempt["pr_id"] in refreshed:
                    live = self.github.get_pr(workflow["repository"], workflow["number"])
                    c.observe(live, self.now())
                    grant = c.authorize(row[0], live, self.now())
                    if grant:
                        c.accept_status(row[0], self.worker.request("authorize-publish", row[0], authorization=grant))
            for row in c.db.execute("SELECT pr_id FROM stages WHERE kind='conflicts' AND state='blocked'").fetchall():
                workflow = c.workflow(row[0])
                stage = c.stages(row[0])[-1]
                if (workflow["lifecycle_state"] != "active" or row[0] not in refreshed
                        or stage["result"].get("retryable_reason") != "base_advanced"):
                    continue
                count = c.db.execute("SELECT COUNT(*) FROM attempts WHERE pr_id=? AND stage='conflicts'", (row[0],)).fetchone()[0]
                if count >= c.config.get("correction_rounds", 2):
                    continue
                live = self.github.get_pr(workflow["repository"], workflow["number"])
                if (live["head"]["sha"] != stage["spec"]["input_head_sha"]
                        or identity(live) != identity(stage["spec"]["pr"])):
                    continue
                c.observe(live, self.now())
                c.control("retry-stage", row[0], "conflicts", f"automatic base advancement recompute {count + 1}")
                self.notice(f"base-retry:{stage['active_attempt_id']}", row[0],
                            f"PR maintenance: {workflow['repository']} #{workflow['number']}\n"
                            f"{workflow['pr']['html_url']}\n\n"
                            "Recomputing the final merge because the base branch advanced. Previous local work is retained.")
            for row in list(c.db.execute("SELECT pr_id FROM workflows WHERE lifecycle_state='active' ORDER BY eligible_at,pr_id")):
                if row[0] not in refreshed:
                    continue
                workflow = c.workflow(row[0])
                # Reconciliation may have just discovered a push after the scan.
                # Pin the next attempt to that remote head, never the previous stage's input.
                if not any(s["active_attempt_id"] and s["state"] not in TERMINAL for s in c.stages(row[0])):
                    c.observe(self.github.get_pr(workflow["repository"], workflow["number"]), self.now())
                spec = c.claim(row[0], at)
                if not spec:
                    continue
                attempt = c.attempt(spec["attempt_id"])
                if attempt["state"] != "waiting_worker" or attempt["last_contact"] is not None:
                    continue
                if spec["stage"] == "feedback" and spec.get("feedback") is None:
                    snapshot = self.github.feedback(c.workflow(row[0])["repository"], spec["pr"]["number"])
                    check_secrets(canonical(snapshot))
                    spec = c.attach_feedback(spec["attempt_id"], snapshot)
                c.mark_submitted(spec["attempt_id"])
                if spec["stage"] == "feedback":
                    spool = c.feedback_path(spec["attempt_id"])
                    if not spool.exists():
                        raise Blocked("feedback spool missing before worker acceptance; reconcile retained snapshot")
                    feedback = json.loads(spool.read_text())
                    if digest(feedback) != spec["feedback"]["sha256"]:
                        raise Blocked("feedback snapshot hash mismatch; inspection required")
                    response = self.worker.submit(spec, feedback=feedback)
                    c.accept_status(spec["attempt_id"], response)
                    spool.unlink()
                else:
                    c.accept_status(spec["attempt_id"], self.worker.submit(spec))
        except WorkerOffline:
            failures = int(c.meta("worker_connect_failures", "0")) + 1
            retry_at = at + min(900, 15 * 2 ** min(failures - 1, 6))
            with transaction(c.db):
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('worker_connect_failures',?)", (str(failures),))
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('worker_retry_at',?)", (str(retry_at),))
            for row in c.db.execute("SELECT pr_id FROM workflows WHERE lifecycle_state='active'"):
                workflow = c.workflow(row[0])
                if workflow["eligible_at"] is not None and at - workflow["eligible_at"] >= c.config.get("delay_notice_after", 600):
                    self.notice(f"offline:{row[0]}", row[0], f"PR maintenance delayed for {workflow['repository']} #{workflow['number']}: contents-may-differ is unreachable. The durable attempt is retained; no replacement writer has been started.")
        self.reports()
        c.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_tick',?)", (str(at),))
