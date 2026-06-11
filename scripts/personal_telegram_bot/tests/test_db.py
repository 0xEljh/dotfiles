from personal_telegram_bot.db import StateDB


def make_db(tmp_path):
    return StateDB(tmp_path / "state.sqlite3")


def test_digest_not_sent_initially(tmp_path):
    db = make_db(tmp_path)
    assert not db.was_sent("morning", "2026-06-11")


def test_digest_dedupe(tmp_path):
    db = make_db(tmp_path)
    db.record_sent("morning", "2026-06-11", message_id=42)
    assert db.was_sent("morning", "2026-06-11")
    # other kinds and dates unaffected
    assert not db.was_sent("night", "2026-06-11")
    assert not db.was_sent("morning", "2026-06-12")


def test_record_sent_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    db.record_sent("morning", "2026-06-11", message_id=1)
    db.record_sent("morning", "2026-06-11", message_id=2)
    assert db.was_sent("morning", "2026-06-11")


def test_health_state_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_health_statuses() == {}
    db.set_health_status("nginx.service", "ok", detail="active")
    db.set_health_status("kodo-api.service", "fail", detail="failed")
    assert db.get_health_statuses() == {
        "nginx.service": "ok",
        "kodo-api.service": "fail",
    }


def test_health_since_preserved_on_same_status(tmp_path):
    db = make_db(tmp_path)
    db.set_health_status("nginx.service", "ok", detail="active", now="2026-06-11T09:00:00")
    db.set_health_status("nginx.service", "ok", detail="active", now="2026-06-11T10:00:00")
    assert db.get_health_row("nginx.service")["since"] == "2026-06-11T09:00:00"


def test_health_since_resets_on_status_change(tmp_path):
    db = make_db(tmp_path)
    db.set_health_status("nginx.service", "ok", detail="active", now="2026-06-11T09:00:00")
    db.set_health_status("nginx.service", "fail", detail="failed", now="2026-06-11T10:00:00")
    assert db.get_health_row("nginx.service")["since"] == "2026-06-11T10:00:00"


def test_last_sent(tmp_path):
    db = make_db(tmp_path)
    assert db.last_sent("morning") is None
    db.record_sent("morning", "2026-06-10", message_id=7)
    db.record_sent("morning", "2026-06-11", message_id=8)
    assert db.last_sent("morning")["date_key"] == "2026-06-11"
