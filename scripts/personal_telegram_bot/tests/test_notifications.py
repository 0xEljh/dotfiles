import asyncio
import json
import logging
import random

import httpx
import pytest

from personal_telegram_bot.notifications import NotificationService, NotificationSettings, RetryPolicy
from personal_telegram_bot.telegram_api import OwnerNotificationSender


INPUT = {"kind": "ready", "title": "Ready <plain>", "message": "Tests pass.\r\nReview next."}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


def run_case(args=None, sends=None, chat=None, policy=None, owner=123, allowed=(123,)):
    calls = []
    responses = iter(sends or [{"ok": True, "result": {"message_id": 7}}])

    async def request(req):
        payload = json.loads(req.content)
        calls.append((req.url.path.rsplit("/", 1)[-1], payload))
        if req.url.path.endswith("/getChat"):
            body = chat if chat is not None else {"ok": True, "result": {"id": 123, "type": "private"}}
        else:
            body = next(responses)
        if isinstance(body, Exception):
            raise body
        status = body.pop("_status", 200)
        return httpx.Response(status, json=body)

    clock = Clock()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
            sender = OwnerNotificationSender("SECRET_TOKEN", owner, allowed, client)
            service = NotificationService(sender, policy=policy or RetryPolicy(), clock=clock,
                                          sleep=clock.sleep, jitter=lambda: 0)
            return await service.notify(INPUT if args is None else args)

    return asyncio.run(go()), calls, clock


def test_plain_normal_and_silent_button_payloads():
    for silent in (False, True):
        result, calls, _ = run_case({**INPUT, "silent": silent,
                                    "link": {"url": "https://example.com/work"},
                                    "source": {"project": "dotfiles", "client": "OpenCode", "host": "pc", "session_id": "s1"}})
        assert result["state"] == "sent"
        assert result["send_attempts"] == 1
        assert result["possible_duplicate"] is False
        assert calls[1][1] == {
            "chat_id": 123,
            "text": f"READY | dotfiles\nReady <plain>\n\nTests pass.\nReview next.\n\nOpenCode | pc | session s1\nRef: {result['reference']}",
            "disable_notification": silent,
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {"inline_keyboard": [[{"text": "Open work", "url": "https://example.com/work"}]]},
        }


@pytest.mark.parametrize("extra", [
    {"chat_id": 123}, {"source": {"token": "PRIVATE"}}, {"link": {"url": "https://example.com", "extra": True}},
    {"title": "x\ny"}, {"title": "x\ty"}, {"message": "x\ry"}, {"message": "x\x00y"},
    {"message": "x\u202ey"}, {"title": "x\u2028y"}, {"message": "\ud800"},
    {"title": ""}, {"message": " "}, {"reference": ""}, {"reference": None}, {"silent": 1},
    {"source": None}, {"source": {"host": None}}, {"link": {"url": "http://example.com"}},
    {"link": {"url": "https://user:pass@example.com"}}, {"link": {"url": "https://example.com:bad"}},
    {"link": {"url": "https://example.com", "label": ""}}, {"link": {"url": "https://"}},
    {"message": "😀" * 1800}, {"message": "x" * 2001}, {"title": "x" * 121},
])
def test_invalid_payload_never_reaches_network_or_echoes_values(extra, caplog):
    caplog.set_level(logging.INFO, logger="personal_telegram_bot.notifications")
    result, calls, _ = run_case({**INPUT, **extra})
    assert result["state"] == "not_sent"
    assert result["code"] == "validation"
    assert result["next_action"] == "fix_input"
    assert calls == []
    assert "PRIVATE" not in json.dumps(result) + caplog.text


def test_emoji_joiners_and_literal_markup_are_preserved():
    result, calls, _ = run_case({**INPUT, "message": "👩‍💻 <b>not HTML</b>\tOK"})
    assert result["state"] == "sent"
    assert "👩‍💻 <b>not HTML</b>\tOK" in calls[1][1]["text"]


