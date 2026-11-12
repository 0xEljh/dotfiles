"""Owned-clone Git operations. Nothing in this module selects a developer checkout."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .common import Blocked, PreflightRejected


def sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value):
        raise Blocked("a full immutable commit SHA is required")
    return value


def ref(value: str) -> str:
    if not isinstance(value, str) or subprocess.run(
        ["git", "check-ref-format", "refs/heads/" + value], capture_output=True
    ).returncode:
        raise Blocked("invalid Git branch ref")
    return "refs/heads/" + value


def repository_url(repo: dict) -> str:
    name = repo.get("full_name", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise Blocked("invalid GitHub repository identity")
    expected = "https://github.com/" + name + ".git"
    if repo.get("clone_url", expected) != expected:
        raise Blocked("repository remote does not match GitHub identity")
    return expected


def current_base_snapshot(pr: dict, evidence: dict) -> dict:
    """Derive an execution snapshot from the exact live base ref, not PR history."""
    try:
        if (not isinstance(evidence, dict) or not isinstance(evidence.get("object"), dict)
                or evidence.get("ref") != "refs/heads/" + pr["base"]["ref"]
                or evidence["object"].get("type") != "commit"):
            raise Blocked("live base-ref response does not identify the expected commit ref")
        observed = sha(evidence["object"]["sha"])
        return dict(pr, base=dict(pr["base"], sha=observed))
    except (KeyError, TypeError, ValueError) as error:
        raise Blocked("live base-ref response is incomplete; no execution snapshot accepted") from error


def validate_live_pr(expected: dict, live: dict, repository: dict,
                     author_id: int = 34589654, organization_id: int = 97932676) -> None:
    try:
        if (live["id"] != expected["id"] or live["state"] != "open" or live.get("draft", True)
                or live.get("merged", False) or live["user"]["id"] != author_id
                or live["base"]["repo"]["owner"]["id"] != organization_id):
            raise PreflightRejected("PR lifecycle, author, or organization no longer eligible")
        for side in ("head", "base"):
            if (live[side]["repo"]["id"] != expected[side]["repo"]["id"]
                    or live[side]["repo"]["full_name"] != expected[side]["repo"]["full_name"]
                    or live[side]["ref"] != expected[side]["ref"]):
                raise Blocked("PR repository identity or target ref changed")
            repository_url(live[side]["repo"])
            ref(live[side]["ref"])
        if live["head"]["repo"]["id"] != live["base"]["repo"]["id"]:
            raise Blocked("fork head needs explicit write enrollment")
        if repository["id"] != live["head"]["repo"]["id"]:
            raise Blocked("default-branch lookup identity changed")
        if live["head"]["ref"] in (live["base"]["ref"], repository["default_branch"]):
            raise Blocked("publication to default or base branch is forbidden")
    except (KeyError, TypeError) as error:
        raise Blocked("incomplete live GitHub identity; inspect enrollment") from error


class GitOps:
    def __init__(self, path: Path, remote: str):
        self.path = Path(path)
        self.remote = remote

    def run(self, *args: str, check: bool = True, input: bytes | None = None,
            index_file: Path | None = None) -> subprocess.CompletedProcess:
        # -C alone does not override GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE.
        # Helpers may be invoked from an agent shell inside another Git command.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
        if index_file is not None:
            env["GIT_INDEX_FILE"] = str(index_file)
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "submodule.recurse=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", str(self.path), *args],
            input=input, capture_output=True, env=env, timeout=180,
        )
        if check and result.returncode:
            # Raw Git output can contain repository text or credentials. Keep it private.
            raise Blocked("Git operation failed: " + args[0] + "; inspect owned checkout")
        return result

    def fetch(self, branch: str, slot: str) -> str:
        if slot not in ("input", "base", "observed"):
            raise ValueError("unknown local fetch slot")
        target = "refs/pr-maintenance/" + slot
        self.run("fetch", "--no-tags", "--", "origin", "+" + ref(branch) + ":" + target)
        return sha(self.run("rev-parse", "--verify", target).stdout.decode().strip())

    def prepare(self, head_ref: str, head_sha: str, base_ref: str, base_sha: str,
                *, before_checkout: Callable[[], None] | None = None) -> None:
        sha(head_sha)
        sha(base_sha)
        # Existing directories are retained, never reset, cleaned, or reused.
        self.path.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.run("init", "--quiet")
        self.run("remote", "add", "origin", self.remote)
        if self.fetch(head_ref, "input") != head_sha:
            raise PreflightRejected("head advanced before checkout preparation")
        if self.fetch(base_ref, "base") != base_sha:
            raise PreflightRejected("base advanced before checkout preparation")
        if before_checkout is not None:
            before_checkout()
        self.run("checkout", "--detach", head_sha, "--")

    def assert_remote(self) -> None:
        actual = self.run("remote", "get-url", "--push", "--all", "origin").stdout.decode().splitlines()
        fetch_url = self.run("remote", "get-url", "--all", "origin").stdout.decode().splitlines()
        if actual != [self.remote] or fetch_url != [self.remote]:
            raise Blocked("owned checkout remote changed")

    def head(self) -> str:
        return sha(self.run("rev-parse", "--verify", "HEAD").stdout.decode().strip())

    def clean(self) -> bool:
        status = ("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if self.run("diff-index", "--cached", "--no-ext-diff", "--no-textconv", "--name-only", "-z", "HEAD", "--").stdout:
            return False
        # A fresh index checks tracked contents without trusting skip-worktree,
        # assume-unchanged, or cached stat data. Leave the real index untouched.
        with tempfile.TemporaryDirectory(prefix=".verify-index-", dir=self.path.parent) as directory:
            index = Path(directory) / "index"
            options = ("-c", "core.sparseCheckout=false", "-c", "core.splitIndex=false", "-c", "core.ignorestat=false")
            self.run(*options, "read-tree", "--no-sparse-checkout", "HEAD", index_file=index)
            return not self.run(*options, *status, index_file=index).stdout

    def changed_paths(self, start: str, end: str) -> list[str]:
        return [os.fsdecode(value) for value in self.run(
            "diff", "--name-only", "--no-renames", "-z", sha(start), sha(end), "--"
        ).stdout.split(b"\0") if value]

    def diff(self, start: str, end: str) -> str:
        return self.run("diff", "--no-ext-diff", "--no-textconv", sha(start), sha(end), "--").stdout.decode("utf-8", "replace")

    def ancestor(self, before: str, after: str) -> bool:
        result = self.run("merge-base", "--is-ancestor", sha(before), sha(after), check=False)
        if result.returncode not in (0, 1):
            raise Blocked("unable to establish candidate ancestry")
        return result.returncode == 0

    def begin_conflict_merge(self, head: str, base: str) -> bool:
        result = self.run("merge-tree", "--write-tree", sha(head), sha(base), check=False)
        if result.returncode == 0:
            return False
        if result.returncode != 1:
            raise Blocked("actual merge check unavailable; Git toolchain onboarding required")
        merge = self.run("merge", "--no-commit", "--no-ff", sha(base), check=False)
        if merge.returncode != 1 or not self.run("ls-files", "--unmerged", "-z").stdout:
            raise Blocked("conflict merge did not produce an inspectable unresolved index")
        return True

    def check_candidate(self, start: str, candidate: str, base: str | None = None) -> list[str]:
        if self.run("ls-files", "--unmerged", "-z").stdout:
            raise Blocked("unresolved index entries retained; publication forbidden")
        if not self.clean() or self.head() != sha(candidate):
            raise Blocked("unexplained edits or checkout does not match candidate")
        if not self.ancestor(start, candidate):
            raise Blocked("candidate does not preserve the input history")
        if base:
            parents = self.run("rev-list", "--parents", "-n", "1", sha(candidate)).stdout.decode().split()[1:]
            if parents != [sha(start), sha(base)]:
                raise Blocked("conflict candidate must be a merge preserving both exact parents")
        elif self.run("rev-list", "--min-parents=2", sha(start) + ".." + sha(candidate), "--").stdout:
            raise Blocked("merge commits are reserved for the final conflict stage")
        diff = self.run("diff", "--no-ext-diff", "--no-textconv", "--unified=0", sha(start), sha(candidate), "--").stdout
        if re.search(rb"(?m)^\+(?:<{7}|={7}|>{7})(?:[ \t\r\n]|$)", diff):
            raise Blocked("introduced conflict markers retained; publication forbidden")
        return self.changed_paths(start, candidate)

    def observed_head(self, branch: str) -> str:
        self.assert_remote()
        return self.fetch(branch, "observed")

    def publication(self, branch: str, start: str, candidate: str) -> str:
        observed = self.observed_head(branch)
        if self.ancestor(candidate, observed):
            return "already_published"
        if observed != sha(start):
            raise Blocked("head advanced; retain candidate for explicit fresh attempt")
        return "unpublished"

    def closed_pr_nonpublication(self, number: int, head_branch: str, base_branch: str,
                                 start: str, base_head: str, candidate: str) -> dict:
        """Observe an unchanged retained PR head and current base, never publish."""
        if type(number) is not int or number <= 0:
            raise Blocked("closed PR recovery requires a numeric PR number")
        start, base_head, candidate = sha(start), sha(base_head), sha(candidate)
        head_ref, base_ref = ref(head_branch), ref(base_branch)

        def require_missing_branch():
            self.assert_remote()
            # A failed authenticated lookup is not an absent branch. ref() rejects
            # patterns; any returned record means absence was not established.
            listing = self.run("ls-remote", "--heads", "--", "origin", head_ref)
            if listing.stdout.strip():
                raise Blocked("head branch exists or was recreated; ownership retained")

        require_missing_branch()
        prefix = "refs/pr-maintenance/recovery/" + str(number)
        self.run("fetch", "--no-tags", "--", "origin", "+refs/pull/" + str(number) + "/head:" + prefix + "/head")
        observed_head = sha(self.run("rev-parse", "--verify", prefix + "/head").stdout.decode().strip())
        if observed_head != start:
            raise Blocked("retained PR head changed; candidate publication remains unresolved")
        self.run("fetch", "--no-tags", "--", "origin", "+" + base_ref + ":" + prefix + "/base")
        observed_base = sha(self.run("rev-parse", "--verify", prefix + "/base").stdout.decode().strip())
        if observed_base != base_head:
            raise Blocked("base changed during closed PR recovery; inspect and retry")
        if self.ancestor(candidate, observed_head) or self.ancestor(candidate, observed_base):
            raise Blocked("candidate is reachable from retained PR head or base; ownership retained")
        refs_observed_at = time.time()
        require_missing_branch()
        return {"source": "closed_pr_head", "pr_number": number, "head_branch_missing": True,
                "head_sha": observed_head, "base_sha": observed_base,
                "refs_observed_at": refs_observed_at, "branch_absence_observed_at": time.time()}

    def publish(self, branch: str, start: str, candidate: str, *, prechecked: bool = False) -> str:
        sha(candidate)
        self.assert_remote()
        if not self.ancestor(start, candidate):
            raise Blocked("candidate does not preserve the input history")
        if not prechecked and self.publication(branch, start, candidate) == "already_published":
            return "already_published"
        result = self.run("push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no", "--", "origin", sha(candidate) + ":" + ref(branch), check=False)
        # Inspect even a rejected/lost response: the update may already be durable.
        if self.publication(branch, start, candidate) == "already_published":
            return "published"
        if result.returncode:
            raise Blocked("push not confirmed; retained candidate requires reconciliation")
        raise Blocked("push response did not establish remote reachability")
