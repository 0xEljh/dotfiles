from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.config import Config
from personal_telegram_bot.db import StateDB
from personal_telegram_bot.tpot.client import BatchResponse, BatchResult, Candidate, OperatorActionTpotError, RetryableTpotError
from personal_telegram_bot.tpot.job import run_tpot_seed
from personal_telegram_bot.tpot.seeds import SeedStore
from personal_telegram_bot.tpot.topics import Topic

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
        tpot_inference_url="http://tpot.test",
        tpot_inference_token="secret",
        wakatime_api_key="waka",
    )


def test_seed_job_noops_when_all_topics_already_have_candidates(tmp_path):
    cfg = _cfg(tmp_path)
    topic = Topic(text="working on tpot", source="waka:tpot", provenance="1.2h on tpot")
    SeedStore(StateDB(cfg.db_path)).add_seed(
        seed_date=date(2026, 6, 28),
        topic=topic.text,
        source=topic.source,
        provenance=topic.provenance,
        text="existing",
        score=1.0,
        model_versions={},
    )

    def client_factory():
        raise AssertionError("client should not be constructed")

    result = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ),
        topics_fetcher=lambda *_: [topic],
        client_factory=client_factory,
    )

    assert result.exit_code == 0
    assert result.requested_topics == []


def test_seed_job_stores_partial_success_and_next_run_requests_only_missing(tmp_path):
    cfg = _cfg(tmp_path)
    topics = [
        Topic(text="working on a", source="waka:a", provenance="1.5h on a"),
        Topic(text="working on b", source="waka:b", provenance="1.4h on b"),
    ]
    calls = []

    class FakeClient:
        def batch(self, requests, **kwargs):
            calls.append([request["topic"] for request in requests])
            if len(calls) == 1:
                return BatchResponse(
                    model_versions={"writer": "w1"},
                    results=[
                        BatchResult(id="topic-0", status="ok", candidates=[Candidate(text="seed a", score=1.0)]),
                        BatchResult(id="topic-1", status="error", code="deadline_exceeded", message="slow"),
                    ],
                )
            return BatchResponse(
                model_versions={"writer": "w1"},
                results=[BatchResult(id="topic-0", status="ok", candidates=[Candidate(text="seed b", score=0.9)])],
            )

    first = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ),
        topics_fetcher=lambda *_: topics,
        client_factory=FakeClient,
    )
    second = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 30, tzinfo=TZ),
        topics_fetcher=lambda *_: topics,
        client_factory=FakeClient,
    )

    assert first.exit_code == 0 and second.exit_code == 0
    assert calls == [["working on a", "working on b"], ["working on b"]]
    rows = SeedStore(StateDB(cfg.db_path)).seeds_for_date(date(2026, 6, 28))
    assert [row.text for row in rows] == ["seed a", "seed b"]


def test_seed_job_retryable_api_failures_exit_zero(tmp_path):
    cfg = _cfg(tmp_path)
    topic = Topic(text="working on tpot", source="waka:tpot", provenance="1.2h on tpot")

    class FakeClient:
        def batch(self, requests, **kwargs):
            raise RetryableTpotError("gpu_unavailable", "desktop asleep")

    result = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ),
        topics_fetcher=lambda *_: [topic],
        client_factory=FakeClient,
    )

    assert result.exit_code == 0
    assert result.stored == 0


def test_seed_job_operator_action_failures_exit_nonzero(tmp_path):
    cfg = _cfg(tmp_path)
    topic = Topic(text="working on tpot", source="waka:tpot", provenance="1.2h on tpot")

    class FakeClient:
        def batch(self, requests, **kwargs):
            raise OperatorActionTpotError("models_not_exported", "missing")

    result = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ),
        topics_fetcher=lambda *_: [topic],
        client_factory=FakeClient,
    )

    assert result.exit_code == 1


def test_seed_job_missing_inference_config_is_operator_failure(tmp_path):
    cfg = replace(_cfg(tmp_path), tpot_inference_url=None, tpot_inference_token=None)
    topic = Topic(text="working on tpot", source="waka:tpot", provenance="1.2h on tpot")

    result = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ),
        topics_fetcher=lambda *_: [topic],
    )

    assert result.exit_code == 1
