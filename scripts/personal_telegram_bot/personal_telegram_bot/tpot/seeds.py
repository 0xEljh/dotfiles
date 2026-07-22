from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from ..db import StateDB
from .client import Candidate
from .topics import Topic


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date_key(seed_date: date | str) -> str:
    return seed_date if isinstance(seed_date, str) else seed_date.isoformat()


def normalize_seed_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass(frozen=True)
class SeedRow:
    id: int
    seed_date: date
    topic: str
    source: str
    provenance: str
    text: str
    score: float | None
    model_versions: dict
    status: str
    generation_key: str | None
    evidence_keys: tuple[str, ...]
    evidence_summary: str | None
    generator: str
    candidate_order: int | None
    created_at: str


@dataclass(frozen=True)
class SeedEvent:
    id: int
    seed_id: int
    event: str
    detail: dict | None
    at: str


def _row_to_seed(row) -> SeedRow:
    return SeedRow(
        id=row["id"],
        seed_date=date.fromisoformat(row["seed_date"]),
        topic=row["topic"],
        source=row["source"],
        provenance=row["provenance"],
        text=row["text"],
        score=row["score"],
        model_versions=json.loads(row["model_versions"] or "{}"),
        status=row["status"],
        generation_key=row["generation_key"],
        evidence_keys=tuple(json.loads(row["evidence_keys"] or "[]")),
        evidence_summary=row["evidence_summary"],
        generator=row["generator"],
        candidate_order=row["candidate_order"],
        created_at=row["created_at"],
    )


def _row_to_event(row) -> SeedEvent:
    detail = json.loads(row["detail"]) if row["detail"] else None
    return SeedEvent(id=row["id"], seed_id=row["seed_id"], event=row["event"], detail=detail, at=row["at"])


