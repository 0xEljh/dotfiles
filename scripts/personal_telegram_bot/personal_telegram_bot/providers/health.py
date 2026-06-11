from __future__ import annotations

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

    @property
    def status(self) -> str:
        return "ok" if self.ok else "fail"


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


def run_all(units: Iterable[str], urls: Iterable[str]) -> list[CheckResult]:
    return [check_systemd_unit(u) for u in units] + [check_http(u) for u in urls]


def diff_transitions(
    previous: Mapping[str, str], results: Iterable[CheckResult]
) -> list[Transition]:
    """Notify on ok->fail, fail->ok, and unknown->fail. New healthy checks are silent."""
    transitions = []
    for result in results:
        old = previous.get(result.name)
        if result.status == old or (old is None and result.ok):
            continue
        transitions.append(
            Transition(name=result.name, old=old, new=result.status, detail=result.detail)
        )
    return transitions
