import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from personal_telegram_bot import bot
from personal_telegram_bot.config import parse_user_ids
from personal_telegram_bot.bot import (
    is_authorized,
    is_private_owner_chat,
    parse_tpot_callback_data,
)


def test_parse_single_id():
    assert parse_user_ids("448383615") == frozenset({448383615})


def test_parse_multiple_ids_with_spaces():
    assert parse_user_ids("123, 456 ,789") == frozenset({123, 456, 789})


def test_parse_empty_string_is_empty():
    assert parse_user_ids("") == frozenset()


def test_authorized_user_accepted():
    assert is_authorized(448383615, frozenset({448383615}))


def test_unknown_user_rejected():
    assert not is_authorized(999, frozenset({448383615}))


def test_empty_allowlist_rejects_everyone():
    assert not is_authorized(448383615, frozenset())


def test_private_owner_chat_requires_matching_user_and_default_chat():
    assert is_private_owner_chat(123, 123, "private", 123, frozenset({123}))
    assert not is_private_owner_chat(123, -456, "group", -456, frozenset({123}))
    assert not is_private_owner_chat(123, 456, "private", 456, frozenset({123}))
    assert not is_private_owner_chat(999, 999, "private", 999, frozenset({123}))


def test_tpot_callback_parser_accepts_known_actions():
    assert parse_tpot_callback_data("tpot:used:42") == ("used", 42)
    assert parse_tpot_callback_data("tpot:remix:42") == ("remix", 42)
    assert parse_tpot_callback_data("tpot:discarded:42") == ("discarded", 42)


def test_tpot_callback_parser_rejects_invalid_data():
    assert parse_tpot_callback_data(None) is None
    assert parse_tpot_callback_data("tpot:unknown:42") is None
    assert parse_tpot_callback_data("tpot:used:not-an-int") is None


def test_status_keeps_health_details_and_one_send_timestamp_per_digest(monkeypatch):
    cfg = SimpleNamespace(
        allowed_user_ids=frozenset({123}), health_units=(), health_urls=(), health_disk_paths=(),
        health_disk_warning_percent=80, health_disk_critical_percent=90,
        aw_data_dir="unused", aw_max_age_hours=2, db_path="unused",
    )
    monkeypatch.setattr(bot, "run_all", lambda *_: ["service check"])
    monkeypatch.setattr(bot, "check_aw_freshness", lambda *_: "activity check")
    def summary(results):
        assert results == ["service check", "activity check"]
        return "Health: 2 checks OK\nservice: running\nactivity: fresh"
    monkeypatch.setattr(bot, "format_health_summary", summary)
    rows = {
        "morning": {"date_key": "2026-01-01", "sent_at": "2026-01-01T08:00:00Z"},
        "health-alert": {"date_key": "2026-01-02", "sent_at": "2026-01-02T09:00:00Z"},
    }
    monkeypatch.setattr(bot, "StateDB", lambda _: SimpleNamespace(last_sent=rows.get))
    update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(bot.cmd_status(update, SimpleNamespace(bot_data={"config": cfg})))
    text = update.message.reply_text.call_args.args[0]
    assert text.startswith("Health: 2 checks OK\nservice: running\nactivity: fresh")
    assert "Last morning: 2026-01-01T08:00:00Z" in text
    assert "Last health-alert: 2026-01-02T09:00:00Z" in text
    assert text.count("2026-01-01") == 1
    assert text.count("2026-01-02") == 1
    assert "parse_mode" not in update.message.reply_text.call_args.kwargs
