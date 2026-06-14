from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, Iterator
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class T3Pairing:
    token: str
    pairing_url: str | None = None
    connection_string: str | None = None


class T3PairingLogParser:
    def __init__(self) -> None:
        self._connection_string: str | None = None
        self._token: str | None = None

    def feed(self, line: str) -> T3Pairing | None:
        line = line.strip()
        if line.startswith("Connection string:"):
            self._connection_string = line.removeprefix("Connection string:").strip() or None
            return None
        if line.startswith("Token:"):
            self._token = line.removeprefix("Token:").strip() or None
            return None
        if not line.startswith("Pairing URL:"):
            return None

        pairing_url = line.removeprefix("Pairing URL:").strip()
        token = self._token or _token_from_pairing_url(pairing_url)
        if not token:
            return None

        pairing = T3Pairing(
            token=token,
            pairing_url=pairing_url or None,
            connection_string=self._connection_string,
        )
        self._token = None
        return pairing


def _token_from_pairing_url(pairing_url: str) -> str | None:
    try:
        parsed = urlparse(pairing_url)
    except ValueError:
        return None
    fragment_token = parse_qs(parsed.fragment).get("token", [None])[0]
    query_token = parse_qs(parsed.query).get("token", [None])[0]
    return fragment_token or query_token


def parse_t3_pairings(lines: Iterable[str]) -> Iterator[T3Pairing]:
    parser = T3PairingLogParser()
    for line in lines:
        pairing = parser.feed(line)
        if pairing is not None:
            yield pairing


def pairing_dedupe_key(pairing: T3Pairing) -> str:
    return hashlib.sha256(pairing.token.encode("utf-8")).hexdigest()


def format_t3_pairing_message(pairing: T3Pairing, label: str = "nervous energy") -> str:
    lines = [f"T3 Code pairing key ({label})", f"Token: {pairing.token}"]
    if pairing.connection_string:
        lines.append(f"Connection: {pairing.connection_string}")
    if pairing.pairing_url:
        lines.append(f"Pairing URL: {pairing.pairing_url}")
    lines.append("Treat this like a password; it expires soon.")
    return "\n".join(lines)


def watch_t3_pairing_journal(unit: str, since: str = "now") -> Iterator[T3Pairing]:
    command = [
        "journalctl",
        "--user",
        "-u",
        unit,
        "--follow",
        "--output",
        "cat",
        "--since",
        since,
    ]
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    ) as proc:
        if proc.stdout is None:
            raise RuntimeError("journalctl stdout was not captured")
        yield from parse_t3_pairings(proc.stdout)
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"journalctl exited with status {code}")