@pytest.mark.parametrize("chat,owner,allowed,code", [
    (None, -123, (-123,), "destination_rejected"),
    (None, 123, (), "destination_rejected"),
    ({"ok": True, "result": {"id": 123, "type": "group"}}, 123, (123,), "destination_rejected"),
    ({"ok": True, "result": {"id": 456, "type": "private"}}, 123, (123,), "destination_rejected"),
    ({"ok": True, "result": {"id": "123", "type": "private"}}, 123, (123,), "unavailable"),
    ({"ok": False, "error_code": 401}, 123, (123,), "configuration"),
    ({"ok": False, "error_code": 403}, 123, (123,), "destination_rejected"),
    ({"ok": False, "error_code": 429, "parameters": {"retry_after": 90}}, 123, (123,), "rate_limited"),
    (httpx.ReadTimeout("PRIVATE SECRET_TOKEN"), 123, (123,), "unavailable"),
])
def test_failed_preflight_is_known_not_sent(chat, owner, allowed, code):
    result, calls, _ = run_case(chat=chat, owner=owner, allowed=allowed)
    assert result["state"] == "not_sent"
    assert result["code"] == code
    assert result["send_attempts"] == 0
    assert all(method == "getChat" for method, _ in calls)


def test_uncertain_then_success_retains_duplicate_risk_and_identical_payload(caplog):
    caplog.set_level(logging.DEBUG, logger="personal_telegram_bot.notifications")
    result, calls, clock = run_case({**INPUT, "reference": "SECRET_REFERENCE"}, sends=[
        httpx.ReadTimeout("PRIVATE SECRET_TOKEN"), {"ok": True, "result": {"message_id": 8}},
    ])
    assert result["state"] == "sent"
    assert result["possible_duplicate"] is True
    assert result["send_attempts"] == 2
    assert calls[1] == calls[2]
    assert clock.waits == [1]
    assert '"state": "retry"' in caplog.text and '"state": "sent"' in caplog.text
    assert "SECRET_REFERENCE" not in caplog.text
    assert "SECRET_TOKEN" not in caplog.text
    assert "PRIVATE" not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("last", [
    {"ok": False, "error_code": 403},
    {"ok": False, "error_code": 429, "parameters": {"retry_after": 90}},
    {"ok": True, "result": {"message_id": True}},
])
def test_uncertainty_dominates_later_failure(last):
    result, _, _ = run_case(sends=[httpx.ReadTimeout("PRIVATE"), last], policy=RetryPolicy(attempts=2))
    assert result["state"] == "unknown"
    assert result["code"] == "delivery_unknown"
    assert result["possible_duplicate"] is True


def test_rate_limit_delay_is_not_shortened_to_fit_budget():
    result, calls, clock = run_case(sends=[{"ok": False, "error_code": 429, "parameters": {"retry_after": 90}}])
    assert result["code"] == "rate_limited"
    assert result["retry_after_seconds"] == 90
    assert len(calls) == 2
    assert clock.waits == []
    result, calls, clock = run_case(sends=[
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 5}},
        {"ok": True, "result": {"message_id": 8}},
    ])
    assert clock.waits == [5]
    assert result["possible_duplicate"] is False


def test_attempt_limit_and_no_reference_deduplication():
    failures = [httpx.ReadTimeout("PRIVATE") for _ in range(3)]
    result, calls, clock = run_case(sends=failures)
    assert result["state"] == "unknown"
    assert result["send_attempts"] == 3
    assert clock.waits == [1, 2]
    assert len(calls) == 4
    first, _, _ = run_case({**INPUT, "reference": "same"})
    second, calls, _ = run_case({**INPUT, "reference": "same"})
    assert first["call_id"] != second["call_id"]
    assert second["state"] == "sent" and len(calls) == 2


def test_repeated_reference_on_same_service_is_still_a_new_send():
    async def go():
        payloads = []

        class Sender:
            async def validate(self, timeout):
                pass

            async def send(self, text, **kwargs):
                payloads.append(text)
                return len(payloads)

        service = NotificationService(Sender())
        first = await service.notify({**INPUT, "reference": "same"})
        second = await service.notify({**INPUT, "reference": "same"})
        assert payloads[0] == payloads[1]
        assert first["call_id"] != second["call_id"]
        assert second["telegram_message_id"] == 2
    asyncio.run(go())


