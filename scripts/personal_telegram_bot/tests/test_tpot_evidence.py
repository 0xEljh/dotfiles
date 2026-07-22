from datetime import date, datetime
from zoneinfo import ZoneInfo

from personal_telegram_bot.tpot.evidence import (
    EvidenceItem,
    evidence_fingerprint,
    evidence_to_topics,
    normalize_evidence_text,
    select_evidence,
)


TZ = ZoneInfo("Asia/Singapore")


def _item(key: str, source: str, title: str, hour: int = 12) -> EvidenceItem:
    return EvidenceItem(
        key=key,
        source=source,
        kind="work",
        occurred_at=datetime(2026, 7, 20, hour, tzinfo=TZ),
        title=title,
        detail=None,
        url=None,
        private=True,
    )


def test_normalization_redacts_secrets_controls_and_bounds_text():
    text, redactions = normalize_evidence_text(
        "ship\x00 TOKEN=abc123 github_pat_abcdefghijklmnopqrstuvwxyz " + "x" * 400,
        limit=80,
    )

    assert "abc123" not in text
    assert "github_pat_" not in text
    assert "\x00" not in text
    assert len(text) == 80
    assert text.endswith("...")
    assert redactions == 2


def test_selection_is_source_balanced_stable_and_fingerprinted():
    items = [
        _item("g2", "github", "second", 13),
        _item("g1", "github", "first", 12),
        _item("n1", "notion", "task", 10),
        _item("w1", "wakatime", "project", 9),
    ]

    selected = select_evidence(list(reversed(items)), max_items=3, max_chars=2400)

    assert {item.source for item in selected} == {"github", "notion", "wakatime"}
    assert selected == select_evidence(items, max_items=3, max_chars=2400)
    assert evidence_fingerprint(selected) == evidence_fingerprint(list(selected))


def test_evidence_fallback_topics_remain_source_balanced():
    items = [
        _item("g1", "github", "opened pull request"),
        _item("n1", "notion", "finished rollout"),
        _item("w1", "wakatime", "worked on dotfiles"),
    ]

    topics = evidence_to_topics(items)

    assert [topic.source for topic in topics] == ["github:g1", "notion:n1", "wakatime:w1"]
    assert topics[0].text == "opened pull request"
