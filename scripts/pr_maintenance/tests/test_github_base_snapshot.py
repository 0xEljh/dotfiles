import copy

import pytest

from pr_maintenance.github import GitHubError, RequestBudgetExceeded
from test_coordinator import pr
from test_github import Response, client


def ref_snapshot(branch="main", sha="c" * 40):
    return {"ref": "refs/heads/" + branch, "object": {"type": "commit", "sha": sha}}


def test_ready_pr_uses_current_base_ref_instead_of_stale_pr_payload():
    payload = pr()
    original = copy.deepcopy(payload)
    github, transport = client(Response(payload), Response(ref_snapshot()))
    result = github.get_pr("EigenformAI/test", 1)
    assert result["base"]["sha"] == "c" * 40
    assert result["head"] == original["head"]
    assert payload == original
    assert [request.full_url for request in transport.requests] == [
        "https://api.github.com/repos/EigenformAI/test/pulls/1",
        "https://api.github.com/repos/EigenformAI/test/git/ref/heads/main",
    ]


@pytest.mark.parametrize("evidence", [None, {}, {"ref": "refs/heads/main", "object": None},
    ref_snapshot("other"), {"ref": "refs/heads/main", "object": {"type": "tag", "sha": "c" * 40}},
    ref_snapshot(sha="short")])
def test_unverified_base_ref_never_falls_back_to_stale_pr_payload(evidence):
    github, _ = client(Response(pr()), Response(evidence))
    with pytest.raises(GitHubError):
        github.get_pr("EigenformAI/test", 1)


def test_budget_exhaustion_during_base_lookup_does_not_return_old_snapshot():
    github, transport = client(Response(pr()), budget=1)
    with pytest.raises(RequestBudgetExceeded):
        github.get_pr("EigenformAI/test", 1)
    assert len(transport.requests) == 1


@pytest.mark.parametrize("change", [{"state": "closed", "merged": True}, {"draft": True}])
def test_closed_or_draft_pr_does_not_need_an_execution_base_snapshot(change):
    payload = pr() | change
    github, transport = client(Response(payload))
    assert github.get_pr("EigenformAI/test", 1) == payload
    assert len(transport.requests) == 1


def test_base_lookup_stays_on_the_requested_repository():
    payload = pr()
    payload["base"]["repo"]["full_name"] = "Other/repository"
    github, transport = client(Response(payload))
    with pytest.raises(GitHubError):
        github.get_pr("EigenformAI/test", 1)
    assert len(transport.requests) == 1


def test_nested_base_branch_is_read_as_the_exact_ref():
    payload = pr()
    payload["base"]["ref"] = "release/v1"
    github, transport = client(Response(payload), Response(ref_snapshot("release/v1")))
    assert github.get_pr("EigenformAI/test", 1)["base"]["sha"] == "c" * 40
    assert transport.requests[-1].full_url.endswith("/git/ref/heads/release/v1")
