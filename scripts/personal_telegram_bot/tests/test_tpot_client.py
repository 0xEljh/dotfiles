import json

import httpx
import pytest

from personal_telegram_bot.tpot.client import (
    OperatorActionTpotError,
    RetryableTpotError,
    TpotClient,
)


def test_batch_sends_contract_payload_and_bearer_header():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url == "http://tpot.test/v1/tpot/batch"
        assert request.headers["authorization"] == "Bearer secret"
        assert json.loads(request.content) == {
            "requests": [
                {"id": "i1", "op": "ideate", "topic": "working on tpot", "k": 3, "best_of": 8}
            ],
            "timeout_s": 180,
        }
        return httpx.Response(
            200,
            json={
                "model_versions": {"writer": "w1", "scorer": "s1"},
                "results": [
                    {
                        "id": "i1",
                        "status": "ok",
                        "candidates": [{"text": "ship the thing", "score": 1.2}],
                    }
                ],
            },
        )

    client = TpotClient(
        "http://tpot.test",
        "secret",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )

    response = client.batch(
        [{"id": "i1", "op": "ideate", "topic": "working on tpot", "k": 3, "best_of": 8}],
        timeout_s=180,
    )

    assert len(seen) == 1
    assert response.model_versions == {"writer": "w1", "scorer": "s1"}
    assert response.result_by_id("i1").candidates[0].text == "ship the thing"
    assert response.result_by_id("i1").candidates[0].score == 1.2


def test_batch_honors_short_retry_after_once():
    attempts = 0
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                503,
                json={"error": {"code": "gpu_busy", "message": "busy", "retry_after_s": 7}},
            )
        return httpx.Response(200, json={"model_versions": {}, "results": []})

    client = TpotClient(
        "http://tpot.test",
        "secret",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )

    client.batch([], timeout_s=180, retry_top_level=True, max_retry_after_s=120)

    assert attempts == 2
    assert sleeps == [7]


def test_batch_does_not_sleep_for_too_long_retry_after():
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": {"code": "gpu_busy", "message": "busy", "retry_after_s": 121}},
        )

    client = TpotClient(
        "http://tpot.test",
        "secret",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )

    with pytest.raises(RetryableTpotError) as exc:
        client.batch([], timeout_s=180, retry_top_level=True, max_retry_after_s=120)

    assert exc.value.code == "gpu_busy"
    assert exc.value.retry_after_s == 121
    assert sleeps == []


def test_operator_action_error_is_not_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": {"code": "models_not_exported", "message": "missing exports"}},
        )

    client = TpotClient("http://tpot.test", "secret", transport=httpx.MockTransport(handler))

    with pytest.raises(OperatorActionTpotError) as exc:
        client.batch([], timeout_s=180)

    assert exc.value.code == "models_not_exported"


def test_per_result_errors_are_returned_for_partial_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model_versions": {"writer": "w1"},
                "results": [
                    {"id": "ok", "status": "ok", "candidates": [{"text": "one", "score": 0.5}]},
                    {
                        "id": "slow",
                        "status": "error",
                        "code": "deadline_exceeded",
                        "message": "timed out",
                    },
                ],
            },
        )

    client = TpotClient("http://tpot.test", "secret", transport=httpx.MockTransport(handler))

    response = client.batch([], timeout_s=180)

    assert response.result_by_id("ok").ok
    assert not response.result_by_id("slow").ok
    assert response.result_by_id("slow").code == "deadline_exceeded"
