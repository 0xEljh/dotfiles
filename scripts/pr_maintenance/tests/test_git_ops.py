import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pr_maintenance.common import Blocked
from pr_maintenance.git_ops import GitOps, validate_live_pr


def git(path, *args):
    return subprocess.check_output(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", str(path), *args], stderr=subprocess.DEVNULL).decode().strip()


class Repository(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin.git"
        self.seed = self.root / "seed"
        subprocess.run(["git", "init", "--bare", str(self.origin)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(self.seed)], check=True, capture_output=True)
        git(self.seed, "config", "user.name", "Test")
        git(self.seed, "config", "user.email", "test@example.test")
        (self.seed / "code.txt").write_text("original\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "initial")
        git(self.seed, "remote", "add", "origin", str(self.origin))
        git(self.seed, "push", "origin", "main")
        git(self.seed, "checkout", "-b", "topic")
        (self.seed / "topic.txt").write_text("topic\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "topic")
        git(self.seed, "push", "origin", "topic")
        self.head = git(self.seed, "rev-parse", "HEAD")
        self.base = git(self.seed, "rev-parse", "main")
        self.ops = GitOps(self.root / "owned", str(self.origin))
        self.ops.prepare("topic", self.head, "main", self.base)
        git(self.ops.path, "config", "user.name", "Test")
        git(self.ops.path, "config", "user.email", "test@example.test")

    def candidate(self):
        (self.ops.path / "topic.txt").write_text("maintained\n")
        git(self.ops.path, "add", ".")
        git(self.ops.path, "commit", "-m", "maintenance")
        return git(self.ops.path, "rev-parse", "HEAD")


class WorktreeIntegrityTests(Repository):
    def test_hidden_edits_are_not_clean_or_a_valid_candidate(self):
        path = self.ops.path / "topic.txt"
        index = self.ops.path / ".git" / "index"
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                git(self.ops.path, "update-index", "--" + flag, "topic.txt")
                original_index = index.read_bytes()
                try:
                    self.assertTrue(self.ops.clean())
                    path.write_text("other\n")
                    self.assertFalse(self.ops.clean())
                    with self.assertRaisesRegex(Blocked, "unexplained edits"):
                        self.ops.check_candidate(self.head, self.head)
                    self.assertEqual(path.read_text(), "other\n")
                    self.assertEqual(index.read_bytes(), original_index)
                finally:
                    path.write_text("topic\n")
                    git(self.ops.path, "update-index", "--no-" + flag, "topic.txt")

    def test_staged_edits_are_dirty_even_when_working_contents_match_head(self):
        path = self.ops.path / "topic.txt"
        path.write_text("staged\n")
        git(self.ops.path, "add", "topic.txt")
        path.write_text("topic\n")
        self.assertFalse(self.ops.clean())

    def test_git_equivalent_line_endings_remain_clean(self):
        (self.ops.path / ".gitattributes").write_text("*.txt text eol=crlf\n")
        git(self.ops.path, "add", ".gitattributes")
        git(self.ops.path, "commit", "-m", "declare line endings")
        (self.ops.path / "topic.txt").write_bytes(b"topic\r\n")
        self.assertTrue(self.ops.clean())


class PublicationTests(Repository):
    def test_normal_publication_and_lost_ack_reconcile(self):
        candidate = self.candidate()
        self.assertEqual(self.ops.publish("topic", self.head, candidate), "published")
        self.assertEqual(self.ops.publish("topic", self.head, candidate), "already_published")
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), candidate)

    def test_concurrent_head_update_is_never_overwritten(self):
        candidate = self.candidate()
        (self.seed / "human.txt").write_text("human\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "human")
        git(self.seed, "push", "origin", "topic")
        moved = git(self.seed, "rev-parse", "HEAD")
        with self.assertRaisesRegex(Blocked, "head advanced"):
            self.ops.publish("topic", self.head, candidate)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), moved)

    def test_human_push_between_preflight_and_push_is_rejected_by_git(self):
        candidate = self.candidate()
        original_run = self.ops.run
        moved = []
        def racing_run(*args, **kwargs):
            if args[0] == "push":
                (self.seed / "human.txt").write_text("concurrent human change\n")
                git(self.seed, "add", ".")
                git(self.seed, "commit", "-m", "human race")
                git(self.seed, "push", "origin", "topic")
                moved.append(git(self.seed, "rev-parse", "HEAD"))
            return original_run(*args, **kwargs)
        with patch.object(self.ops, "run", side_effect=racing_run), self.assertRaises(Blocked):
            self.ops.publish("topic", self.head, candidate)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), moved[0])

    def test_clean_divergence_is_noop_without_merge_commit(self):
        git(self.seed, "checkout", "main")
        (self.seed / "base.txt").write_text("new base\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "base")
        git(self.seed, "push", "origin", "main")
        base = git(self.seed, "rev-parse", "HEAD")
        self.ops.fetch("main", "base")
        self.assertFalse(self.ops.begin_conflict_merge(self.head, base))
        self.assertEqual(git(self.ops.path, "rev-parse", "HEAD"), self.head)
        self.assertTrue(self.ops.clean())

    def test_conflicting_merge_retains_index_and_rejects_markers(self):
        (self.seed / "code.txt").write_text("topic intent\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "topic conflict")
        git(self.seed, "push", "origin", "topic")
        head = git(self.seed, "rev-parse", "HEAD")
        git(self.seed, "checkout", "main")
        (self.seed / "code.txt").write_text("base intent\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "base conflict")
        git(self.seed, "push", "origin", "main")
        base = git(self.seed, "rev-parse", "HEAD")
        other = GitOps(self.root / "conflict", str(self.origin))
        other.prepare("topic", head, "main", base)
        git(other.path, "config", "user.name", "Test")
        git(other.path, "config", "user.email", "test@example.test")
        self.assertTrue(other.begin_conflict_merge(head, base))
        with self.assertRaisesRegex(Blocked, "unresolved"):
            other.check_candidate(head, head, base)
        git(other.path, "add", "code.txt")
        git(other.path, "commit", "-m", "bad resolution")
        with self.assertRaisesRegex(Blocked, "conflict markers"):
            other.check_candidate(head, git(other.path, "rev-parse", "HEAD"), base)

    def test_nul_paths_are_literal(self):
        name = "-odd\nname.txt"
        (self.ops.path / name).write_text("content\n")
        git(self.ops.path, "add", "--", name)
        git(self.ops.path, "commit", "-m", "unusual path")
        self.assertEqual(self.ops.changed_paths(self.head, git(self.ops.path, "rev-parse", "HEAD")), [name])

    def test_multiple_push_urls_are_rejected(self):
        candidate = self.candidate()
        git(self.ops.path, "config", "--add", "remote.origin.pushurl", str(self.origin))
        git(self.ops.path, "config", "--add", "remote.origin.pushurl", str(self.root / "unintended.git"))
        with self.assertRaisesRegex(Blocked, "remote changed"):
            self.ops.publish("topic", self.head, candidate)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/topic"), self.head)

    def test_inherited_git_environment_cannot_redirect_owned_checkout(self):
        candidate = self.candidate()
        with patch.dict(os.environ, {"GIT_DIR": str(self.seed / ".git"), "GIT_WORK_TREE": str(self.seed)}):
            self.assertEqual(self.ops.head(), candidate)

    def test_merge_commit_only_permitted_with_exact_conflict_parent_contract(self):
        git(self.seed, "checkout", "main")
        (self.seed / "base.txt").write_text("base change\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "base advance")
        git(self.seed, "push", "origin", "main")
        base = git(self.seed, "rev-parse", "HEAD")
        self.ops.fetch("main", "base")
        git(self.ops.path, "merge", "--no-ff", base, "-m", "merge")
        candidate = self.ops.head()
        with self.assertRaisesRegex(Blocked, "merge commits"):
            self.ops.check_candidate(self.head, candidate)
        self.assertEqual(self.ops.check_candidate(self.head, candidate, base), ["base.txt"])


def pr_payload():
    repo = {"id": 10, "full_name": "EigenformAI/example", "owner": {"id": 97932676}, "default_branch": "main"}
    return {"id": 77, "number": 1, "state": "open", "draft": False, "merged": False,
            "user": {"id": 34589654}, "base": {"repo": repo, "ref": "main", "sha": "a" * 40},
            "head": {"repo": repo, "ref": "topic", "sha": "b" * 40}}


class IdentityTests(unittest.TestCase):
    def test_draft_retarget_and_default_branch_block(self):
        expected = pr_payload()
        for change in ("draft", "retarget", "default"):
            live = pr_payload()
            if change == "draft":
                live["draft"] = True
            elif change == "retarget":
                live["base"]["ref"] = "other"
            else:
                live["head"]["repo"]["default_branch"] = "topic"
            with self.subTest(change=change), self.assertRaises(Blocked):
                validate_live_pr(expected, live, live["head"]["repo"])
