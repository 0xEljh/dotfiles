from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from ..config import Config
from ..providers.github_activity import fetch_github_evidence
from ..providers.notion_evidence import fetch_notion_evidence
from .evidence import EvidenceItem
from .topics import fetch_waka_projects


@dataclass(frozen=True)
class EvidenceCollection:
    items: list[EvidenceItem]
    provider_status: dict[str, str]


def collect_evidence(cfg: Config, target_date: date) -> EvidenceCollection:
    items: list[EvidenceItem] = []
    statuses: dict[str, str] = {}

    if cfg.github_activity_token:
        try:
            github = fetch_github_evidence(
                cfg.github_activity_token, cfg.github_username, target_date, cfg.tz
            )
            items.extend(github)
            statuses["github"] = "ok_nonempty" if github else "ok_empty"
        except Exception as exc:
            statuses["github"] = f"error:{type(exc).__name__}"
    else:
        statuses["github"] = "config_error"

    if cfg.notion_token and cfg.bread_datasource_id:
        try:
            notion = fetch_notion_evidence(
                cfg.notion_token, cfg.bread_datasource_id, target_date, cfg.tz
            )
            items.extend(notion)
            statuses["notion"] = "ok_nonempty" if notion else "ok_empty"
        except Exception as exc:
            statuses["notion"] = f"error:{type(exc).__name__}"
    else:
        statuses["notion"] = "config_error"

    if cfg.wakatime_api_key:
        try:
            projects = fetch_waka_projects(
                cfg.wakatime_api_key, target_date, min_minutes=cfg.tpot_waka_min_minutes
            )
            occurred_at = datetime.combine(target_date, time(12), tzinfo=cfg.tz)
            waka = [
                EvidenceItem(
                    key=f"wakatime:project:{project['name']}",
                    source="wakatime",
                    kind="work_session",
                    occurred_at=occurred_at,
                    title=f"Worked on {project['name']} for {project['minutes'] / 60:.1f}h",
                    detail=f"top language/entity: {project['detail']}" if project.get("detail") else None,
                    url=None,
                    private=True,
                )
                for project in projects
            ]
            items.extend(waka)
            statuses["wakatime"] = "ok_nonempty" if waka else "ok_empty"
        except Exception as exc:
            statuses["wakatime"] = f"error:{type(exc).__name__}"
    else:
        statuses["wakatime"] = "config_error"
    return EvidenceCollection(items, statuses)
