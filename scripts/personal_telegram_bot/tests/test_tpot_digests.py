from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import personal_telegram_bot.digests as digests
from personal_telegram_bot.config import Config
from personal_telegram_bot.db import StateDB
from personal_telegram_bot.life_events import LifeEvent, LifeEventsDB
from personal_telegram_bot.providers import notion_todos
from personal_telegram_bot.tpot.seeds import SeedStore

TZ = ZoneInfo("Asia/Singapore")


def _cfg(tmp_path) -> Config:
    return Config(
        telegram_token="tok",
        default_chat_id=1,
        allowed_user_ids=frozenset({123}),
        notion_token="ntn",
        bread_datasource_id="ds",
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
        time_accounting_url="https://ta.example/db",
    )


def _home_event(when: datetime) -> LifeEvent:
    return LifeEvent(
        source="owntracks",
        event_type="place_present",
        observed_at=when,
        state="Home",
        payload={"value1": "Home"},
    )


def test_standdown_includes_post_seeds_and_marks_surfaced_after_send(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    life = LifeEventsDB(cfg.life_db_path)
    life.insert(_home_event(datetime(2026, 6, 28, 21, 40, tzinfo=TZ)))
    life.close()
    store = SeedStore(StateDB(cfg.db_path))
    seed_id = store.add_seed(
        seed_date=date(2026, 6, 28),
        topic="working on tpot",
        source="waka:tpot",
        provenance="3.2h on tpot",
        text="Use <angle> & ampersands",
        score=1.25,
        model_versions={},
    )
    sent = []

    def fake_send(token, chat, text, parse_mode=None, reply_markup=None):
        sent.append({"text": text, "reply_markup": reply_markup})
        return 77

    monkeypatch.setattr(digests, "send_message", fake_send)

    assert digests.deliver_evening_standdown(
        cfg, trigger="timer", now=datetime(2026, 6, 28, 22, 0, tzinfo=TZ)
    )

    assert "Post seeds" in sent[0]["text"]
    assert "Use &lt;angle&gt; &amp; ampersands" in sent[0]["text"]
    assert sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"tpot:used:{seed_id}"
    row = SeedStore(StateDB(cfg.db_path)).get_seed(seed_id)
    assert row.status == "surfaced"
    assert SeedStore(StateDB(cfg.db_path)).events_for_seed(seed_id)[0].detail == {"message_id": 77}


def test_standdown_dry_run_does_not_mark_seeds_surfaced(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    store = SeedStore(StateDB(cfg.db_path))
    seed_id = store.add_seed(
        seed_date=date(2026, 6, 28),
        topic="working on tpot",
        source="waka:tpot",
        provenance="3.2h on tpot",
        text="draft",
        score=1.25,
        model_versions={},
    )

    assert digests.deliver_evening_standdown(
        cfg,
        trigger="manual",
        force=True,
        dry_run=True,
        now=datetime(2026, 6, 28, 22, 0, tzinfo=TZ),
    )

    assert "Post seeds" in capsys.readouterr().out
    assert SeedStore(StateDB(cfg.db_path)).get_seed(seed_id).status == "proposed"


def test_morning_carries_over_yesterdays_unactioned_seed_and_marks_surfaced(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store = SeedStore(StateDB(cfg.db_path))
    seed_id = store.add_seed(
        seed_date=date(2026, 6, 27),
        topic="working on tpot",
        source="waka:tpot",
        provenance="3.2h on tpot",
        text="morning seed",
        score=0.8,
        model_versions={},
    )
    sent = []
    monkeypatch.setattr(notion_todos, "fetch_due_tasks", lambda *a, **k: ([], []))
    monkeypatch.setattr(
        digests,
        "send_message",
        lambda token, chat, text, parse_mode=None, reply_markup=None: sent.append(text) or 88,
    )

    assert digests.deliver_morning_digest(
        cfg, trigger="timer", now=datetime(2026, 6, 28, 9, 30, tzinfo=TZ)
    )

    assert "Still on the table" in sent[0]
    assert "morning seed" in sent[0]
    assert SeedStore(StateDB(cfg.db_path)).get_seed(seed_id).status == "surfaced"


def test_morning_dry_run_does_not_mark_carryover_seed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store = SeedStore(StateDB(cfg.db_path))
    seed_id = store.add_seed(
        seed_date=date(2026, 6, 27),
        topic="working on tpot",
        source="waka:tpot",
        provenance="3.2h on tpot",
        text="morning seed",
        score=0.8,
        model_versions={},
    )
    monkeypatch.setattr(notion_todos, "fetch_due_tasks", lambda *a, **k: ([], []))

    assert digests.deliver_morning_digest(
        cfg,
        trigger="timer",
        dry_run=True,
        now=datetime(2026, 6, 28, 9, 30, tzinfo=TZ),
    )

    assert SeedStore(StateDB(cfg.db_path)).get_seed(seed_id).status == "proposed"
