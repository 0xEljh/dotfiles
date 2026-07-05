from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from ..config import Config
from ..db import StateDB
from .client import (
    CLIENT_BUG_CODES,
    RETRYABLE_CODES,
    BatchResponse,
    ClientBugTpotError,
    OperatorActionTpotError,
    RetryableTpotError,
    TpotClient,
    TpotError,
)
from .seeds import SeedStore, normalize_seed_text
from .topics import Topic, collect_topics


@dataclass(frozen=True)
class SeedJobResult:
    exit_code: int
    requested_topics: list[str] = field(default_factory=list)
    stored: int = 0
    errors: list[str] = field(default_factory=list)


def _requests_for_topics(topics: list[Topic]) -> list[dict]:
    return [
        {"id": f"topic-{index}", "op": "ideate", "topic": topic.text, "k": 3, "best_of": 8}
        for index, topic in enumerate(topics)
    ]


def run_tpot_seed(
    cfg: Config,
    *,
    target_date: date | None = None,
    now: datetime | None = None,
    topics_fetcher: Callable[[Config, date], list[Topic]] = collect_topics,
    client_factory: Callable[[], TpotClient] | None = None,
) -> SeedJobResult:
    now = now or datetime.now(cfg.tz)
    target_date = target_date or now.date()
    store = SeedStore(StateDB(cfg.db_path))
    store.expire_stale_proposed(today=now.date())

    topics = topics_fetcher(cfg, target_date)
    missing = store.missing_topics(target_date, topics)
    if not missing:
        return SeedJobResult(exit_code=0)

    if not cfg.tpot_inference_url or not cfg.tpot_inference_token:
        return SeedJobResult(
            exit_code=1,
            requested_topics=[topic.text for topic in missing],
            errors=["TPOT_INFERENCE_URL / TPOT_INFERENCE_TOKEN not configured"],
        )

    if client_factory is None:
        client_factory = lambda: TpotClient(cfg.tpot_inference_url or "", cfg.tpot_inference_token or "")

    requests = _requests_for_topics(missing)
    try:
        response = client_factory().batch(
            requests,
            timeout_s=180,
            retry_top_level=True,
            max_retry_after_s=120,
        )
    except RetryableTpotError as exc:
        return SeedJobResult(
            exit_code=0,
            requested_topics=[topic.text for topic in missing],
            errors=[str(exc)],
        )
    except (OperatorActionTpotError, ClientBugTpotError, TpotError) as exc:
        return SeedJobResult(
            exit_code=1,
            requested_topics=[topic.text for topic in missing],
            errors=[str(exc)],
        )

    stored, errors, nonretryable = _store_response(store, target_date, missing, response)
    return SeedJobResult(
        exit_code=1 if nonretryable else 0,
        requested_topics=[topic.text for topic in missing],
        stored=stored,
        errors=errors,
    )


def _store_response(
    store: SeedStore,
    target_date: date,
    topics: list[Topic],
    response: BatchResponse,
) -> tuple[int, list[str], bool]:
    by_id = {f"topic-{index}": topic for index, topic in enumerate(topics)}
    seen = store.recent_seen_normalized_texts(today=target_date, days=14)
    stored = 0
    errors: list[str] = []
    nonretryable = False

    for result in response.results:
        topic = by_id.get(result.id)
        if topic is None:
            errors.append(f"unknown result id {result.id}")
            nonretryable = True
            continue
        if not result.ok:
            code = result.code or "unknown"
            errors.append(f"{topic.text}: {code}")
            if code not in RETRYABLE_CODES:
                nonretryable = True
            if code in CLIENT_BUG_CODES:
                nonretryable = True
            continue

        for candidate in result.candidates:
            normalized = normalize_seed_text(candidate.text)
            if normalized in seen:
                continue
            store.add_seed(
                seed_date=target_date,
                topic=topic.text,
                source=topic.source,
                provenance=topic.provenance,
                text=candidate.text,
                score=candidate.score,
                model_versions=response.model_versions,
            )
            seen.add(normalized)
            stored += 1

    return stored, errors, nonretryable
