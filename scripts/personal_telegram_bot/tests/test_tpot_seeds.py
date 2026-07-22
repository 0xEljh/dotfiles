from datetime import date

from personal_telegram_bot.db import StateDB
from personal_telegram_bot.tpot.seeds import SeedStore, normalize_seed_text
from personal_telegram_bot.tpot.topics import Topic


def _store(tmp_path) -> SeedStore:
    return SeedStore(StateDB(tmp_path / "state.sqlite3"))


def test_seed_events_are_append_only_and_status_tracks_current_state(tmp_path):
    store = _store(tmp_path)
    seed_id = store.add_seed(
        seed_date=date(2026, 6, 28),
        topic="working on tpot",
        source="waka:tpot",
        provenance="3.2h on tpot",
        text="post this",
        score=1.5,
        model_versions={"writer": "w1"},
        created_at="2026-06-28T14:00:00+00:00",
    )

    store.record_event(seed_id, "surfaced", {"message_id": 101}, at="2026-06-28T22:00:00+00:00")
    store.record_event(seed_id, "remixed", {"text": "variant"}, at="2026-06-28T22:01:00+00:00")
    store.record_event(seed_id, "used", {"source": "button"}, at="2026-06-28T22:02:00+00:00")

    row = store.get_seed(seed_id)
    events = store.events_for_seed(seed_id)
    assert row is not None
    assert row.status == "used"
    assert [event.event for event in events] == ["surfaced", "remixed", "used"]
    assert events[0].detail == {"message_id": 101}


def test_expire_stale_proposed_marks_only_after_following_day(tmp_path):
    store = _store(tmp_path)
    old_id = store.add_seed(
        seed_date=date(2026, 6, 10),
        topic="old",
        source="manual",
        provenance="manual",
        text="old",
        score=None,
        model_versions={},
    )
    fresh_id = store.add_seed(
        seed_date=date(2026, 6, 11),
        topic="fresh",
        source="manual",
        provenance="manual",
        text="fresh",
        score=None,
        model_versions={},
    )

    expired = store.expire_stale_proposed(today=date(2026, 6, 12), at="2026-06-12T23:59:00+08:00")

    assert expired == [old_id]
    assert store.get_seed(old_id).status == "expired"
    assert store.get_seed(fresh_id).status == "proposed"
    assert store.events_for_seed(old_id)[0].event == "expired"


def test_recent_seen_text_dedupe_ignores_proposed_and_expired_rows(tmp_path):
    store = _store(tmp_path)
    surfaced = store.add_seed(
        seed_date=date(2026, 6, 14),
        topic="seen",
        source="manual",
        provenance="manual",
        text="Ship  the thing!",
        score=None,
        model_versions={},
    )
    proposed = store.add_seed(
        seed_date=date(2026, 6, 14),
        topic="hidden",
        source="manual",
        provenance="manual",
        text="Invisible",
        score=None,
        model_versions={},
    )
    expired = store.add_seed(
        seed_date=date(2026, 6, 10),
        topic="expired",
        source="manual",
        provenance="manual",
        text="Expired",
        score=None,
        model_versions={},
    )
    store.record_event(surfaced, "surfaced", {}, at="2026-06-14T22:00:00+08:00")
    store.record_event(expired, "expired", {}, at="2026-06-12T22:00:00+08:00")

    seen = store.recent_seen_normalized_texts(today=date(2026, 6, 15), days=14)

    assert normalize_seed_text("ship the thing!") in seen
    assert normalize_seed_text("Invisible") not in seen
    assert normalize_seed_text("Expired") not in seen
    assert store.get_seed(proposed).status == "proposed"


def test_missing_topics_are_based_on_existing_seed_rows(tmp_path):
    store = _store(tmp_path)
    topics = [
        Topic(text="working on a", source="waka:a", provenance="1.2h on a"),
        Topic(text="working on b", source="waka:b", provenance="1.1h on b"),
    ]
    store.add_seed(
        seed_date=date(2026, 6, 28),
        topic="working on a",
        source="waka:a",
        provenance="1.2h on a",
        text="candidate",
        score=1.0,
        model_versions={},
    )

    assert store.missing_topics(date(2026, 6, 28), topics) == [topics[1]]


def test_grounded_batch_is_idempotent_and_missing_scores_can_be_updated(tmp_path):
    store = _store(tmp_path)
    ids = store.add_grounded_batch(
        seed_date=date(2026, 7, 20),
        generation_key="fingerprint",
        ideas=[
            ("lesson", "First grounded idea for the evening.", ["github:event:1"], "github: committed change"),
            ("question", "Second grounded idea for the evening.", ["notion:page:1"], "notion: shipped task"),
        ],
        scores=None,
        model_versions={"synthesizer": "deepseek"},
    )

    assert len(ids) == 2
    assert store.add_grounded_batch(
        seed_date=date(2026, 7, 20),
        generation_key="fingerprint",
        ideas=[],
        scores=None,
        model_versions={},
    ) == []
    assert [seed.id for seed in store.unscored_for_generation("fingerprint")] == ids

    store.update_generation_scores("fingerprint", [0.4, 0.8], {"scorer": "v1"})

    assert [seed.score for seed in store.seeds_for_date(date(2026, 7, 20))] == [0.4, 0.8]


def test_mark_surfaced_records_digest(tmp_path):
    store = _store(tmp_path)
    seed_id = store.add_seed(
        seed_date=date(2026, 7, 20), topic="topic", source="source", provenance="p",
        text="idea", score=None, model_versions={},
    )
    seed = store.get_seed(seed_id)

    store.mark_surfaced([seed], message_id=12, digest="standdown")

    assert store.events_for_seed(seed_id)[0].detail == {"digest": "standdown", "message_id": 12}


def test_new_grounded_generation_supersedes_only_older_proposed_rows(tmp_path):
    store = _store(tmp_path)
    old_ids = store.add_grounded_batch(
        seed_date=date(2026, 7, 20), generation_key="old",
        ideas=[("lesson", "An older grounded idea for tonight.", ["github:1"], "github: old")],
        scores=None, model_versions={},
    )
    protected_ids = store.add_grounded_batch(
        seed_date=date(2026, 7, 20), generation_key="protected",
        ideas=[("question", "A surfaced grounded idea for tonight.", ["github:2"], "github: protected")],
        scores=None, model_versions={},
    )
    store.record_event(protected_ids[0], "surfaced", {})

    superseded = store.supersede_other_proposed(date(2026, 7, 20), "new")

    assert superseded == old_ids
    assert store.get_seed(old_ids[0]).status == "superseded"
    assert store.get_seed(protected_ids[0]).status == "surfaced"