def test_concurrent_calls_keep_independent_payloads_under_jitter_and_429s():
    async def go():
        counts = {}
        randomizer = random.Random(42)

        async def request(req):
            await asyncio.sleep(randomizer.random() / 1000)
            payload = json.loads(req.content)
            if req.url.path.endswith("/getChat"):
                return httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": "private"}})
            text = payload["text"]
            counts[text] = counts.get(text, 0) + 1
            if counts[text] == 1:
                return httpx.Response(429, json={"ok": False, "error_code": 429})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 8}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
            service = NotificationService(OwnerNotificationSender("fake", 123, (123,), client),
                                          policy=RetryPolicy(backoff=0.001), jitter=randomizer.random)
            results = await asyncio.gather(*(service.notify({**INPUT, "reference": f"task-{i}"}) for i in range(50)))
        assert len(counts) == 50
        assert set(counts.values()) == {2}
        assert all(r["state"] == "sent" and r["send_attempts"] == 2 and not r["possible_duplicate"] for r in results)
    asyncio.run(go())


@pytest.mark.parametrize("status", [408, 500, 503])
def test_ambiguous_http_status_overrides_apparent_rejection_body(status):
    result, _, _ = run_case(sends=[{"_status": status, "ok": False, "error_code": 403}],
                            policy=RetryPolicy(attempts=1))
    assert result["state"] == "unknown"
    assert result["code"] == "delivery_unknown"
    # This flag records an uncertain attempt followed by another send *within
    # this call*, not whether a future call could duplicate an accepted send.
    assert result["possible_duplicate"] is False


@pytest.mark.parametrize("url", ["https://example.com:", "https://[::1]junk", "https://example.com/%xx"])
def test_malformed_url_authority_and_escape_never_send(url):
    result, calls, _ = run_case({**INPUT, "link": {"url": url}})
    assert result["code"] == "validation"
    assert calls == []


def test_validation_identifies_safe_field_not_supplied_value():
    result, _, _ = run_case({**INPUT, "title": "PRIVATE\nLINE"})
    assert "title" in result["message"]
    assert "PRIVATE" not in result["message"]


def test_total_deadline_includes_preflight_and_backoff():
    async def go():
        clock = Clock()
        attempts = []

        class Sender:
            async def validate(self, timeout):
                assert timeout == 5
                clock.now += 3

            async def send(self, *args, **kwargs):
                from personal_telegram_bot.telegram_api import NotificationDeliveryError
                assert kwargs["timeout"] == 2
                attempts.append(1)
                raise NotificationDeliveryError("rate_limited", retry_after=3)

        result = await NotificationService(Sender(), policy=RetryPolicy(deadline=5),
                                           clock=clock, sleep=clock.sleep).notify(INPUT)
        assert result["code"] == "rate_limited"
        assert attempts == [1]
        assert clock.waits == []
    asyncio.run(go())


def test_whole_call_timeout_interrupts_slow_transport_and_cancellation_is_not_retried():
    async def go():
        attempts = []

        class Sender:
            async def validate(self, timeout):
                pass

            async def send(self, *args, **kwargs):
                attempts.append(1)
                await asyncio.sleep(10)

        service = NotificationService(Sender(), policy=RetryPolicy(deadline=0.02))
        result = await asyncio.wait_for(service.notify(INPUT), timeout=1)
        assert result["state"] == "unknown"
        assert result["send_attempts"] == 1
        task = asyncio.create_task(service.notify(INPUT))
        await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert attempts == [1, 1]
    asyncio.run(go())


def test_settings_only_use_explicit_file_without_environment_or_interpolation(tmp_path, monkeypatch):
    path = tmp_path / "bot.env"
    path.write_text('TELEGRAM_BOT_TOKEN="file-token-${UNTRUSTED}"\nTELEGRAM_DEFAULT_CHAT_ID=123\n'
                    'TELEGRAM_ALLOWED_USER_IDS=123\nAGENT_NOTIFY_ATTEMPTS=4\n'
                    'NOTION_TOKEN=ignore-me\n')
    monkeypatch.setenv("UNTRUSTED", "SECRET")
    monkeypatch.setenv("PERSONAL_TELEGRAM_BOT_ENV", "/untrusted/path")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ambient-token")
    settings = NotificationSettings.from_file(path)
    assert settings.token == "file-token-${UNTRUSTED}"
    assert settings.policy.attempts == 4
    assert not hasattr(settings, "notion_token")
    assert "file-token" not in repr(settings)
