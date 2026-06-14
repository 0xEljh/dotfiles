import json
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.cli import main
from personal_telegram_bot.life_events import LifeEventsDB, normalize_saa

TZ = ZoneInfo("Asia/Singapore")


def _seed_night(db_path):
    db = LifeEventsDB(db_path)
    db.insert(
        normalize_saa({"event": "sleep_tracking_started"}, datetime(2026, 6, 13, 23, 48, tzinfo=TZ))
    )
    db.insert(
        normalize_saa({"event": "sleep_tracking_stopped"}, datetime(2026, 6, 14, 7, 21, tzinfo=TZ))
    )
    db.close()


def test_sleep_summary_json_for_seeded_night(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "life.sqlite3"
    _seed_night(db_path)
    # sleep-summary must run WITHOUT a Telegram token (dotfiles-sync has none).
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("LIFE_DB", str(db_path))
    monkeypatch.setenv("TARGET_TZ", "Asia/Singapore")

    rc = main(["sleep-summary", "--date", "2026-06-14", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["date"] == "2026-06-14"
    assert payload["sleep"]["duration_text"] == "7h33m"
    assert payload["sleep"]["duration_hours"] == 7.55
    assert payload["sleeping_hours"] == [0, 1, 2, 3, 4, 5, 6]


def test_sleep_summary_json_when_no_data(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("LIFE_DB", str(tmp_path / "empty.sqlite3"))
    monkeypatch.setenv("TARGET_TZ", "Asia/Singapore")

    rc = main(["sleep-summary", "--date", "2026-06-14", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sleep"] is None
    assert payload["sleeping_hours"] == []
