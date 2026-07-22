from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..tpot.evidence import EvidenceItem, normalize_evidence_text


class GitHubActivityError(Exception):
    pass


def _is_bot(value: str | None) -> bool:
    value = (value or "").lower()
    return value.endswith("[bot]") or value in {"dependabot", "dependabot[bot]"}


def _parse_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _normalize_event(event: dict) -> EvidenceItem | None:
    event_id = str(event.get("id") or "")
    event_type = event.get("type")
    repo = str((event.get("repo") or {}).get("name") or "unknown repository")
    payload = event.get("payload") or {}
    occurred_at = _parse_time(str(event.get("created_at")))
    title = None
    detail = None
    kind = "repository_activity"

    if event_type == "PushEvent":
        commits = []
        for commit in payload.get("commits") or []:
            author = commit.get("author") or {}
            if _is_bot(author.get("name")) or _is_bot(author.get("login")):
                continue
            subject = str(commit.get("message") or "").split("\n", 1)[0]
            subject = normalize_evidence_text(subject, limit=200)[0]
            if subject:
                commits.append(subject)
        if not commits:
            return None
        ref = str(payload.get("ref") or "").removeprefix("refs/heads/")
        title = f"Pushed {len(commits)} commit{'s' if len(commits) != 1 else ''} to {repo}"
        if ref:
            title += f" on {ref}"
        detail = "; ".join(commits[:8])
        kind = "commit"
    elif event_type in {"PullRequestEvent", "PullRequestReviewEvent"}:
        pr = payload.get("pull_request") or {}
        action = str(payload.get("action") or "updated").capitalize()
        number = pr.get("number") or payload.get("number") or "?"
        pr_title = normalize_evidence_text(str(pr.get("title") or "Untitled"), limit=300)[0]
        title = f"{action} PR #{number} in {repo}: {pr_title}"
        kind = "pull_request" if event_type == "PullRequestEvent" else "pull_request_review"
    elif event_type in {"IssuesEvent", "IssueCommentEvent"}:
        issue = payload.get("issue") or {}
        action = str(payload.get("action") or "commented").capitalize()
        issue_title = normalize_evidence_text(str(issue.get("title") or "Untitled"), limit=300)[0]
        title = f"{action} issue #{issue.get('number', '?')} in {repo}: {issue_title}"
        kind = "issue"
    elif event_type == "CreateEvent":
        title = f"Created {payload.get('ref_type', 'item')} {payload.get('ref') or ''} in {repo}".strip()
    elif event_type == "ReleaseEvent":
        release = payload.get("release") or {}
        title = f"{str(payload.get('action') or 'Published').capitalize()} release {release.get('tag_name') or ''} in {repo}".strip()
        kind = "release"
    elif event_type == "ForkEvent":
        title = f"Forked {repo}"
    else:
        return None

    title = normalize_evidence_text(title, limit=300)[0]
    return EvidenceItem(
        key=f"github:event:{event_id}",
        source="github",
        kind=kind,
        occurred_at=occurred_at,
        title=title,
        detail=detail,
        url=None,
        private=not bool(event.get("public", True)),
    )


def fetch_github_evidence(
    token: str,
    username: str,
    target_date: date,
    tz: ZoneInfo,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[EvidenceItem]:
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    overlap_start = start.astimezone(timezone.utc) - timedelta(hours=24)
    url = f"https://api.github.com/users/{username}/events"
    params = {"per_page": "100"}
    seen: set[str] = set()
    items: list[EvidenceItem] = []

    with httpx.Client(transport=transport, timeout=30) as client:
        while url:
            response = client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            params = None
            if response.status_code in {401, 403}:
                raise GitHubActivityError("GitHub Events permission or authentication failed")
            response.raise_for_status()
            events = response.json()
            for event in events:
                event_id = str(event.get("id") or "")
                if not event_id or event_id in seen or _is_bot((event.get("actor") or {}).get("login")):
                    continue
                seen.add(event_id)
                occurred_at = _parse_time(str(event.get("created_at")))
                if occurred_at < overlap_start:
                    continue
                local = occurred_at.astimezone(tz)
                if not (start <= local < end):
                    continue
                item = _normalize_event(event)
                if item:
                    items.append(item)
            next_link = response.links.get("next")
            url = next_link["url"] if next_link else ""
    return sorted(items, key=lambda item: (item.occurred_at, item.key))
