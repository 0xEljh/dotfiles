from datetime import datetime

from personal_telegram_bot.cli import FAILURE_ALERT_WINDOW_HOURS, failure_window_key


def test_same_window_same_key():
    a = failure_window_key("dotfiles-sync.service", datetime(2026, 6, 11, 13, 0))
    b = failure_window_key("dotfiles-sync.service", datetime(2026, 6, 11, 13, 40))
    assert a == b


def test_next_window_differs():
    a = failure_window_key("dotfiles-sync.service", datetime(2026, 6, 11, 13, 0))
    b = failure_window_key(
        "dotfiles-sync.service",
        datetime(2026, 6, 11, 13 + FAILURE_ALERT_WINDOW_HOURS, 0),
    )
    assert a != b


def test_key_scoped_per_unit():
    now = datetime(2026, 6, 11, 13, 0)
    assert failure_window_key("a.service", now) != failure_window_key("b.service", now)


def test_day_boundary_differs():
    a = failure_window_key("a.service", datetime(2026, 6, 11, 1, 0))
    b = failure_window_key("a.service", datetime(2026, 6, 12, 1, 0))
    assert a != b


def test_failure_preview_uses_local_host_without_fetching_logs(tmp_path, monkeypatch, capsys):
    import subprocess
    from dataclasses import replace
    from unittest.mock import Mock

    from personal_telegram_bot import cli
    from personal_telegram_bot.config import Config

    cfg = replace(Config.from_env({
        "TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_DEFAULT_CHAT_ID": "1",
    }), db_path=tmp_path / "state.sqlite3")
    monkeypatch.setattr(Config, "from_env", lambda: cfg)
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "test-host")

    no_journal = Mock(side_effect=AssertionError("a notification must not read private logs"))
    monkeypatch.setattr(subprocess, "run", no_journal)
    assert cli.main(["send", "failure", "--unit", "example.service", "--dry-run"]) == 0
    no_journal.assert_not_called()
    assert capsys.readouterr().out == (
        "example.service failed on test-host\n\n"
        "Logs:\njournalctl -u example.service -n 50 --no-pager\n"
    )
