"""Controlled remote observations and real disposable repositories for PR contracts."""

from copy import deepcopy
from pathlib import Path

from tests.hexagonal.support.projects import git

HEAD, BASE, MERGE, TREE = (letter * 40 for letter in "abcd")
REPOSITORY = "example/project"


def snapshot(**changes):
    """A complete remote observation whose check belongs to its test merge."""
    value = {
        "repository": REPOSITORY, "number": 17, "head": HEAD, "base": BASE, "merge": MERGE,
        "head_ref": "feature", "base_ref": "master", "source_repository": REPOSITORY,
        "draft": True, "state": "open", "mergeable": True, "complete": True, "validation": "passed",
        "checks": [{"id": 101, "name": "CI result", "sha": MERGE, "status": "completed",
                    "conclusion": "success", "run_id": 201, "run_attempt": 1, "event": "pull_request"}],
        "reviews": [], "threads": [], "provenance": {"status": "qualified"},
        "observed_at": "2026-11-10T12:00:00Z", "body": "Owner-written description",
    }
    return {**value, **deepcopy(changes)}


class HostingFixture:
    """External state can change independently of the source and base SHAs."""

    def __init__(self):
        self.current = snapshot()
        self.snapshots = []
        self.existing = []
        self.created = []
        self.create_callback = None
        self.snapshot_callback = None
        self.reads = 0

    def snapshot(self, repository, number):
        self.reads += 1
        if self.snapshot_callback is not None:
            self.snapshot_callback(self, self.reads)
        value = self.snapshots.pop(0) if self.snapshots else self.current
        return deepcopy(value)

    def branch(self, repository, ref):
        return {"sha": self.current["head"] if ref in {"feature", self.current["head"]} else self.current["base"], "tree": TREE}

    def find(self, repository, branch):
        return deepcopy(self.existing)

    def create(self, repository, branch, base, title, body):
        self.created.append({"repository": repository, "branch": branch, "base": base,
                             "title": title, "body": body})
        self.current = {**self.current, "body": body}
        self.existing = [deepcopy(self.current)]
        if self.create_callback is not None:
            return self.create_callback(self)
        return deepcopy(self.current)


class WorkspaceFixture:
    def __init__(self):
        self.current = {"head": HEAD, "tree": TREE, "branch": "feature", "worktree": "/owned/worktree",
                        "repository": REPOSITORY}
        self.publications = []

    def identity(self, name):
        return deepcopy(self.current)

    def publish(self, name, expected_head, branch):
        self.publications.append((name, expected_head, branch))
        return deepcopy(self.current)


def publication(**changes):
    return {
        "repository": REPOSITORY, "workspace": "owned", "branch": "feature", "base": "master",
        "expected_head": HEAD, "title": "A coherent change", "body": "Proposed generated description",
        "review": {"head": HEAD, "tree": TREE, "scope": "Candidate diff and failure paths",
                   "reviewer": "independent-session", "findings": [],
                   "verification": ["red witness and healthy control"]}, **deepcopy(changes),
    }


class GitRepository:
    """A repository and owned linked worktree; assertions use Git itself as oracle."""

    def __init__(self, root):
        self.root = Path(root)
        self.main = self.root / "main"
        self.feature = self.root / "feature"
        self.main.mkdir(parents=True)
        git("init", "-q", "-b", "master", cwd=self.main)
        for name in ("shared.txt", "feature.txt", "base.txt"):
            (self.main / name).write_text("original\n")
        self.commit(self.main, ["shared.txt", "feature.txt", "base.txt"], "initial")
        self.initial = self.head(self.main)
        git("worktree", "add", "-q", "-b", "feature", str(self.feature), cwd=self.main)

    @staticmethod
    def commit(worktree, paths, message):
        git("add", "--", *paths, cwd=worktree)
        git("commit", "-q", "-m", message, cwd=worktree)
        return GitRepository.head(worktree)

    @staticmethod
    def head(worktree):
        return git("rev-parse", "HEAD", cwd=worktree)

    def advance_base(self, name="base.txt", text="target change\n"):
        (self.main / name).write_text(text)
        return self.commit(self.main, [name], "advance target")

    def options(self, remote="origin"):
        return {"workspaces": {"owned": {"path": str(self.feature), "remote": remote,
                                          "repository": REPOSITORY}}}
