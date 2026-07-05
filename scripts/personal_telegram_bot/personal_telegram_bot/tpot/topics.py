from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx


@dataclass(frozen=True)
class Topic:
    text: str
    source: str
    provenance: str


@dataclass(frozen=True)
class CompletedTodo:
    page_id: str
    title: str
    url: str | None


def _first_name(items: list[dict] | None) -> str | None:
    if not items:
        return None
    name = items[0].get("name")
    return str(name) if name else None


def parse_waka_projects(summary: dict, *, min_minutes: int = 45) -> list[dict]:
    days = summary.get("data") or []
    if not days:
        return []
    projects = []
    for project in days[0].get("projects") or []:
        name = project.get("name")
        if not name:
            continue
        minutes = float(project.get("total_seconds") or 0) / 60.0
        if minutes < min_minutes:
            continue
        projects.append(
            {
                "name": str(name),
                "minutes": minutes,
                "detail": _first_name(project.get("languages")) or _first_name(project.get("entities")),
            }
        )
    return sorted(projects, key=lambda item: item["minutes"], reverse=True)


def parse_completed_todo(page: dict) -> CompletedTodo:
    props = page.get("properties", {})
    title_parts = props.get("Name", {}).get("title", [])
    title = title_parts[0]["plain_text"] if title_parts else "Untitled"
    return CompletedTodo(page_id=str(page.get("id", "unknown")), title=title, url=page.get("url"))


def build_topics(
    waka_projects: list[dict],
    completed_todos: list[CompletedTodo],
    *,
    max_topics: int = 3,
) -> list[Topic]:
    topics: list[Topic] = []
    for project in sorted(waka_projects, key=lambda item: item["minutes"], reverse=True):
        detail = f": {project['detail']}" if project.get("detail") else ""
        topics.append(
            Topic(
                text=f"working on {project['name']}{detail}",
                source=f"waka:{project['name']}",
                provenance=f"{project['minutes'] / 60:.1f}h on {project['name']}",
            )
        )
    for todo in completed_todos:
        topics.append(
            Topic(
                text=f"completed: {todo.title}",
                source=f"todo:{todo.page_id}",
                provenance=f"✅ {todo.title}",
            )
        )
    return topics[:max_topics]


def build_completed_todos_filter(target_date: date) -> dict:
    day = target_date.isoformat()
    return {
        "and": [
            {"property": "Date", "date": {"on_or_before": day}},
            {"property": "Date", "date": {"on_or_after": day}},
            {"property": "Status", "status": {"equals": "Done"}},
        ]
    }


def fetch_waka_projects(api_key: str, target_date: date, *, min_minutes: int = 45) -> list[dict]:
    day = target_date.isoformat()
    response = httpx.get(
        "https://wakatime.com/api/v1/users/current/summaries",
        params={"start": day, "end": day, "api_key": api_key},
        timeout=30,
    )
    response.raise_for_status()
    return parse_waka_projects(response.json(), min_minutes=min_minutes)


def fetch_completed_todos(notion_token: str, datasource_id: str, target_date: date) -> list[CompletedTodo]:
    from notion_client import Client

    notion = Client(auth=notion_token)
    results: list[dict] = []
    cursor = None
    while True:
        kwargs = {"data_source_id": datasource_id, "filter": build_completed_todos_filter(target_date)}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = notion.data_sources.query(**kwargs)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return [parse_completed_todo(page) for page in results]


def collect_topics(cfg, target_date: date) -> list[Topic]:
    waka_projects: list[dict] = []
    completed_todos: list[CompletedTodo] = []
    if cfg.wakatime_api_key:
        try:
            waka_projects = fetch_waka_projects(
                cfg.wakatime_api_key,
                target_date,
                min_minutes=cfg.tpot_waka_min_minutes,
            )
        except Exception:
            waka_projects = []
    if cfg.notion_token and cfg.bread_datasource_id:
        try:
            completed_todos = fetch_completed_todos(cfg.notion_token, cfg.bread_datasource_id, target_date)
        except Exception:
            completed_todos = []
    return build_topics(waka_projects, completed_todos)
