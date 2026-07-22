from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.config import Config
from personal_telegram_bot.db import StateDB
from personal_telegram_bot.tpot.client import BatchResponse, BatchResult, Candidate, OperatorActionTpotError, RetryableTpotError
from personal_telegram_bot.tpot.collection import EvidenceCollection
from personal_telegram_bot.tpot.evidence import EvidenceItem
from personal_telegram_bot.tpot.job import run_tpot_seed
from personal_telegram_bot.tpot.seeds import SeedStore
from personal_telegram_bot.tpot.synthesizer import SynthesizedIdea, SynthesisError
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


def _evidence() -> EvidenceItem:
    return EvidenceItem(
        key="github:event:1",
        source="github",
        kind="commit",
        occurred_at=datetime(2026, 6, 28, 18, 0, tzinfo=TZ),
        title="Committed evidence pipeline",
        detail="Ship grounded synthesis",
        url=None,
        private=True,
    )


def test_grounded_job_scores_unchanged_synthesis_and_persists_provenance(tmp_path):
    cfg = replace(_cfg(tmp_path), tpot_synth_enable=True)
    ideas = [
        SynthesizedIdea(
            "The best writing prompts preserve the evidence behind the work.",
            ("github:event:1",),
            "lesson",
            "high",
        )
    ]
    requests = []

    class Synth:
        def synthesize(self, evidence):
            return ideas

    class FakeClient:
        def batch(self, incoming, **kwargs):
            requests.extend(incoming)
            return BatchResponse(
                model_versions={"scorer": "v1"},
                results=[BatchResult(id="grounded-ideas", status="ok", scores=[0.8])],
            )

    result = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ),
        evidence_fetcher=lambda *_: EvidenceCollection([_evidence()], {"github": "ok_nonempty"}),
        synthesizer_factory=lambda: Synth(),
        client_factory=FakeClient,
    )

    assert result.exit_code == 0 and result.stored == 1
    assert requests == [{"id": "grounded-ideas", "op": "score", "texts": [ideas[0].text]}]
    row = SeedStore(StateDB(cfg.db_path)).seeds_for_date(date(2026, 6, 28))[0]
    assert row.text == ideas[0].text
    assert row.generator == "opencode"
    assert row.evidence_keys == ("github:event:1",)
    assert row.score == 0.8


def test_grounded_job_retries_only_missing_scores_without_resynthesis(tmp_path):
    cfg = replace(_cfg(tmp_path), tpot_synth_enable=True)
    synth_calls = 0
    client_calls = 0

    class Synth:
        def synthesize(self, evidence):
            nonlocal synth_calls
            synth_calls += 1
            return [
                SynthesizedIdea(
                    "A grounded idea with enough detail to stand alone.",
                    ("github:event:1",),
                    "observation",
                    "high",
                )
            ]

    class FakeClient:
        def batch(self, incoming, **kwargs):
            nonlocal client_calls
            client_calls += 1
            scores = [] if client_calls == 1 else [0.7]
            return BatchResponse(
                model_versions={"scorer": "v1"},
                results=[BatchResult(id="grounded-ideas", status="ok", scores=scores)],
            )

    kwargs = dict(
        target_date=date(2026, 6, 28),
        evidence_fetcher=lambda *_: EvidenceCollection([_evidence()], {"github": "ok_nonempty"}),
        synthesizer_factory=lambda: Synth(),
        client_factory=FakeClient,
    )
    first = run_tpot_seed(cfg, now=datetime(2026, 6, 28, 21, 0, tzinfo=TZ), **kwargs)
    second = run_tpot_seed(cfg, now=datetime(2026, 6, 28, 21, 30, tzinfo=TZ), **kwargs)

    assert first.stored == 1 and second.stored == 0
    assert synth_calls == 1 and client_calls == 2
    assert SeedStore(StateDB(cfg.db_path)).seeds_for_date(date(2026, 6, 28))[0].score == 0.7


def test_synthesis_failure_falls_back_to_existing_ideate_contract(tmp_path):
    cfg = replace(_cfg(tmp_path), tpot_synth_enable=True)
    requests = []

    class Synth:
        def synthesize(self, evidence):
            raise SynthesisError("invalid output")

    class FakeClient:
        def batch(self, incoming, **kwargs):
            requests.extend(incoming)
            return BatchResponse(
                model_versions={"writer": "v1"},
                results=[BatchResult(id="topic-0", status="ok", candidates=[Candidate("fallback idea", 0.2)])],
            )

    result = run_tpot_seed(
        cfg,
        target_date=date(2026, 6, 28),
        evidence_fetcher=lambda *_: EvidenceCollection([_evidence()], {"github": "ok_nonempty"}),
        synthesizer_factory=lambda: Synth(),
        client_factory=FakeClient,
    )

    assert result.stored == 1
    assert requests[0]["op"] == "ideate"
    assert requests[0]["topic"] == "Committed evidence pipeline"
