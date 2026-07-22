from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .topics import Topic


_TOKEN_PREFIX = re.compile(r"\b(?:ghp_|github_pat_|secret_|ntn_|sk-|wakatime_)[A-Za-z0-9_\-]+")
_ASSIGNMENT = re.compile(
    r"\b(?:(?:[A-Za-z][A-Za-z0-9_]*_)?(?:TOKEN|KEY|SECRET|PASSWORD)|Authorization)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


@dataclass(frozen=True)
class EvidenceItem:
    key: str
    source: str
    kind: str
    occurred_at: datetime
    title: str
    detail: str | None
    url: str | None
    private: bool

    def model_dict(self) -> dict:
        return {
            "key": self.key,
            "source": self.source,
            "kind": self.kind,
            "occurred_at": self.occurred_at.isoformat(timespec="minutes"),
            "title": self.title,
            "detail": self.detail,
        }


def normalize_evidence_text(text: str, *, limit: int) -> tuple[str, int]:
    text = _CONTROLS.sub(" ", str(text))
    text, prefix_count = _TOKEN_PREFIX.subn("[REDACTED]", text)
    text, assignment_count = _ASSIGNMENT.subn("[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text, prefix_count + assignment_count


def normalized_item(item: EvidenceItem) -> EvidenceItem:
    title, _ = normalize_evidence_text(item.title, limit=300)
    detail = normalize_evidence_text(item.detail, limit=500)[0] if item.detail else None
    return EvidenceItem(
        key=item.key,
        source=item.source,
        kind=item.kind,
        occurred_at=item.occurred_at,
        title=title,
        detail=detail,
        url=item.url,
        private=item.private,
    )


def _sort_key(item: EvidenceItem) -> tuple:
    priority = {
        "pull_request": 0,
        "commit": 1,
        "task_done_on_date": 2,
        "work_session": 3,
        "task_edited": 4,
        "task_due": 5,
    }.get(item.kind, 6)
    return priority, -item.occurred_at.timestamp(), item.source, item.key


def select_evidence(
    items: Iterable[EvidenceItem], *, max_items: int = 12, max_chars: int = 2400
) -> list[EvidenceItem]:
    normalized = sorted((normalized_item(item) for item in items), key=_sort_key)
    by_source: dict[str, list[EvidenceItem]] = {}
    for item in normalized:
        by_source.setdefault(item.source, []).append(item)

    ordered: list[EvidenceItem] = []
    for source in sorted(by_source):
        ordered.append(by_source[source][0])
    selected_keys = {item.key for item in ordered}
    ordered.extend(item for item in normalized if item.key not in selected_keys)

    selected: list[EvidenceItem] = []
    used_chars = 0
    for item in ordered:
        size = len(json.dumps(item.model_dict(), ensure_ascii=False, separators=(",", ":")))
        if len(selected) >= max_items:
            break
        if selected and used_chars + size > max_chars:
            continue
        selected.append(item)
        used_chars += size
    return selected


def evidence_fingerprint(items: Iterable[EvidenceItem]) -> str:
    payload = [item.model_dict() for item in items]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def evidence_to_topics(items: Iterable[EvidenceItem], *, max_topics: int = 3) -> list[Topic]:
    topics = []
    seen_sources: set[str] = set()
    for item in items:
        if item.source in seen_sources:
            continue
        seen_sources.add(item.source)
        topics.append(
            Topic(
                text=item.title,
                source=f"{item.source}:{item.key.removeprefix(item.source + ':')}",
                provenance=f"{item.source}: {item.title}",
            )
        )
        if len(topics) == max_topics:
            break
    return topics
