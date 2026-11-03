import io
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from personal_telegram_bot import cli, telegram_api
from personal_telegram_bot.config import Config


@pytest.fixture
def cfg():
    return Config.from_env({
        "TELEGRAM_BOT_TOKEN": "fake-token-not-a-secret",
        "TELEGRAM_DEFAULT_CHAT_ID": "123",
        "TELEGRAM_ALLOWED_USER_IDS": "123,456",
    })


def response(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data)


def private_chat(chat_id=123, chat_type="private"):
    return response({"ok": True, "result": {"id": chat_id, "type": chat_type}})


def setup_cli(monkeypatch, cfg, text="exact <plain> report"):
    monkeypatch.setattr(Config, "from_env", lambda: cfg)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(text))


@pytest.mark.parametrize("chat,allowed", [(-123, {-123}), (0, {0}), (123, set()), (789, {123})])
def test_report_destination_rejected_locally_before_network(cfg, monkeypatch, capsys, chat, allowed):
    setup_cli(monkeypatch, replace(cfg, default_chat_id=chat, allowed_user_ids=frozenset(allowed)))
    post = Mock()
    monkeypatch.setattr(telegram_api.httpx, "post", post)
    assert cli.main(["report-plain"]) == 1
    post.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {"error": "configuration"}


@pytest.mark.parametrize("chat_id,chat_type", [(456, "private"), (123, "group"), (123, "supergroup"), ("123", "private"), (None, "private")])
def test_report_destination_requires_live_private_matching_owner(cfg, monkeypatch, capsys, chat_id, chat_type):
    setup_cli(monkeypatch, cfg)
    post = Mock(return_value=private_chat(chat_id, chat_type))
    monkeypatch.setattr(telegram_api.httpx, "post", post)
    assert cli.main(["report-plain"]) == 1
    assert post.call_count == 1
    assert post.call_args.args[0].endswith("/getChat")
    assert json.loads(capsys.readouterr().out) == {"error": "configuration"}


def test_destination_startup_check_sends_nothing_and_send_rechecks_each_time(cfg, monkeypatch, capsys):
    setup_cli(monkeypatch, cfg)
    monkeypatch.setattr(cli.sys, "stdin", Mock(read=Mock(side_effect=AssertionError("validation must not read stdin"))))
    post = Mock(side_effect=[private_chat(), private_chat(), response({
        "ok": True, "result": {"message_id": 456}
    }), private_chat(123, "group")])
    monkeypatch.setattr(telegram_api.httpx, "post", post)
    assert cli.main(["report-plain", "--validate-only"]) == 0
    assert json.loads(capsys.readouterr().out) == {"destination_valid": True}
    setup_cli(monkeypatch, cfg)
    assert cli.main(["report-plain"]) == 0
    assert json.loads(capsys.readouterr().out) == {"message_id": 456}
    send = post.call_args_list[2]
    assert send.args[0].endswith("/sendMessage")
    assert send.kwargs["json"] == {
        "chat_id": 123, "text": "exact <plain> report", "disable_web_page_preview": True,
    }
    setup_cli(monkeypatch, cfg)
    assert cli.main(["report-plain"]) == 1
    assert post.call_count == 4


@pytest.mark.parametrize("length,success", [(3500, True), (3501, False), (0, False)])
def test_plain_sender_rejects_oversize_without_truncation(cfg, monkeypatch, capsys, length, success):
    setup_cli(monkeypatch, cfg, "x" * length)
    post = Mock(side_effect=[private_chat(), response({"ok": True, "result": {"message_id": 7}})])
    monkeypatch.setattr(telegram_api.httpx, "post", post)
    assert cli.main(["report-plain"]) == (0 if success else 1)
    if success:
        assert post.call_args.kwargs["json"]["text"] == "x" * length
    else:
        post.assert_not_called()
        assert json.loads(capsys.readouterr().out) == {"error": "configuration"}


def test_rate_limit_returns_structured_retry_after_without_api_description(cfg, monkeypatch, capsys):
    setup_cli(monkeypatch, cfg)
    post = Mock(side_effect=[private_chat(), response({
        "ok": False, "error_code": 429, "description": "PRIVATE BODY fake-token-not-a-secret",
        "parameters": {"retry_after": 93},
    }, 429)])
    monkeypatch.setattr(telegram_api.httpx, "post", post)
    assert cli.main(["report-plain"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "rate_limited", "retry_after": 93}
    assert "PRIVATE" not in captured.out + captured.err
    assert cfg.telegram_token not in captured.out + captured.err


@pytest.mark.parametrize("failure,expected", [
    (httpx.ReadTimeout("PRIVATE token in URL"), "transient"),
    (response({"ok": False, "error_code": 500, "description": "PRIVATE"}, 500), "transient"),
    (response({"ok": False, "error_code": 403, "description": "PRIVATE"}, 403), "configuration"),
    (response({"ok": True, "result": {"message_id": True}}), "transient"),
])
def test_sender_failures_are_content_free_and_unconfirmed_ids_fail(cfg, monkeypatch, capsys, failure, expected):
    setup_cli(monkeypatch, cfg)
    monkeypatch.setattr(telegram_api.httpx, "post", Mock(side_effect=[private_chat(), failure]))
    assert cli.main(["report-plain"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": expected}
    assert "PRIVATE" not in captured.out + captured.err


def test_config_parse_failure_emits_only_structured_content_free_error(monkeypatch, capsys):
    monkeypatch.setattr(Config, "from_env", Mock(side_effect=ValueError("PRIVATE env content")))
    assert cli.main(["report-plain", "--validate-only"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "configuration"}
    assert "PRIVATE" not in captured.out + captured.err
