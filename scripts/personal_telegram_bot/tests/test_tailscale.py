from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import personal_telegram_bot.cli as cli
from personal_telegram_bot.config import Config
from personal_telegram_bot.formatters import format_tailscale_key_expiry
from personal_telegram_bot.providers.tailscale import (
    DeviceKeyExpiry,
    actionable_key_expiries,
    load_status,
    parse_status,
)

TZ = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 7, 26, 9, 0, tzinfo=TZ)


def _cfg(tmp_path):
    return Config(
        telegram_token="tok",
        default_chat_id=1,
        allowed_user_ids=frozenset(),
        notion_token=None,
        bread_datasource_id=None,
        tz=TZ,
        db_path=tmp_path / "state.sqlite3",
        health_units=[],
        health_urls=[],
        aw_data_dir=tmp_path,
        aw_max_age_hours=26.0,
        aw_systematic_after_hours=24.0,
        aw_stale_reminder_hours=12,
        life_db_path=tmp_path / "life.sqlite3",
        life_ingest_token=None,
        life_ingest_bind="127.0.0.1",
        life_ingest_port=8830,
        tailscale_key_expiry_warning_days=14,
    )


def test_parse_status_reads_self_and_peers_and_ignores_missing_expiry():
    devices = parse_status(
        {
            "BackendState": "Running",
            "Self": {
                "ID": "self-id",
                "HostName": "nervous-energy",
                "KeyExpiry": "2026-08-01T01:02:03Z",
            },
            "Peer": {
                "nodekey:one": {
                    "ID": "peer-id",
                    "DNSName": "contents-may-differ.example.ts.net.",
                    "KeyExpiry": "2026-07-25T14:49:55+00:00",
                    "Expired": True,
                },
                "nodekey:two": {"ID": "no-expiry", "HostName": "expiry-disabled"},
            },
        }
    )

    assert devices == [
        DeviceKeyExpiry(
            id="self-id",
            name="nervous-energy",
            expires_at=datetime(2026, 8, 1, 1, 2, 3, tzinfo=timezone.utc),
            expired=False,
        ),
        DeviceKeyExpiry(
            id="peer-id",
            name="contents-may-differ.example.ts.net",
            expires_at=datetime(2026, 7, 25, 14, 49, 55, tzinfo=timezone.utc),
            expired=True,
        ),
    ]


def test_parse_status_rejects_non_running_backend():
    with pytest.raises(RuntimeError, match="Stopped"):
        parse_status({"BackendState": "Stopped", "Self": {}, "Peer": {}})


def test_actionable_expiries_include_expired_and_warning_boundary():
    boundary = NOW.astimezone(timezone.utc) + timedelta(days=14)
    devices = [
        DeviceKeyExpiry("later", "later", boundary + timedelta(seconds=1), False),
        DeviceKeyExpiry("boundary", "boundary", boundary, False),
        DeviceKeyExpiry("expired", "expired", boundary + timedelta(days=30), True),
        DeviceKeyExpiry("past", "past timestamp", boundary - timedelta(days=20), False),
    ]

    result = actionable_key_expiries(devices, NOW, warning_days=14)

    assert [device.id for device in result] == ["expired", "past", "boundary"]


def test_load_status_reports_cli_and_json_failures_without_raw_output():
    def failed_runner(command, **kwargs):
        assert command == ["tailscale", "status", "--json"]
        assert kwargs["timeout"] == 20
        return SimpleNamespace(returncode=1, stdout="private status", stderr="socket unavailable")

    with pytest.raises(RuntimeError, match="socket unavailable") as exc_info:
        load_status(runner=failed_runner)
    assert "private status" not in str(exc_info.value)

    def malformed_runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not-json", stderr="")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        load_status(runner=malformed_runner)


def test_formatter_describes_expired_and_upcoming_keys():
    devices = [
        DeviceKeyExpiry("old", "contents-may-differ", NOW - timedelta(days=1), True),
        DeviceKeyExpiry("soon", "Elijah <phone>", NOW + timedelta(days=3), False),
    ]

    text = format_tailscale_key_expiry(devices, NOW)

    assert "contents-may-differ: expired 1 day ago" in text
    assert "Elijah <phone>: expires in 3 days" in text
    assert "tailscale up" in text


def test_formatter_requires_at_least_one_device():
    with pytest.raises(ValueError, match="at least one"):
        format_tailscale_key_expiry([], NOW)


def test_send_tailscale_keys_sends_once_per_day(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    devices = [DeviceKeyExpiry("peer", "contents-may-differ", NOW - timedelta(days=1), True)]
    sent = []
    monkeypatch.setattr(
        "personal_telegram_bot.providers.tailscale.load_status", lambda: devices
    )
    monkeypatch.setattr(cli, "_deliver", lambda cfg, text, dry_run, parse_mode=None: sent.append(text) or 9)
    args = Namespace(force=False, dry_run=False)

    assert cli.send_tailscale_keys(cfg, args, now=NOW) == 0
    assert cli.send_tailscale_keys(cfg, args, now=NOW) == 0

    assert len(sent) == 1


def test_send_tailscale_keys_force_bypasses_daily_dedupe(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    devices = [DeviceKeyExpiry("peer", "contents-may-differ", NOW - timedelta(days=1), True)]
    sent = []
    monkeypatch.setattr(
        "personal_telegram_bot.providers.tailscale.load_status", lambda: devices
    )
    monkeypatch.setattr(cli, "_deliver", lambda cfg, text, dry_run, parse_mode=None: sent.append(text) or 9)

    cli.send_tailscale_keys(cfg, Namespace(force=False, dry_run=False), now=NOW)
    cli.send_tailscale_keys(cfg, Namespace(force=True, dry_run=False), now=NOW)

    assert len(sent) == 2


def test_send_tailscale_keys_is_silent_when_no_key_is_near_expiry(tmp_path, monkeypatch):
    cfg = replace(_cfg(tmp_path), tailscale_key_expiry_warning_days=7)
    devices = [DeviceKeyExpiry("peer", "healthy", NOW + timedelta(days=8), False)]
    sent = []
    monkeypatch.setattr(
        "personal_telegram_bot.providers.tailscale.load_status", lambda: devices
    )
    monkeypatch.setattr(cli, "_deliver", lambda *args, **kwargs: sent.append(args[1]))

    assert cli.send_tailscale_keys(
        cfg, Namespace(force=False, dry_run=False), now=NOW
    ) == 0
    assert sent == []


def test_tailscale_dry_run_does_not_record_delivery(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    devices = [DeviceKeyExpiry("peer", "expiring", NOW + timedelta(days=2), False)]
    monkeypatch.setattr(
        "personal_telegram_bot.providers.tailscale.load_status", lambda: devices
    )
    args = Namespace(force=False, dry_run=True)

    assert cli.send_tailscale_keys(cfg, args, now=NOW) == 0
    assert "expiring" in capsys.readouterr().out
    assert cli.send_tailscale_keys(cfg, args, now=NOW) == 0
    assert "expiring" in capsys.readouterr().out
