import asyncio
import json
import shlex
import subprocess
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from personal_telegram_bot import bot, cli, t3_pairing, telegram_api
from personal_telegram_bot.config import Config
from personal_telegram_bot.db import StateDB


HOSTS = ("sleeper-service", "contents-may-differ")
SECRET = "test-pairing-secret"


@pytest.fixture
def cfg(tmp_path):
    return Config.from_env({
        "TELEGRAM_BOT_TOKEN": "test-bot-token",
        "TELEGRAM_DEFAULT_CHAT_ID": "123",
        "TELEGRAM_ALLOWED_USER_IDS": "123,456",
        "BOT_STATE_DB": str(tmp_path / "bot.sqlite3"),
    })


def payload(host=HOSTS[0], invocation="invocation-1"):
    return {
        "invocationId": invocation,
        "url": f"https://{host}.example.com",
        "pairUrl": f"https://{host}.example.com/pair#token={SECRET}",
        "expiresAt": "2026-09-20T12:05:00Z",
    }


def poll_args(**overrides):
    return SimpleNamespace(**{
        "local_host": HOSTS[0], "remote_host": HOSTS[1],
        "remote_label": HOSTS[1], "local_db": "/unused/local",
        "remote_db": "/unused/remote", "dry_run": False, "force": False,
        **overrides,
    })


@pytest.fixture
def monitor(monkeypatch):
    calls = []

    def helper(host, action, helper_path):
        calls.append((host, action))
        return payload(host)

    monkeypatch.setattr(t3_pairing, "run_pair_helper", helper, raising=False)
    monkeypatch.setattr(t3_pairing, "load_local_sessions", lambda _: [])
    monkeypatch.setattr(t3_pairing, "load_remote_sessions", lambda *_: [])
    send = Mock(return_value=789)
    monkeypatch.setattr(cli, "send_message", send)
    return calls, send


@pytest.mark.parametrize("user,chat,chat_type,allowed", [
    (999, 123, "private", {123}),
    (123, -123, "group", {123}),
    (123, -123, "supergroup", {123}),
    (456, 456, "private", {123, 456}),
    (123, 123, "private", set()),
    (None, 123, "private", {123}),
])
def test_command_requires_private_configured_owner(cfg, monkeypatch, user, chat, chat_type, allowed):
    helper = Mock()
    monkeypatch.setattr(t3_pairing, "run_pair_helper", helper, raising=False)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user) if user else None,
        effective_chat=SimpleNamespace(id=chat, type=chat_type),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(bot_data={"config": replace(cfg, allowed_user_ids=frozenset(allowed))}, args=[])
    asyncio.run(bot.cmd_t3(update, context))
    helper.assert_not_called()
    update.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize("args", [["other"], ["--help"], ["contents-may-differ;id"], ["sleeper-service", "extra"]])
def test_command_rejects_unsafe_host_before_mint(cfg, monkeypatch, args):
    helper = Mock()
    monkeypatch.setattr(t3_pairing, "run_pair_helper", helper, raising=False)
    update, context = owner_request(cfg, args)
    asyncio.run(bot.cmd_t3(update, context))
    helper.assert_not_called()
    assert "Usage: /t3" in update.message.reply_text.call_args.args[0]


def owner_request(cfg, args):
    return (
        SimpleNamespace(effective_user=SimpleNamespace(id=123),
                        effective_chat=SimpleNamespace(id=123, type="private"),
                        message=SimpleNamespace(reply_text=AsyncMock())),
        SimpleNamespace(bot_data={"config": cfg}, args=args),
    )


