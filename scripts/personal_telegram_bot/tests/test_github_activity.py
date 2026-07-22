from datetime import date
from zoneinfo import ZoneInfo

import httpx

from personal_telegram_bot.providers.github_activity import fetch_github_evidence


TZ = ZoneInfo("Asia/Singapore")


def test_github_events_normalize_push_and_pr_without_bodies_or_bot_commits():
    events = [
        {
            "id": "1",
            "type": "PushEvent",
            "created_at": "2026-07-20T05:00:00Z",
            "actor": {"login": "0xEljh"},
            "repo": {"name": "private/engine"},
            "public": False,
            "payload": {
                "ref": "refs/heads/main",
                "commits": [
                    {"message": "Ship evidence pipeline\n\nsecret trailer", "author": {"name": "Elijah"}},
                    {"message": "Bump deps", "author": {"name": "dependabot[bot]"}},
                ],
            },
        },
        {
            "id": "2",
            "type": "PullRequestEvent",
            "created_at": "2026-07-20T06:00:00Z",
            "actor": {"login": "0xEljh"},
            "repo": {"name": "private/engine"},
            "public": False,
            "payload": {
                "action": "opened",
                "pull_request": {"number": 4, "title": "Ground daily ideas", "body": "do not ingest"},
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/0xEljh/events"
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(200, json=events)

    items = fetch_github_evidence(
        "token", "0xEljh", date(2026, 7, 20), TZ, transport=httpx.MockTransport(handler)
    )

    assert [item.key for item in items] == ["github:event:1", "github:event:2"]
    assert "Ship evidence pipeline" in (items[0].detail or "")
    assert "secret trailer" not in (items[0].detail or "")
    assert "Bump deps" not in (items[0].detail or "")
    assert items[1].title == "Opened PR #4 in private/engine: Ground daily ideas"
    assert "do not ingest" not in str(items)


def test_github_events_filter_to_target_local_day_and_dedupe_pages():
    event = {
        "id": "1",
        "type": "CreateEvent",
        "created_at": "2026-07-19T16:30:00Z",
        "actor": {"login": "0xEljh"},
        "repo": {"name": "owner/repo"},
        "public": True,
        "payload": {"ref_type": "branch", "ref": "main"},
    }
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        headers = {"Link": '<https://api.github.com/users/0xEljh/events?per_page=100&page=2>; rel="next"'} if calls == 1 else {}
        return httpx.Response(200, json=[event], headers=headers)

    items = fetch_github_evidence(
        "token", "0xEljh", date(2026, 7, 20), TZ, transport=httpx.MockTransport(handler)
    )

    assert calls == 2
    assert [item.key for item in items] == ["github:event:1"]