class SeedStore:
    def __init__(self, db: StateDB):
        self.db = db
        self.conn = db.conn

    def add_seed(
        self,
        *,
        seed_date: date | str,
        topic: str,
        source: str,
        provenance: str,
        text: str,
        score: float | None,
        model_versions: dict,
        created_at: str | None = None,
    ) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO tpot_seeds
                    (seed_date, topic, source, provenance, text, score, model_versions, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    _date_key(seed_date),
                    topic,
                    source,
                    provenance,
                    text,
                    score,
                    json.dumps(model_versions, sort_keys=True),
                    created_at or _now(),
                ),
            )
        return int(cursor.lastrowid)

    def add_candidates(
        self,
        *,
        seed_date: date | str,
        topic: Topic,
        candidates: Iterable[Candidate],
        model_versions: dict,
        created_at: str | None = None,
    ) -> list[int]:
        ids = []
        for candidate in candidates:
            ids.append(
                self.add_seed(
                    seed_date=seed_date,
                    topic=topic.text,
                    source=topic.source,
                    provenance=topic.provenance,
                    text=candidate.text,
                    score=candidate.score,
                    model_versions=model_versions,
                    created_at=created_at,
                )
            )
        return ids

    def add_grounded_batch(
        self,
        *,
        seed_date: date | str,
        generation_key: str,
        ideas: list[tuple[str, str, list[str], str]],
        scores: list[float] | None,
        model_versions: dict,
        created_at: str | None = None,
    ) -> list[int]:
        if self.conn.execute(
            "SELECT 1 FROM tpot_seeds WHERE generation_key = ? LIMIT 1", (generation_key,)
        ).fetchone():
            return []
        if scores is not None and len(scores) != len(ideas):
            raise ValueError("score count does not match grounded ideas")
        ids = []
        with self.conn:
            for order, (angle, text, evidence_keys, summary) in enumerate(ideas):
                cursor = self.conn.execute(
                    """
                    INSERT INTO tpot_seeds
                        (seed_date, topic, source, provenance, text, score, model_versions,
                         status, generation_key, evidence_keys, evidence_summary, generator,
                         candidate_order, created_at)
                    VALUES (?, ?, 'evidence', ?, ?, ?, ?, 'proposed', ?, ?, ?, 'opencode', ?, ?)
                    """,
                    (
                        _date_key(seed_date), f"synthesis:{angle}", summary, text,
                        scores[order] if scores is not None else None,
                        json.dumps(model_versions, sort_keys=True), generation_key,
                        json.dumps(evidence_keys), summary, order, created_at or _now(),
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def unscored_for_generation(self, generation_key: str) -> list[SeedRow]:
        rows = self.conn.execute(
            "SELECT * FROM tpot_seeds WHERE generation_key = ? AND score IS NULL ORDER BY candidate_order",
            (generation_key,),
        ).fetchall()
        return [_row_to_seed(row) for row in rows]

    def seeds_for_generation(self, generation_key: str) -> list[SeedRow]:
        rows = self.conn.execute(
            "SELECT * FROM tpot_seeds WHERE generation_key = ? ORDER BY candidate_order",
            (generation_key,),
        ).fetchall()
        return [_row_to_seed(row) for row in rows]

    def update_generation_scores(
        self, generation_key: str, scores: list[float], model_versions: dict
    ) -> None:
        rows = self.unscored_for_generation(generation_key)
        if len(rows) != len(scores):
            raise ValueError("score count does not match unscored seeds")
        with self.conn:
            for seed, score in zip(rows, scores, strict=True):
                versions = seed.model_versions | model_versions
                self.conn.execute(
                    "UPDATE tpot_seeds SET score = ?, model_versions = ? WHERE id = ?",
                    (score, json.dumps(versions, sort_keys=True), seed.id),
                )

    def supersede_other_proposed(
        self, seed_date: date | str, generation_key: str, *, at: str | None = None
    ) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT id FROM tpot_seeds
            WHERE seed_date = ? AND status = 'proposed'
              AND generation_key IS NOT NULL AND generation_key != ?
            ORDER BY id
            """,
            (_date_key(seed_date), generation_key),
        ).fetchall()
        ids = [row["id"] for row in rows]
        for seed_id in ids:
            self.record_event(seed_id, "superseded", {"generation_key": generation_key}, at=at)
        return ids

    def get_seed(self, seed_id: int) -> SeedRow | None:
        row = self.conn.execute("SELECT * FROM tpot_seeds WHERE id = ?", (seed_id,)).fetchone()
        return _row_to_seed(row) if row else None

    def seeds_for_date(self, seed_date: date | str) -> list[SeedRow]:
        rows = self.conn.execute(
            "SELECT * FROM tpot_seeds WHERE seed_date = ? ORDER BY id",
            (_date_key(seed_date),),
        ).fetchall()
        return [_row_to_seed(row) for row in rows]

    def topics_with_candidates(self, seed_date: date | str) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT topic FROM tpot_seeds WHERE seed_date = ?",
            (_date_key(seed_date),),
        ).fetchall()
        return {row["topic"] for row in rows}

    def missing_topics(self, seed_date: date | str, topics: Iterable[Topic]) -> list[Topic]:
        seeded = self.topics_with_candidates(seed_date)
        return [topic for topic in topics if topic.text not in seeded]

    def record_event(
        self,
        seed_id: int,
        event: str,
        detail: dict | None = None,
        *,
        at: str | None = None,
    ) -> None:
        current_status = {
            "surfaced": "surfaced",
            "used": "used",
            "discarded": "discarded",
            "remixed": "remixed",
            "expired": "expired",
            "superseded": "superseded",
        }.get(event)
        at = at or _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO tpot_seed_events (seed_id, event, detail, at) VALUES (?, ?, ?, ?)",
                (seed_id, event, json.dumps(detail, sort_keys=True) if detail is not None else None, at),
            )
            if current_status:
                self.conn.execute("UPDATE tpot_seeds SET status = ? WHERE id = ?", (current_status, seed_id))

    def mark_surfaced(
        self,
        seeds: Iterable[SeedRow],
        *,
        message_id: int,
        digest: str,
        at: str | None = None,
    ) -> None:
        for seed in seeds:
            self.record_event(
                seed.id, "surfaced", {"message_id": message_id, "digest": digest}, at=at
            )

    def events_for_seed(self, seed_id: int) -> list[SeedEvent]:
        rows = self.conn.execute(
            "SELECT * FROM tpot_seed_events WHERE seed_id = ? ORDER BY id", (seed_id,)
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def expire_stale_proposed(self, *, today: date, at: str | None = None) -> list[int]:
        cutoff = today - timedelta(days=1)
        rows = self.conn.execute(
            "SELECT id FROM tpot_seeds WHERE status = 'proposed' AND seed_date < ? ORDER BY id",
            (cutoff.isoformat(),),
        ).fetchall()
        expired = [row["id"] for row in rows]
        for seed_id in expired:
            self.record_event(seed_id, "expired", {"reason": "stale-proposed"}, at=at)
        return expired

    def recent_seen_normalized_texts(self, *, today: date, days: int = 14) -> set[str]:
        cutoff = today - timedelta(days=days)
        rows = self.conn.execute(
            """
            SELECT DISTINCT s.text
            FROM tpot_seeds s
            JOIN tpot_seed_events e ON e.seed_id = s.id AND e.event = 'surfaced'
            WHERE s.seed_date >= ? AND s.status != 'expired'
            """,
            (cutoff.isoformat(),),
        ).fetchall()
        return {normalize_seed_text(row["text"]) for row in rows}

    def select_standdown_seeds(self, seed_date: date | str, *, limit: int = 3) -> list[SeedRow]:
        rows = self.conn.execute(
            "SELECT * FROM tpot_seeds WHERE seed_date = ? AND status = 'proposed' ORDER BY id",
            (_date_key(seed_date),),
        ).fetchall()
        seeds = [_row_to_seed(row) for row in rows]
        return _select_diverse(seeds, limit=limit)

    def select_morning_carryover(self, seed_date: date | str, *, limit: int = 3) -> list[SeedRow]:
        rows = self.conn.execute(
            """
            SELECT * FROM tpot_seeds
            WHERE seed_date = ? AND status IN ('proposed', 'surfaced')
            ORDER BY id
            """,
            (_date_key(seed_date),),
        ).fetchall()
        return _select_diverse([_row_to_seed(row) for row in rows], limit=limit)


def _score(seed: SeedRow) -> float:
    return seed.score if seed.score is not None else float("-inf")


def _select_diverse(seeds: list[SeedRow], *, limit: int) -> list[SeedRow]:
    selected: list[SeedRow] = []
    seen_topics: set[str] = set()
    by_score = sorted(seeds, key=lambda seed: (_score(seed), -seed.id), reverse=True)
    for seed in by_score:
        if seed.topic in seen_topics:
            continue
        selected.append(seed)
        seen_topics.add(seed.topic)
        if len(selected) == limit:
            return selected
    selected_ids = {seed.id for seed in selected}
    for seed in by_score:
        if seed.id in selected_ids:
            continue
        selected.append(seed)
        if len(selected) == limit:
            break
    return selected


def build_reaction_keyboard(seeds: list[SeedRow]) -> dict | None:
    if not seeds:
        return None
    rows = []
    for index, seed in enumerate(seeds, start=1):
        rows.append(
            [
                {"text": f"use {index}", "callback_data": f"tpot:used:{seed.id}"},
                {"text": f"remix {index}", "callback_data": f"tpot:remix:{seed.id}"},
                {"text": f"skip {index}", "callback_data": f"tpot:discarded:{seed.id}"},
            ]
        )
    return {"inline_keyboard": rows}