@pytest.mark.parametrize("args,host", [([], HOSTS[0]), ([HOSTS[0]], HOSTS[0]), ([HOSTS[1]], HOSTS[1])])
def test_command_fresh_link_in_worker_thread_with_no_preview(cfg, monkeypatch, args, host):
    calls = []

    def helper(actual_host, action, helper_path):
        assert threading.current_thread() is not threading.main_thread()
        calls.append((actual_host, action, helper_path))
        return payload(actual_host)

    monkeypatch.setattr(t3_pairing, "run_pair_helper", helper, raising=False)
    update, context = owner_request(cfg, args)
    asyncio.run(bot.cmd_t3(update, context))
    asyncio.run(bot.cmd_t3(update, context))
    assert calls == [(host, "create", cfg.t3_pair_helper)] * 2
    text = update.message.reply_text.call_args.args[0]
    assert all(part in text for part in (host, payload(host)["pairUrl"], "12:05:00", "Tailscale", "single-use", "5 minutes"))
    assert update.message.reply_text.call_args.kwargs["disable_web_page_preview"] is True


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_command_errors_do_not_expose_secrets(cfg, monkeypatch, caplog, delivery_fails):
    helper = Mock(return_value=payload())
    if not delivery_fails:
        helper.side_effect = RuntimeError(SECRET)
    monkeypatch.setattr(t3_pairing, "run_pair_helper", helper, raising=False)
    update, context = owner_request(cfg, [])
    if delivery_fails:
        update.message.reply_text.side_effect = [RuntimeError(SECRET), None]
    asyncio.run(bot.cmd_t3(update, context))
    assert SECRET not in caplog.text
    assert "unavailable" in update.message.reply_text.call_args.args[0]


@pytest.mark.parametrize("host,action,timeout", [(HOSTS[0], "create", 100), (HOSTS[1], "create", 100), (HOSTS[1], "status", 30)])
def test_helper_uses_fixed_commands_and_timeouts(monkeypatch, host, action, timeout):
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(payload(host))))
    monkeypatch.setattr(subprocess, "run", run)
    helper_path = "/etc/profiles/per-user/elijah/bin/t3-pair"
    assert t3_pairing.run_pair_helper(host, action, helper_path) == payload(host)
    command = run.call_args.args[0]
    if host == HOSTS[0]:
        assert command == [helper_path, action]
    else:
        assert command == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes", HOSTS[1], shlex.join([helper_path, action])]
    assert run.call_args.kwargs == {"capture_output": True, "text": True, "timeout": timeout, "check": True}


@pytest.mark.parametrize("host,action,path", [("-oProxyCommand=id", "create", "/bin/t3-pair"), (HOSTS[0], "restart", "/bin/t3-pair"), (HOSTS[0], "create", "t3-pair")])
def test_helper_rejects_unsafe_arguments(monkeypatch, host, action, path):
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError):
        t3_pairing.run_pair_helper(host, action, path)
    run.assert_not_called()


@pytest.mark.parametrize("changes", [{"url": "http://insecure"}, {"pairUrl": "https://different.example.com/?token=secret"}, {"pairUrl": "http://sleeper-service.example.com"}, {"invocationId": ""}, {"expiresAt": None}])
def test_helper_rejects_invalid_responses(monkeypatch, changes):
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(payload() | changes)))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError):
        t3_pairing.run_pair_helper(HOSTS[0], "create", "/bin/t3-pair")


def test_poll_first_observation_sends_then_dedupes_by_host_invocation(cfg, monitor):
    calls, send = monitor
    assert cli.send_t3_pairings(cfg, poll_args()) == 0
    assert send.call_count == 2
    assert cli.send_t3_pairings(cfg, poll_args()) == 0
    assert send.call_count == 2
    assert calls == [(host, action) for host in HOSTS for action in ("status", "create")] + [(host, "status") for host in HOSTS]
    db = StateDB(cfg.db_path)
    rows = db.conn.execute("SELECT date_key FROM sent_digests WHERE kind='t3-link'").fetchall()
    assert {row["date_key"] for row in rows} == {f"{host}/invocation-1" for host in HOSTS}
    assert SECRET not in "\n".join(db.conn.iterdump())


