from types import SimpleNamespace
from zoneinfo import ZoneInfo

import personal_telegram_bot.cli as cli
from personal_telegram_bot.config import Config
from personal_telegram_bot.db import StateDB
from personal_telegram_bot.providers import health
from personal_telegram_bot.providers.health import CheckResult


def config(tmp_path):
    return Config(
        telegram_token="tok",
        default_chat_id=1,
        allowed_user_ids=frozenset(),
        notion_token=None,
        bread_datasource_id=None,
        tz=ZoneInfo("Asia/Singapore"),
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
    )


def args(*, force=False, dry_run=False):
    return SimpleNamespace(host="contents-may-differ", force=force, dry_run=dry_run)


def test_new_healthy_disk_is_recorded_without_alert(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    sent = []
    monkeypatch.setattr(
        health,
        "check_disk",
        lambda *_args: CheckResult("disk /", True, "71.0% used"),
    )
    monkeypatch.setattr(cli, "_deliver", lambda *_args, **_kwargs: sent.append(_args[1]))

    assert cli.send_disk_health(cfg, args()) == 0

    assert sent == []
    assert StateDB(cfg.db_path).get_health_row("disk /")["status"] == "ok"


def test_disk_warning_alerts_once_with_host_label(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    sent = []
    monkeypatch.setattr(
        health,
        "check_disk",
        lambda *_args: CheckResult(
            "disk /", False, "81.0% used", severity="warning"
        ),
    )
    monkeypatch.setattr(cli, "_deliver", lambda *_args, **_kwargs: sent.append(_args[1]))

    assert cli.send_disk_health(cfg, args()) == 0
    assert cli.send_disk_health(cfg, args()) == 0

    assert len(sent) == 1
    assert "contents-may-differ health" in sent[0]
    assert "warning" in sent[0]
    assert StateDB(cfg.db_path).last_sent("disk-health-alert") is not None


def test_disk_warning_escalates_to_critical(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    results = iter(
        [
            CheckResult("disk /", False, "81.0% used", severity="warning"),
            CheckResult("disk /", False, "91.0% used", severity="critical"),
        ]
    )
    sent = []
    monkeypatch.setattr(health, "check_disk", lambda *_args: next(results))
    monkeypatch.setattr(cli, "_deliver", lambda *_args, **_kwargs: sent.append(_args[1]))

    cli.send_disk_health(cfg, args())
    cli.send_disk_health(cfg, args())

    assert len(sent) == 2
    assert "critical" in sent[1]


def test_forced_disk_summary_is_not_recorded_as_transition_alert(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    sent = []
    monkeypatch.setattr(
        health,
        "check_disk",
        lambda *_args: CheckResult("disk /", True, "71.0% used"),
    )
    monkeypatch.setattr(cli, "_deliver", lambda *_args, **_kwargs: sent.append(_args[1]))

    cli.send_disk_health(cfg, args(force=True))

    assert sent == ["contents-may-differ status\n✅ disk /: 71.0% used"]
    assert StateDB(cfg.db_path).last_sent("disk-health-alert") is None


def test_disk_dry_run_does_not_persist_state(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(
        health,
        "check_disk",
        lambda *_args: CheckResult(
            "disk /", False, "81.0% used", severity="warning"
        ),
    )
    monkeypatch.setattr(cli, "_deliver", lambda *_args, **_kwargs: None)

    cli.send_disk_health(cfg, args(dry_run=True))

    assert StateDB(cfg.db_path).get_health_row("disk /") is None
