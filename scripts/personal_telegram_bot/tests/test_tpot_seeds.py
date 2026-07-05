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