def test_poll_delivery_failure_retries_only_failed_host(cfg, monitor, capsys):
    calls, send = monitor
    send.side_effect = [RuntimeError(SECRET), 789, 790]
    assert cli.send_t3_pairings(cfg, poll_args()) == 1
    assert cli.send_t3_pairings(cfg, poll_args()) == 0
    assert send.call_count == 3
    assert calls.count((HOSTS[0], "create")) == 2
    assert calls.count((HOSTS[1], "create")) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_poll_records_create_invocation_not_stale_status(cfg, monitor, monkeypatch):
    calls, send = monitor
    monkeypatch.setattr(t3_pairing, "run_pair_helper", lambda host, action, _: payload(host, "new" if action == "create" else "old"))
    assert cli.send_t3_pairings(cfg, poll_args()) == 0
    db = StateDB(cfg.db_path)
    assert db.was_sent("t3-link", f"{HOSTS[0]}/new")
    assert not db.was_sent("t3-link", f"{HOSTS[0]}/old")
    monkeypatch.setattr(t3_pairing, "run_pair_helper", lambda host, action, _: payload(host, "newer"))
    assert cli.send_t3_pairings(cfg, poll_args()) == 0
    assert send.call_count == 4


def test_offline_host_not_started_and_session_notifications_preserved(cfg, monitor, monkeypatch, capsys):
    calls, send = monitor

    def helper(host, action, _):
        calls.append((host, action))
        if host == HOSTS[0]:
            raise subprocess.CalledProcessError(1, "status", output=SECRET, stderr=SECRET)
        return payload(host)

    monkeypatch.setattr(t3_pairing, "run_pair_helper", helper)
    db = StateDB(cfg.db_path)
    t3_pairing.select_new_pairings(db, HOSTS[0], [])
    session = t3_pairing.PairingSession("client", None, None, "mobile", None, None, "today")
    monkeypatch.setattr(t3_pairing, "load_local_sessions", lambda _: [session])
    assert cli.send_t3_pairings(cfg, poll_args()) == 1
    assert (HOSTS[0], "create") not in calls
    assert send.call_count == 2
    assert any("New T3 client paired" in call.args[2] for call in send.call_args_list)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("chat,allowed", [(-123, {-123}), (0, {0}), (123, set()), (789, {123})])
def test_cli_never_mints_for_non_owner_destination(cfg, monitor, chat, allowed):
    calls, send = monitor
    cfg = replace(cfg, default_chat_id=chat, allowed_user_ids=frozenset(allowed))
    assert cli.send_t3_pairings(cfg, poll_args()) == 1
    assert cli.send_t3_link(cfg, SimpleNamespace(host=HOSTS[0], dry_run=False)) == 1
    assert calls == []
    send.assert_not_called()


def test_manual_cli_publishes_and_dry_run_does_not_mint(cfg, monitor, monkeypatch, capsys):
    calls, send = monitor
    monkeypatch.setattr(Config, "from_env", lambda: cfg)
    assert cli.main(["send", "t3-link", "--host", HOSTS[1], "--dry-run"]) == 0
    assert calls == [(HOSTS[1], "status")]
    send.assert_not_called()
    assert cli.main(["send", "t3-link", "--host", HOSTS[1]]) == 0
    assert calls[-1] == (HOSTS[1], "create")
    assert send.call_count == 1
    assert cli.send_t3_pairings(cfg, poll_args()) == 0
    assert send.call_count == 2  # Only the local host still needs publication.
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_cli_link_send_disables_previews(cfg, monkeypatch):
    monkeypatch.setattr(t3_pairing, "run_pair_helper", lambda *_: payload(), raising=False)
    post = Mock(return_value=SimpleNamespace(json=lambda: {"ok": True, "result": {"message_id": 789}}))
    monkeypatch.setattr(telegram_api.httpx, "post", post)
    assert cli.send_t3_link(cfg, SimpleNamespace(host=HOSTS[0], dry_run=False)) == 0
    assert post.call_args.kwargs["json"]["disable_web_page_preview"] is True
    assert post.call_args.kwargs["json"]["chat_id"] == 123


def test_t3_command_registered(cfg):
    app = bot.build_application(cfg)
    assert any("t3" in getattr(handler, "commands", ()) for handler in app.handlers[0])
