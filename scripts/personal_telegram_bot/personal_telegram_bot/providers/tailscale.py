from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass(frozen=True)
class DeviceKeyExpiry:
    id: str
    name: str
    expires_at: datetime | None
    expired: bool


def _parse_device(raw: dict) -> DeviceKeyExpiry | None:
    expires_raw = raw.get("KeyExpiry")
    expired = bool(raw.get("Expired", False))
    if not expires_raw and not expired:
        return None

    expires_at = None
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("tailscale status returned an invalid KeyExpiry") from exc
        if expires_at.tzinfo is None:
            raise RuntimeError("tailscale status returned a KeyExpiry without a timezone")

    device_id = str(raw.get("ID") or raw.get("PublicKey") or "unknown")
    name = str(raw.get("HostName") or raw.get("DNSName") or device_id).rstrip(".")
    return DeviceKeyExpiry(device_id, name, expires_at, expired)


def parse_status(payload: dict) -> list[DeviceKeyExpiry]:
    backend_state = payload.get("BackendState")
    if backend_state != "Running":
        raise RuntimeError(f"tailscaled is not running (state: {backend_state or 'unknown'})")

    self_status = payload.get("Self")
    peers = payload.get("Peer", {})
    if not isinstance(self_status, dict) or not isinstance(peers, dict):
        raise RuntimeError("tailscale status returned an unexpected JSON schema")

    devices = []
    for raw in [self_status, *peers.values()]:
        if not isinstance(raw, dict):
            raise RuntimeError("tailscale status returned an unexpected peer record")
        device = _parse_device(raw)
        if device is not None:
            devices.append(device)
    return devices


def load_status(
    runner: Callable = subprocess.run,
) -> list[DeviceKeyExpiry]:
    try:
        proc = runner(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not run tailscale status: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"tailscale status failed: {detail}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("tailscale status returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("tailscale status returned an unexpected JSON schema")
    return parse_status(payload)


def actionable_key_expiries(
    devices: list[DeviceKeyExpiry],
    now: datetime,
    warning_days: int,
) -> list[DeviceKeyExpiry]:
    now_utc = now.astimezone(timezone.utc)
    cutoff = now_utc + timedelta(days=warning_days)
    actionable = [
        device
        for device in devices
        if device.expired or (device.expires_at is not None and device.expires_at <= cutoff)
    ]

    def sort_key(device: DeviceKeyExpiry):
        expires_at = device.expires_at or datetime.min.replace(tzinfo=timezone.utc)
        state = 0 if device.expired else 1 if expires_at <= now_utc else 2
        return state, expires_at, device.name.lower()

    return sorted(actionable, key=sort_key)
