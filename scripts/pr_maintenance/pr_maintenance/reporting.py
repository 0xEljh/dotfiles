from __future__ import annotations

import contextlib
import json
import math
import sqlite3
import subprocess
import textwrap
import time
from collections.abc import Callable, Sequence

from .common import check_secrets, first_secret


PART_LIMIT = 3499


class TransientDeliveryError(RuntimeError):
    pass


class PermanentDeliveryError(RuntimeError):
    pass


class RateLimited(TransientDeliveryError):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__("report delivery rate limited")


def _valid_id(value: object) -> bool:
    return type(value) is int and value > 0


class BotSender:
    """One-shot botctl adapter; inherits the bot environment without reading it.

    Construction checks the destination without sending. The bot command checks
    it again on every send. No message, response body, or process stderr is
    included in exceptions.
    """

    def __init__(self, argv: Sequence[str], *, timeout: float = 75):
        if isinstance(argv, str) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise PermanentDeliveryError("report sender command is not configured")
        self.argv = tuple(argv)
        self.timeout = timeout
        self.validate()

    def _request(self, text: str, *, validate: bool = False) -> dict:
        command = [*self.argv, *(["--validate-only"] if validate else [])]
        try:
            process = subprocess.run(
                command, input=text, capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise TransientDeliveryError("report delivery timed out; acceptance unknown") from None
        except OSError:
            raise PermanentDeliveryError("report sender is unavailable") from None
        try:
            data = json.loads(process.stdout)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise TransientDeliveryError("report sender response is unconfirmed") from None
        error = data.get("error")
        if error == "rate_limited":
            retry_after = data.get("retry_after")
            if type(retry_after) in (int, float) and math.isfinite(retry_after) and retry_after > 0:
                raise RateLimited(retry_after)
            raise TransientDeliveryError("report delivery rate limited; delay unconfirmed")
        if error == "configuration":
            raise PermanentDeliveryError("report destination or configuration rejected")
        if error or process.returncode != 0:
            raise TransientDeliveryError("report delivery failed; acceptance unknown")
        return data

    def validate(self) -> None:
        if self._request("", validate=True).get("destination_valid") is not True:
            raise PermanentDeliveryError("private report destination is unconfirmed")

    def __call__(self, text: str) -> int:
        check_secrets(text)
        if not text or len(text) > 3500:
            raise PermanentDeliveryError("report message size rejected")
        message_id = self._request(text).get("message_id")
        if not _valid_id(message_id):
            raise TransientDeliveryError("report delivery ID is unconfirmed")
        return message_id


@contextlib.contextmanager
def _atomic(connection: sqlite3.Connection):
    # Enqueue may participate in a coordinator transaction without committing it.
    connection.execute("SAVEPOINT reporting")
    try:
        yield
        connection.execute("RELEASE SAVEPOINT reporting")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT reporting")
        connection.execute("RELEASE SAVEPOINT reporting")
        raise


def brief(value: object, limit: int = 240) -> str:
    text = str(value)
    # Preserve a complete match for the existing withholding/retention path.
    if first_secret(text):
        return text
    return textwrap.shorten(text, width=limit, placeholder="…")


def _split(text: str) -> list[str]:
    if len(text) <= PART_LIMIT:
        return [text]
    label = brief(text.splitlines()[0], 100)
    total = 1
    while True:
        bodies, start = [], 0
        while start < len(text):
            capacity = PART_LIMIT - len(f"\n\n{label} · Part {len(bodies) + 1}/{total}")
            end = min(len(text), start + capacity)
            if end < len(text):
                for boundary in ("\n\n", "\n"):
                    split_at = text.rfind(boundary, start, end)
                    if split_at != -1:
                        end = split_at + len(boundary)
                        break
            bodies.append(text[start:end])
            start = end
        # Only the total's digit count affects capacity. Re-split when it grows.
        if len(str(len(bodies))) <= len(str(total)):
            return [f"{body}\n\n{label} · Part {index}/{len(bodies)}"
                    for index, body in enumerate(bodies, 1)]
        total = len(bodies)


class Outbox:
    """Durable labeled delivery for a single coordinator, not exactly-once sends.

    Pass a private SQLite connection. Enqueue can join the caller's transaction;
    deliver must run outside it so attempts are durable before external effects.
    A crash or timeout can repeat the same persisted part, never rerun coding.
    """

    def __init__(self, connection: sqlite3.Connection, now: Callable[[], float] = time.time):
        self.connection = connection
        self.now = now
        with _atomic(connection):
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY,
                    report_key TEXT NOT NULL UNIQUE,
                    workflow_id INTEGER,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS report_parts (
                    report_id INTEGER NOT NULL REFERENCES reports(id),
                    part INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL,
                    last_error TEXT,
                    message_id INTEGER,
                    sent_at REAL,
                    PRIMARY KEY (report_id, part)
                )
            """)

    def enqueue(self, report_key: str, workflow_id: int | None, text: str) -> bool:
        """Return whether a new immutable report was recorded; scan before storage."""
        check_secrets(report_key)
        check_secrets(text)
        if not report_key or "\n" in report_key or "\r" in report_key or not text:
            raise ValueError("a nonempty report and single-line report key are required")
        parts = _split(text)
        with _atomic(self.connection):
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO reports (report_key, workflow_id, text, created_at) VALUES (?, ?, ?, ?)",
                (report_key, workflow_id, text, self.now()),
            )
            if not cursor.rowcount:
                return False
            self.connection.executemany(
                "INSERT INTO report_parts (report_id, part, text, next_attempt) VALUES (?, ?, ?, ?)",
                [(cursor.lastrowid, part, content, self.now()) for part, content in enumerate(parts, 1)],
            )
        return True

    def deliver(self, sender: Callable[[str], int]) -> int:
        """Attempt due parts in order, returning confirmed successful part count.

        Configuration failures remain pending with a one-hour retry. Rate limits
        defer all pending parts. Unknown exceptions/timeouts retry with backoff;
        exception text is deliberately not persisted.
        """
        if self.connection.in_transaction:
            raise RuntimeError("report delivery requires committed coordinator state")
        delivered = 0
        rows = self.connection.execute("""
            SELECT p.report_id, p.part, p.text, p.attempts, r.text, r.report_key
            FROM report_parts p JOIN reports r ON r.id = p.report_id
            WHERE p.message_id IS NULL AND p.next_attempt <= ?
            ORDER BY r.id, p.part
        """, (self.now(),)).fetchall()
        for report_id, part, text, attempts, full_text, key in rows:
            check_secrets(key)
            check_secrets(full_text)
            check_secrets(text)
            if len(text) > PART_LIMIT:
                raise PermanentDeliveryError("stored report part exceeds delivery limit")
            now = self.now()
            backoff = min(3600, 30 * (2 ** min(attempts, 7)))
            with _atomic(self.connection):
                claim = self.connection.execute("""
                    UPDATE report_parts SET attempts = attempts + 1, next_attempt = ?,
                        last_error = 'delivery outcome unconfirmed'
                    WHERE report_id = ? AND part = ? AND attempts = ?
                        AND message_id IS NULL AND next_attempt <= ?
                        AND NOT EXISTS (
                            SELECT 1 FROM report_parts earlier WHERE earlier.report_id = ?
                                AND earlier.part < ? AND earlier.message_id IS NULL
                        )
                """, (now + backoff, report_id, part, attempts, now, report_id, part))
            if not claim.rowcount:
                continue
            stop = False
            try:
                message_id = sender(text)
                if not _valid_id(message_id):
                    raise TransientDeliveryError("delivery ID unconfirmed")
            except RateLimited as error:
                delay = error.retry_after
                if type(delay) not in (int, float) or not math.isfinite(delay) or delay <= 0:
                    delay = backoff
                retry_at = self.now() + max(backoff, delay)
                error_text = "delivery rate limited"
                with _atomic(self.connection):
                    self.connection.execute("""
                        UPDATE report_parts SET next_attempt = MAX(next_attempt, ?)
                        WHERE message_id IS NULL
                    """, (retry_at,))
                stop = True
            except PermanentDeliveryError:
                retry_at = self.now() + 3600
                error_text = "delivery configuration rejected; report retained"
                stop = True
            except Exception:
                retry_at = self.now() + backoff
                error_text = "transient delivery failure; acceptance unknown"
            else:
                with _atomic(self.connection):
                    self.connection.execute("""
                        UPDATE report_parts SET message_id = ?, sent_at = ?, last_error = NULL
                        WHERE report_id = ? AND part = ?
                    """, (message_id, self.now(), report_id, part))
                delivered += 1
                continue
            with _atomic(self.connection):
                self.connection.execute("""
                    UPDATE report_parts SET next_attempt = ?, last_error = ?
                    WHERE report_id = ? AND part = ?
                """, (retry_at, error_text, report_id, part))
            if stop:
                break
        return delivered

    def status(self) -> dict:
        pending_reports, oldest = self.connection.execute("""
            SELECT COUNT(*), MIN(created_at) FROM reports r WHERE EXISTS (
                SELECT 1 FROM report_parts p WHERE p.report_id = r.id AND p.message_id IS NULL
            )
        """).fetchone()
        reports = self.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        pending, sent, attempts, errors, next_attempt = self.connection.execute("""
            SELECT COALESCE(SUM(message_id IS NULL), 0), COALESCE(SUM(message_id IS NOT NULL), 0),
                COALESCE(SUM(attempts), 0), COALESCE(SUM(last_error IS NOT NULL), 0),
                MIN(CASE WHEN message_id IS NULL THEN next_attempt END) FROM report_parts
        """).fetchone()
        return {
            "pending_reports": pending_reports, "sent_reports": reports - pending_reports,
            "pending_parts": pending, "sent_parts": sent, "attempts": attempts, "errors": errors,
            "oldest_pending_age": max(0, self.now() - oldest) if oldest is not None else None,
            "next_attempt": next_attempt,
        }


def _mapping(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list:
    return value if isinstance(value, list) else [value] if value else []


def stage_title(kind: str) -> str:
    return {
        "deep_modules": "Deep modules", "cleanup": "Cleanup",
        "feedback": "Review feedback", "conflicts": "Merge conflicts",
    }.get(kind, kind.replace("_", " ").capitalize())


def state_title(state: str) -> str:
    return {"noop": "No changes"}.get(state, state.replace("_", " ").capitalize())


def _verification_summary(value: object) -> str:
    trusted = _mapping(value)
    evidence = _items(trusted.get("commands")) if "commands" in trusted else _items(value)
    if not evidence:
        return "Verification: not recorded."
    passed = failed = unknown = 0
    notes = []
    for check in evidence:
        record = _mapping(check)
        code = record.get("returncode")
        if type(code) is int:
            passed += code == 0
            failed += code != 0
        else:
            unknown += 1
            note = (record.get("outcome") or record.get("result") or record.get("evidence")) if record else check
            if note:
                notes.append(brief(note, 100))
    counts = [f"{count} {label}" for count, label in
              ((passed, "passed"), (failed, "failed"), (unknown, "unconfirmed")) if count]
    detail = f" (reported: {notes[0]})" if notes else ""
    return "Verification: " + "; ".join(counts) + detail + "."


def render_summary(workflow: dict, stages: list[dict], final: bool) -> str:
    """Render a notification, not the private diagnostic record.

    This formatter does not scan unused inputs. Pass its output through the notice
    or outbox gate before delivery. Stage results can be decoded or JSON serialized.
    Missing verification is explicit, not manufactured as success.
    """
    repository = workflow.get("repository") or workflow.get("repository_full_name") or "unknown repository"
    number = workflow.get("number") or workflow.get("pr_number") or "?"
    url = (workflow.get("url") or workflow.get("html_url") or _mapping(workflow.get("pr")).get("html_url")
           or f"https://github.com/{repository}/pull/{number}")
    lines = [f"{'Final' if final else 'Initial'} maintenance summary: {repository} #{number}", str(url)]
    if workflow.get("lifecycle_state") in {"cancelled", "cancel_requested"}:
        lines.extend(["", brief(workflow.get("paused_reason") or "Maintenance cancelled")])
        if final and not workflow.get("has_attempts", any(s.get("active_attempt_id") or _mapping(s.get("result")) for s in stages)):
            lines.append("Maintenance never started. No maintenance checks ran and no changes were published.")
        blocker = _mapping(workflow.get("last_queue_blocker"))
        if blocker:
            lines.append(f"Previously waiting for {stage_title(blocker['stage'])}: "
                         f"https://github.com/{blocker['repository']}/pull/{blocker['number']}")
    for stage in stages:
        result = _mapping(stage.get("result"))
        result = {**result, **_mapping(result.get("result"))}
        summary = _mapping(result.get("summary"))
        data = {**stage, **summary, **result}
        kind = stage.get("kind", "unknown stage")
        state = stage.get("state", data.get("state", "unknown"))
        if state == "cancelled" and not stage.get("active_attempt_id") and not result and not stage.get("spec"):
            lines.extend(["", f"{stage_title(kind)}: Not started (cancelled)"])
            continue
        lines.extend(["", f"{stage_title(kind)}: {state_title(state)}"])
        if state == "published_with_concern":
            lines.append("Changes were published; incident requires acknowledgement.")
        for decision in _items(data.get("unresolved_decisions")):
            lines.append(f"Decision needed: {brief(decision)}")
        reason = data.get("reason") or data.get("error") or summary.get("summary")
        if not reason and isinstance(data.get("summary"), str):
            reason = data["summary"]
        if reason:
            lines.append(brief(reason))
        commits = _items(data.get("commits"))
        candidate = data.get("candidate_sha")
        if candidate and candidate not in commits:
            commits.append(candidate)
        published = state in ("pushed", "published_with_concern")
        if candidate or commits:
            if not published:
                lines.append("Local changes retained; not confirmed pushed.")
        elif state in ("blocked", "failed", "publication_unknown"):
            lines.append("Publication not confirmed.")
        if published:
            for commit in commits:
                record = _mapping(commit)
                sha = record.get("sha") if record else str(commit)
                link = record.get("url") or f"https://github.com/{repository}/commit/{sha}"
                lines.append(f"Commit: {link}")
        lines.append(_verification_summary(data.get("verification")))
        for finding in _items(data.get("feedback_dispositions") or data.get("dispositions")):
            if isinstance(finding, dict):
                lines.append(f"Feedback {finding.get('id', '?')}: {finding.get('disposition', 'needs decision')} "
                             f"— {brief(finding.get('reason', 'reason not recorded'), 120)}")
                if finding.get("url"):
                    lines.append(str(finding["url"]))
    return "\n".join(lines)
