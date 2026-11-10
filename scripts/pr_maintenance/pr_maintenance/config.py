from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .common import Blocked, STAGES


DEFAULTS = {
    "organization_id": 97932676,
    "organization": "EigenformAI",
    "author_id": 34589654,
    "database": "/var/lib/pr-maintenance/coordinator.sqlite3",
    "worker_host": "contents-may-differ",
    "worker_user": "elijah",
    "worker_executable": "/run/current-system/sw/bin/pr-worker",
    "worker_config": "/etc/pr-maintenance/worker.json",
    "known_hosts": "/etc/pr-maintenance/known_hosts",
    "feedback_delay": 900,
    "delay_notice_after": 600,
    "stage_deadline": 2700,
    "verification_deadline": 900,
    "review_deadline": 900,
    "infrastructure_failures": 3,
    "correction_rounds": 2,
    "publish_ttl": 60,
    "stale_after": None,
    "api_budget": 300,
    "scan_overlap": 600,
    "github_auth": "app",
    "personal_auth_acknowledged": False,
    "allow_repositories": [],
    "allow_repository_ids": [],
    "allow_pr_ids": [],
    "organization_wide": False,
    "private_repositories_only": False,
    "recent_interaction_days": None,
    "execution_enabled": False,
    "rollout_acknowledged": False,
    "models": {},
    "agents": {},
    "verification_profiles": {},
}


def validate_github(config: dict) -> None:
    """Validate authentication and scope even when execution is disabled."""
    if config.get("github_auth", "app") not in ("app", "gh"):
        raise ValueError("github_auth must be app or gh; authentication never falls back")
    days = config.get("recent_interaction_days")
    if days is not None:
        if type(days) not in (int, float) or not math.isfinite(days) or days <= 0:
            raise ValueError("recent_interaction_days must be finite and positive, or null to disable")
        if config.get("github_auth", "app") != "gh":
            raise Blocked("recent_interaction_days requires personal gh authentication for private account events")
    for field in ("organization_id", "author_id"):
        value = config.get(field, DEFAULTS[field])
        if type(value) is not int or value <= 0:
            raise ValueError(f"{field} must be a positive numeric GitHub ID")
    organization = config.get("organization", DEFAULTS["organization"])
    if not isinstance(organization, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", organization):
        raise ValueError("organization must be a safe GitHub organization name")
    for field in ("organization_wide", "private_repositories_only"):
        if type(config.get(field, False)) is not bool:
            raise ValueError(f"{field} must be a boolean")
    names = config.get("allow_repositories", [])
    if not isinstance(names, list) or any(not isinstance(name, str) or len(name.split("/")) != 2 or any(
            part in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
            for part in name.split("/")) for name in names):
        raise ValueError("allow_repositories must contain owner/name repository names")
    for field in ("allow_repository_ids", "allow_pr_ids"):
        values = config.get(field, [])
        if not isinstance(values, list) or any(type(value) is not int or value <= 0 for value in values):
            raise ValueError(f"{field} must be a list of positive numeric GitHub IDs")
    if config.get("github_auth", "app") == "gh":
        if config.get("personal_auth_acknowledged") is not True:
            raise Blocked("personal GitHub credential exposure requires personal_auth_acknowledged")
        if config.get("organization_wide", False):
            if not config.get("private_repositories_only", False):
                raise Blocked("personal organization-wide discovery requires private_repositories_only")
            return
        if not names:
            raise Blocked("personal GitHub authentication requires explicit allow_repositories")
        for field in ("allow_repository_ids", "allow_pr_ids"):
            if not config.get(field):
                raise Blocked(f"personal GitHub authentication requires explicit {field}")


def load(path: str | Path) -> dict:
    supplied = json.loads(Path(path).read_text())
    config = DEFAULTS | supplied
    validate_github(config)
    for field in ("feedback_delay", "delay_notice_after", "stage_deadline", "verification_deadline", "review_deadline", "publish_ttl", "api_budget", "scan_overlap"):
        value = config[field]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{field} must be finite and positive")
    if config["execution_enabled"]:
        if not config["rollout_acknowledged"]:
            raise Blocked("shared-instance credential exposure requires rollout acknowledgement")
        if any(not config["models"].get(stage) for stage in (*STAGES, "review")):
            raise Blocked("configure explicit models for every stage and review")
        if any(not config["agents"].get(stage) for stage in (*STAGES, "review")):
            raise Blocked("configure inspected stage and review agent definitions")
        if not config["organization_wide"] and not config["allow_repositories"]:
            raise Blocked("pilot requires an explicit repository allowlist")
    return config
