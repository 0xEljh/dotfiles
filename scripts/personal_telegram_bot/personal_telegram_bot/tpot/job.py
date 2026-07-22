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
from .collection import EvidenceCollection, collect_evidence
from .evidence import evidence_fingerprint, evidence_to_topics, select_evidence
from .synthesizer import OpenCodeSynthesizer, SynthesisError
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
    evidence_fetcher: Callable[[Config, date], EvidenceCollection] = collect_evidence,
    synthesizer_factory: Callable[[], OpenCodeSynthesizer] | None = None,
    client_factory: Callable[[], TpotClient] | None = None,
) -> SeedJobResult:
    now = now or datetime.now(cfg.tz)
    target_date = target_date or now.date()
    store = SeedStore(StateDB(cfg.db_path))
    store.expire_stale_proposed(today=now.date())

    if cfg.tpot_synth_enable:
        collection = evidence_fetcher(cfg, target_date)
        selected = select_evidence(collection.items)
        store.db.log_event(
            "inspiration-evidence",
            {
                "target_date": target_date.isoformat(),
                "provider_status": collection.provider_status,
                "selected": len(selected),
            },
        )
        if not selected:
            return SeedJobResult(exit_code=0)
        generation_key = f"{target_date.isoformat()}:{evidence_fingerprint(selected)}"
        existing = store.seeds_for_generation(generation_key)
        if existing:
            unscored = [seed for seed in existing if seed.score is None]
            if not unscored:
                return SeedJobResult(exit_code=0)
            scores, versions, errors, nonretryable = _score_texts(
                cfg, [seed.text for seed in unscored], client_factory
            )
            if scores is not None:
                store.update_generation_scores(generation_key, scores, versions)
            return SeedJobResult(exit_code=1 if nonretryable else 0, errors=errors)

        synthesizer = (
            synthesizer_factory()
            if synthesizer_factory
            else OpenCodeSynthesizer(
                model=cfg.tpot_synth_model,
                timeout_seconds=cfg.tpot_synth_timeout_seconds,
            )
        )
        try:
            ideas = synthesizer.synthesize(selected)
        except SynthesisError as exc:
            store.db.log_event(
                "inspiration-synthesis-fallback",
                {"target_date": target_date.isoformat(), "error": type(exc).__name__},
            )
            topics = evidence_to_topics(selected)
        else:
            scores, versions, errors, nonretryable = _score_texts(
                cfg, [idea.text for idea in ideas], client_factory
            )
            evidence_by_id = {item.key: item for item in selected}
            grounded = [
                (
                    idea.angle,
                    idea.text,
                    list(idea.evidence_ids),
                    "; ".join(
                        f"{evidence_by_id[key].source}: {evidence_by_id[key].title}"
                        for key in idea.evidence_ids[:4]
                    ),
                )
                for idea in ideas
            ]
            model_versions = versions | {"synthesizer": cfg.tpot_synth_model}
            ids = store.add_grounded_batch(
                seed_date=target_date,
                generation_key=generation_key,
                ideas=grounded,
                scores=scores,
                model_versions=model_versions,
            )
            if ids:
                store.supersede_other_proposed(target_date, generation_key)
            return SeedJobResult(
                exit_code=1 if nonretryable else 0,
                requested_topics=[item.title for item in selected],
                stored=len(ids),
                errors=errors,
            )
    else:
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


def _score_texts(
    cfg: Config,
    texts: list[str],
    client_factory: Callable[[], TpotClient] | None,
) -> tuple[list[float] | None, dict, list[str], bool]:
    if not cfg.tpot_inference_url or not cfg.tpot_inference_token:
        return None, {}, ["TPOT scorer is not configured; stored unscored ideas"], True
    factory = client_factory or (
        lambda: TpotClient(cfg.tpot_inference_url or "", cfg.tpot_inference_token or "")
    )
    try:
        response = factory().batch(
            [{"id": "grounded-ideas", "op": "score", "texts": texts}],
            timeout_s=180,
            retry_top_level=True,
            max_retry_after_s=120,
        )
    except RetryableTpotError as exc:
        return None, {}, [str(exc)], False
    except (OperatorActionTpotError, ClientBugTpotError, TpotError) as exc:
        return None, {}, [str(exc)], True
    try:
        result = response.result_by_id("grounded-ideas")
    except KeyError:
        return None, response.model_versions, ["TPOT score result is missing"], True
    if not result.ok:
        code = result.code or "unknown"
        return None, response.model_versions, [f"TPOT score: {code}"], code not in RETRYABLE_CODES
    if len(result.scores) != len(texts):
        return None, response.model_versions, ["TPOT score count mismatch"], True
    return result.scores, response.model_versions, [], False


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
