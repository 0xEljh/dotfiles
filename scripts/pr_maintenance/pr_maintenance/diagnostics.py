from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path


MAX_TEXT_BYTES = 64 * 1024
MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS = 200
_FILENAME = re.compile(r"diagnostic-[0-9a-f]{32}\.json")
_TEXT_FIELDS = ("stdout", "stderr", "traceback", "exception")
_METADATA_FIELDS = ("operation", "attempt_id", "host", "executable", "exception_type", "command")


def _bounded_text(value: str | bytes, limit: int) -> tuple[str, bool]:
    if isinstance(value, bytes):
        text = value[:limit + 1].decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value[:limit + 1]
    else:
        raise TypeError("diagnostic text must be text or bytes")
    encoded = text.encode("utf-8", errors="replace")
    return encoded[:limit].decode("utf-8", errors="ignore"), len(value) > limit or len(encoded) > limit


def _payload(category: str, details: dict) -> bytes:
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "category": category}
    for field in _METADATA_FIELDS + _TEXT_FIELDS:
        if field not in details:
            continue
        if details[field] is None:
            record[field] = None
            record[field + "_truncated"] = False
            continue
        limit = MAX_TEXT_BYTES if field in _TEXT_FIELDS else 1024
        record[field], record[field + "_truncated"] = _bounded_text(details[field], limit)
    returncode = details.get("returncode")
    if returncode is not None and (type(returncode) is not int or not -2**31 <= returncode < 2**31):
        raise ValueError("invalid diagnostic return code")
    record["returncode"] = returncode
    while True:
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) <= MAX_RECORD_BYTES:
            return encoded
        # JSON escaping can expand otherwise bounded text. Shrink private text
        # further, marking it truncated, until the serialized record also fits.
        field = max(_TEXT_FIELDS, key=lambda key: len((record.get(key) or "").encode("utf-8")))
        size = len(record[field].encode("utf-8"))
        record[field], _ = _bounded_text(record[field], max(0, size - (len(encoded) - MAX_RECORD_BYTES)))
        record[field + "_truncated"] = True


def _log(category: str, path: Path | None) -> None:
    # Even a failing logging handler must not replace the original failure.
    try:
        logging.warning("private diagnostic category=%s path=%s", category,
                        json.dumps(str(path)) if path is not None else "not-retained")
    except Exception:
        pass


def _prune(directory: int) -> None:
    candidates = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not _FILENAME.fullmatch(entry.name):
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                candidates.append((info.st_mtime_ns, entry.name))
    for _, name in sorted(candidates)[:-MAX_RECORDS]:
        os.unlink(name, dir_fd=directory)


def retain_diagnostic(database_path: str | Path, category: str, **details) -> Path | None:
    """Retain private failure evidence, or return None without exposing its text.

    Accepted details are operation, attempt_id, host, executable, command,
    exception_type, returncode, stdout, stderr, traceback and exception. Other
    fields (including requests, environments and response objects) are ignored.
    Text is bounded to 64 KiB of UTF-8 per private field, metadata to 1 KiB,
    and serialized JSON to 256 KiB; truncation flags describe any reduction.
    Keep the newest 200 generated owner-owned regular files by modification time.
    The existing database parent must exist, with no symlinks in its path.
    """
    safe_category = "invalid-category"
    try:
        if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", category):
            raise ValueError("invalid diagnostic category")
        safe_category = category
        payload = _payload(category, details)
        parent = Path(os.path.abspath(database_path)).parent
        path = parent / "diagnostics" / f"diagnostic-{uuid.uuid4().hex}.json"
        with contextlib.ExitStack() as stack:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory = os.open(parent.anchor, flags)
            stack.callback(os.close, directory)
            for part in parent.parts[1:]:
                directory = os.open(part, flags, dir_fd=directory)
                stack.callback(os.close, directory)
            try:
                os.mkdir("diagnostics", mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open("diagnostics", flags, dir_fd=directory)
            stack.callback(os.close, directory)
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise PermissionError("diagnostics directory must be owner-only")
            # Coordinate writers and pruning without another artifact or a lock
            # that the coordinator's unrelated operator operations must acquire.
            fcntl.flock(directory, fcntl.LOCK_EX)
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                _prune(directory)
                os.fsync(directory)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(path.name, dir_fd=directory)
                raise
        _log(category, path)
        return path
    except Exception:
        _log(safe_category, None)
        return None
