from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

import httpx

RETRYABLE_CODES = frozenset({"gpu_busy", "gpu_unavailable", "deadline_exceeded", "connection_error"})
OPERATOR_ACTION_CODES = frozenset({"models_not_exported", "model_load_failed", "auth_failed"})
CLIENT_BUG_CODES = frozenset({"validation_failed"})


class TpotError(Exception):
    def __init__(self, code: str, message: str, retry_after_s: int | float | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s


class RetryableTpotError(TpotError):
    pass


class OperatorActionTpotError(TpotError):
    pass


class ClientBugTpotError(TpotError):
    pass


@dataclass(frozen=True)
class Candidate:
    text: str
    score: float | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> Candidate:
        score = raw.get("score")
        return cls(text=str(raw.get("text", "")), score=float(score) if score is not None else None)


@dataclass(frozen=True)
class BatchResult:
    id: str
    status: str
    candidates: list[Candidate] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    code: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @classmethod
    def from_raw(cls, raw: dict) -> BatchResult:
        return cls(
            id=str(raw.get("id", "")),
            status=str(raw.get("status", "error")),
            candidates=[Candidate.from_raw(item) for item in raw.get("candidates", [])],
            scores=[float(score) for score in raw.get("scores", [])],
            code=raw.get("code"),
            message=raw.get("message"),
        )


@dataclass(frozen=True)
class BatchResponse:
    model_versions: dict
    results: list[BatchResult]

    def result_by_id(self, request_id: str) -> BatchResult:
        for result in self.results:
            if result.id == request_id:
                return result
        raise KeyError(request_id)

    @classmethod
    def from_raw(cls, raw: dict) -> BatchResponse:
        return cls(
            model_versions=raw.get("model_versions") or {},
            results=[BatchResult.from_raw(item) for item in raw.get("results", [])],
        )


def _http_timeout(timeout_s: int | float) -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=float(timeout_s) + 30.0, write=10.0, pool=5.0)


def _error_from_response(response: httpx.Response) -> TpotError:
    try:
        raw = response.json()
    except ValueError:
        raw = {}
    error = raw.get("error") if isinstance(raw, dict) else None
    if not isinstance(error, dict):
        error = {}

    if response.status_code in (401, 403):
        return OperatorActionTpotError("auth_failed", error.get("message") or "TPOT auth failed")

    code = str(error.get("code") or f"http_{response.status_code}")
    message = str(error.get("message") or response.text or response.reason_phrase)
    retry_after_s = error.get("retry_after_s")

    if code in OPERATOR_ACTION_CODES:
        return OperatorActionTpotError(code, message, retry_after_s=retry_after_s)
    if code in CLIENT_BUG_CODES or 400 <= response.status_code < 500:
        return ClientBugTpotError(code, message, retry_after_s=retry_after_s)
    if code in RETRYABLE_CODES or response.status_code >= 500:
        return RetryableTpotError(code, message, retry_after_s=retry_after_s)
    return TpotError(code, message, retry_after_s=retry_after_s)


class TpotClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[int | float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport
        self.sleep = sleep

    def batch(
        self,
        requests: Iterable[dict],
        *,
        timeout_s: int = 180,
        retry_top_level: bool = False,
        max_retry_after_s: int = 120,
    ) -> BatchResponse:
        payload = {"requests": list(requests), "timeout_s": timeout_s}
        retried = False

        while True:
            try:
                with httpx.Client(
                    base_url=self.base_url,
                    timeout=_http_timeout(timeout_s),
                    transport=self.transport,
                ) as client:
                    response = client.post(
                        "/v1/tpot/batch",
                        headers={"Authorization": f"Bearer {self.token}"},
                        json=payload,
                    )
            except httpx.RequestError as exc:
                raise RetryableTpotError("connection_error", str(exc)) from exc

            if not response.is_error:
                return BatchResponse.from_raw(response.json())

            error = _error_from_response(response)
            retry_after = error.retry_after_s
            if (
                retry_top_level
                and not retried
                and isinstance(error, RetryableTpotError)
                and retry_after is not None
                and retry_after <= max_retry_after_s
            ):
                retried = True
                self.sleep(retry_after)
                continue
            raise error
