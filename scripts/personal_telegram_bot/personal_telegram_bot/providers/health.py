from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Iterable, Mapping

import httpx

HTTP_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    severity: str | None = None

    @property
    def status(self) -> str:
        return self.severity or ("ok" if self.ok else "fail")


@dataclass(frozen=True)
class Transition:
    name: str
    old: str | None
    new: str
    detail: str


def http_status_ok(status_code: int) -> bool:
    return status_code < 400


def check_systemd_unit(unit: str) -> CheckResult:
    proc = subprocess.run(
        ["systemctl", "is-active", unit],
        capture_output=True,
        text=True,
        timeout=30,
    )
    state = proc.stdout.strip() or proc.stderr.strip() or "unknown"
    return CheckResult(name=unit, ok=state == "active", detail=state)


def check_http(url: str) -> CheckResult:
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
        return CheckResult(
            name=url, ok=http_status_ok(resp.status_code), detail=f"HTTP {resp.status_code}"
        )
    except httpx.HTTPError as exc:
        return CheckResult(name=url, ok=False, detail=type(exc).__name__)


def check_disk(
    path: str, warning_percent: int = 80, critical_percent: int = 90
) -> CheckResult:
    try:
        stat = os.statvfs(path)
    except OSError as exc:
        return CheckResult(name=f"disk {path}", ok=False, detail=type(exc).__name__)

    block_size = stat.f_frsize
    used = (stat.f_blocks - stat.f_bfree) * block_size
    available = stat.f_bavail * block_size
    capacity = used + available
    used_percent = 100 * used / capacity if capacity else 100.0
    available_gib = available / (1024**3)
    detail = f"{used_percent:.1f}% used, {available_gib:.1f} GiB available"

    if used_percent > critical_percent:
        return CheckResult(f"disk {path}", False, detail, severity="critical")
    if used_percent > warning_percent:
        return CheckResult(f"disk {path}", False, detail, severity="warning")
    return CheckResult(f"disk {path}", True, detail)


def run_all(
    units: Iterable[str],
    urls: Iterable[str],
    disk_paths: Iterable[str] = (),
    disk_warning_percent: int = 80,
    disk_critical_percent: int = 90,
) -> list[CheckResult]:
    return (
        [check_systemd_unit(unit) for unit in units]
        + [check_http(url) for url in urls]
        + [
            check_disk(path, disk_warning_percent, disk_critical_percent)
            for path in disk_paths
        ]
    )


def diff_transitions(
    previous: Mapping[str, str], results: Iterable[CheckResult]
) -> list[Transition]:
    """Notify on state changes and new failures; new healthy checks are silent."""
    transitions = []
    for result in results:
        old = previous.get(result.name)
        if result.status == old or (old is None and result.ok):
            continue
        transitions.append(
            Transition(name=result.name, old=old, new=result.status, detail=result.detail)
        )
    return transitions
