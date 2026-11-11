from __future__ import annotations

from collections import Counter

from .common import TERMINAL


HELD_STATES = frozenset({"inspection_required", "deadline_exceeded", "publication_unknown", "cancel_requested"})


def snapshot(coordinator, reports: dict, *, at: float) -> dict:
    """Describe ledger evidence only; readiness is independent of tick liveness.

    A recent tick is not proof of worker contact. Ordinary active work and an
    intentionally disabled worker do not themselves indicate an operational issue.
    Only allowlisted codes and aggregate counts leave the private ledger here.
    """
    config = coordinator.config
    last_tick = coordinator.meta("last_tick")
    last_tick = float(last_tick) if last_tick is not None else None
    tick_age = max(0, at - last_tick) if last_tick is not None else None
    live = tick_age is not None and tick_age <= config.get("health_stale_after", 900)
    failures = int(coordinator.meta("worker_connect_failures", "0"))
    retry_at = coordinator.meta("worker_retry_at")
    last_contact = coordinator.db.execute("SELECT MAX(last_contact) FROM attempts").fetchone()[0]
    recovery_hold = bool(coordinator.meta("recovery_hold"))
    counts = Counter()
    held = 0
    for row in coordinator.db.execute("""
        SELECT a.state, s.state AS stage_state, w.lifecycle_state
        FROM attempts a
        LEFT JOIN stages s ON s.active_attempt_id=a.id AND s.pr_id=a.pr_id AND s.kind=a.stage
        JOIN workflows w ON w.pr_id=a.pr_id
    """):
        issues = {row["state"], row["stage_state"]} & HELD_STATES
        if row["lifecycle_state"] == "cancel_requested" and row["state"] not in TERMINAL:
            issues.add("cancel_requested")
        if issues:
            held += 1
            counts.update(issues)
    operational = []
    if config["execution_enabled"] and failures > 0:
        operational.append("worker_transport_failures")
    if recovery_hold:
        operational.append("recovery_hold")
    if coordinator.db.execute("""
        SELECT 1 FROM meta m JOIN attempts a ON m.key='publication_rejected:' || a.id
        JOIN stages s ON s.active_attempt_id=a.id
        JOIN workflows w ON w.pr_id=a.pr_id
        WHERE a.state='ready_to_publish' AND w.lifecycle_state='active' LIMIT 1
    """).fetchone():
        operational.append("publication_rejected")
    operational.extend(sorted(counts))
    report_age = reports["oldest_pending_age"]
    if report_age is not None and report_age > config.get("report_stale_after", 900):
        operational.append("stale_pending_reports")
    return {
        "healthy": live and not operational,
        "live": live,
        "ready": not operational,
        "issue_codes": ([] if live else ["stale_tick"]) + operational,
        "execution_enabled": bool(config["execution_enabled"]),
        "tick": {"last_completed_at": last_tick, "age": tick_age},
        "worker": {
            "transport_failures": failures,
            "retry_at": float(retry_at) if retry_at is not None else None,
            "last_contact": last_contact,
            "contact_age": max(0, at - last_contact) if last_contact is not None else None,
        },
        "held_attempts": held,
        "attempt_issues": dict(sorted(counts.items())),
        "recovery_hold": recovery_hold,
        "reports": reports,
    }
