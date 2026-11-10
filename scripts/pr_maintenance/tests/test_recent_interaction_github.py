import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.config import load
from pr_maintenance.github import GitHub, GitHubError
from test_github import Response, Transport, personal_config


AUTHOR = 34589654
NOW = 1_800_000_000
SINCE = NOW - 7 * 86400


def event(repo=77, at=NOW, actor=AUTHOR, kind="PushEvent"):
    return {"type": kind, "actor": {"id": actor}, "repo": {"id": repo},
            "created_at": datetime.fromtimestamp(at, timezone.utc).isoformat()}


def activity_client(*responses):
    transport = Transport(Response({"id": AUTHOR, "login": "0xEljh"}), *responses)
    github = GitHub(personal_config(recent_interaction_days=7), opener=transport, clock=lambda: NOW)
    github._token = "host-local-token"
    return github, transport


@pytest.mark.parametrize("kind", [
    "PushEvent", "IssuesEvent", "IssueCommentEvent", "PullRequestEvent",
    "PullRequestReviewEvent", "PullRequestReviewCommentEvent", "CommitCommentEvent",
])
def test_contribution_events_require_matching_actor_and_inclusive_seven_day_window(kind):
    github, transport = activity_client(Response([
        event(77, SINCE, kind=kind), event(78, NOW, kind=kind),
        event(79, SINCE - 1, kind=kind), event(80, NOW + 1, kind=kind),
        event(81, actor=AUTHOR + 1, kind=kind), event(82, actor=str(AUTHOR), kind=kind),
        event(83, kind="WatchEvent"), event(84, kind="ForkEvent"),
        event(85, kind="PublicEvent"), event(86, kind="UnknownEvent"),
    ]))
    assert github.recent_interactions(SINCE, NOW) == {77, 78}
    assert [request.full_url for request in transport.requests] == [
        "https://api.github.com/user",
        "https://api.github.com/users/0xEljh/events?per_page=100",
    ]
    assert all(request.get_method() == "GET" for request in transport.requests)


def test_activity_reads_later_pages_after_old_events_and_stops_at_300_entries():
    old = [event(90, SINCE - 1)] * 100
    github, transport = activity_client(
        Response(old, {"Link": '</users/0xEljh/events?per_page=100&page=2>; rel="next"'}),
        Response([event(77)] * 100, {"Link": '</users/0xEljh/events?per_page=100&page=3>; rel="next"'}),
        Response([event(78)] * 100, {"Link": '</users/0xEljh/events?per_page=100&page=4>; rel="next"'}),
        Response([event(79)]),
    )
    assert github.recent_interactions(SINCE, NOW) == {77, 78}
    assert len(transport.requests) == 4  # Identity plus three pages, never page four.


def test_activity_request_cap_applies_even_to_short_pages():
    github, transport = activity_client(*[
        Response([event(77 + page)], {"Link": f'</users/0xEljh/events?per_page=100&page={page + 1}>; rel="next"'})
        for page in range(1, 5)
    ])
    assert github.recent_interactions(SINCE, NOW) == {78, 79, 80}
    assert len(transport.requests) == 4


def test_activity_accepts_github_numeric_pagination_alias_for_verified_account_only():
    github, transport = activity_client(
        Response([event(77)], {"Link": f'</user/{AUTHOR}/events?per_page=100&page=2>; rel="next"'}),
        Response([event(78)]),
    )
    assert github.recent_interactions(SINCE, NOW) == {77, 78}
    assert transport.requests[-1].full_url == f"https://api.github.com/user/{AUTHOR}/events?per_page=100&page=2"


@pytest.mark.parametrize("target", [
    f"/user/{AUTHOR + 1}/events?per_page=100&page=2",
    "/users/Other/events?per_page=100&page=2",
    "/users/0xEljh/received_events?per_page=100&page=2",
    "/users/0xEljh/events/public?per_page=100&page=2",
    "/users/0xEljh/events?per_page=100&page=99",
    "/users/0xEljh/events?per_page=1&page=2",
    "/users/0xEljh/events?per_page=100&page=2&page=3",
    "https://evil.test/users/0xEljh/events?per_page=100&page=2",
])
def test_activity_pagination_cannot_change_account_endpoint_or_window(target):
    github, transport = activity_client(Response([event()], {"Link": f'<{target}>; rel="next"'}))
    with pytest.raises(GitHubError):
        github.recent_interactions(SINCE, NOW)
    assert len(transport.requests) == 2


@pytest.mark.parametrize("bad", [
    None, event(repo=True), event(repo="77"), event(repo=0), event() | {"created_at": "invalid"},
    event() | {"created_at": "2026-11-10T00:00:00"},
])
def test_malformed_contribution_does_not_return_a_partial_selection(bad):
    github, _ = activity_client(Response([event(), bad]))
    with pytest.raises(GitHubError):
        github.recent_interactions(SINCE, NOW)


def test_activity_oversized_page_is_not_accepted():
    github, _ = activity_client(Response([event()] * 101))
    with pytest.raises(GitHubError):
        github.recent_interactions(SINCE, NOW)


def test_failed_later_activity_page_does_not_return_earlier_evidence():
    github, _ = activity_client(
        Response([event()], {"Link": f'</user/{AUTHOR}/events?per_page=100&page=2>; rel="next"'}),
        GitHubError("GitHub API transport failed"),
    )
    with pytest.raises(GitHubError, match="transport failed"):
        github.recent_interactions(SINCE, NOW)


def test_evidence_disappearing_from_the_feed_does_not_remain_eligible():
    github, _ = activity_client(Response([event()]), Response([]))
    assert github.recent_interactions(SINCE, NOW) == {77}
    assert github.recent_interactions(SINCE, NOW) == set()


@pytest.mark.parametrize("identity", [
    {"id": AUTHOR + 1, "login": "0xEljh"}, {"id": AUTHOR},
    {"id": AUTHOR, "login": "../Other"}, {"id": AUTHOR, "login": ""},
])
def test_activity_endpoint_comes_only_from_verified_account(identity):
    transport = Transport(Response(identity))
    github = GitHub(personal_config(recent_interaction_days=7), opener=transport)
    github._token = "host-local-token"
    with pytest.raises(GitHubError):
        github.recent_interactions(SINCE, NOW)
    assert len(transport.requests) == 1


def test_arbitrary_activity_endpoint_is_rejected_before_reading_credentials():
    github, transport = activity_client()
    with patch("pr_maintenance.github.subprocess.run") as run, pytest.raises(GitHubError):
        github.get("/users/Other/events", {"per_page": 100})
    run.assert_not_called()
    assert transport.requests == []


@pytest.mark.parametrize("days", [True, False, 0, -1, "7", [], float("nan"), float("inf")])
def test_invalid_activity_windows_are_rejected_for_disabled_execution(tmp_path, days):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(personal_config(recent_interaction_days=days)))
    with pytest.raises(ValueError, match="recent_interaction_days"):
        load(path)


def test_activity_filter_requires_personal_authentication():
    with pytest.raises(Blocked, match="recent_interaction_days"):
        GitHub({"github_app_id": 12, "github_installation_id": 34, "recent_interaction_days": 7})
