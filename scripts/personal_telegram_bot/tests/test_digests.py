from datetime import datetime
from zoneinfo import ZoneInfo

import personal_telegram_bot.digests as digests
from personal_telegram_bot.config import Config
from personal_telegram_bot.life_events import LifeEventsDB, normalize_saa
from personal_telegram_bot.providers import notion_todos

TZ = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 6, 14, 9, 30, tzinfo=TZ)


def _cfg(tmp_path):
    return Config(
        telegram_token="tok",
        default_chat_id=1,
        allowed_user_ids=frozenset(),
        notion_token="ntn",
        bread_datasource_id="ds",
        tz=TZ,
        db_path=tmp_path / "state.sqlite3",
        health_units=[],
        health_urls=[],
        aw_data_dir=tmp_path,
        aw_max_age_hours=26.0,
        life_db_path=tmp_path / "life.sqlite3",
        life_ingest_token=None,
        life_ingest_bind="127.0.0.1",
        life_ingest_port=8830,
    )


def _capture_sends(monkeypatch):
    sent = []
    monkeypatch.setattr(
        digests,
        "send_message",
        lambda token, chat, text, parse_mode=None: sent.append(text) or 99,
    )
    return sent


def test_delivers_once_then_dedupes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(notion_todos, "fetch_due_tasks", lambda *a, **k: ([], []))

    first = digests.deliver_morning_digest(cfg, trigger="wake", now=NOW)
    second = digests.deliver_morning_digest(cfg, trigger="timer", now=NOW)

    assert first is True
    assert second is False
    assert len(sent) == 1


def test_includes_sleep_line_when_available(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    life = LifeEventsDB(cfg.life_db_path)
    life.insert(
        normalize_saa({"event": "sleep_tracking_started"}, datetime(2026, 6, 13, 23, 48, tzinfo=TZ))
    )
    life.insert(
        normalize_saa({"event": "sleep_tracking_stopped"}, datetime(2026, 6, 14, 7, 21, tzinfo=TZ))
    )
    life.close()
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(notion_todos, "fetch_due_tasks", lambda *a, **k: ([], []))

    digests.deliver_morning_digest(cfg, trigger="wake", now=NOW)

    assert len(sent) == 1
    assert "😴 7h33m" in sent[0]


def test_digest_sends_even_without_sleep_data(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(notion_todos, "fetch_due_tasks", lambda *a, **k: ([], []))

    assert digests.deliver_morning_digest(cfg, trigger="timer", now=NOW) is True
    assert "😴" not in sent[0]


def test_dry_run_does_not_record_or_send(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(notion_todos, "fetch_due_tasks", lambda *a, **k: ([], []))

    digests.deliver_morning_digest(cfg, trigger="timer", dry_run=True, now=NOW)
    # Not recorded, so a real send still goes through afterwards.
    result = digests.deliver_morning_digest(cfg, trigger="timer", now=NOW)

    assert result is True
    assert len(sent) == 1
