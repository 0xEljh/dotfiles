from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path


STAGES = ("deep_modules", "cleanup", "feedback", "conflicts")
TERMINAL = frozenset({"pushed", "published_with_concern", "noop", "blocked", "failed", "cancelled"})


class Blocked(RuntimeError):
    """An operator decision is needed. The retained work must not be discarded."""


class PreflightRejected(Blocked):
    """Successful read-only setup found ineligible work, not a failed tool call."""


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def database(path: str | Path, *, timeout: float = 30) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    os.chmod(path, 0o600)
    connection.row_factory = sqlite3.Row
    # Competing first connections can receive SQLITE_BUSY immediately while WAL
    # is enabled, even with sqlite3's busy timeout. Retry only that transition,
    # within the caller's existing connection budget.
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection.execute(f"PRAGMA busy_timeout={max(0, int((deadline - time.monotonic()) * 1000))}")
            connection.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            remaining = deadline - time.monotonic()
            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or remaining <= 0:
                connection.close()
                raise
            time.sleep(min(0.01, remaining))
    connection.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


@contextlib.contextmanager
def transaction(connection: sqlite3.Connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


SECRET_PATTERNS = (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b",
    r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b",
)


def first_secret(text: str) -> re.Match[str] | None:
    matches = [match for pattern in SECRET_PATTERNS if (match := re.search(pattern, text))]
    return min(matches, key=lambda match: match.start(), default=None)


def check_secrets(text: str) -> None:
    if first_secret(text):
        raise Blocked("possible secret detected; content withheld, inspect private artifacts")
