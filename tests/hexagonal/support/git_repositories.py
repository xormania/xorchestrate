"""Throwaway git repositories for the dev-tool tests: a bare origin, a main checkout and linked trees.

Git runs with no global or system configuration and a fixed test identity, so the
user's own git setup never leaks into a test.
"""

import os
import subprocess
from pathlib import Path

REPOSITORY_NAME = "sample-project"
"""The main checkout's basename; deliberately unlike any real repository."""
DEFAULT_BRANCH = "trunk"
"""The origin's default branch; not ``master``, so a default base must come from origin/HEAD."""
FILES = {
    "README.md": "sample\n",
    ".gitignore": "*.log\n.serena/cache/\n.venv/\nvenv/\nbuild/\ndist/\n.pytest_cache/\n__pycache__/\n",
    "app_constants.py": "LIMIT = 3\n",
    "pkg/__init__.py": "",
    "pkg/a_values.py": "VALUES = (1, 2)\n",
    "pkg/b_model.py": "class Model:\n    pass\n",
    "pkg/c_helpers.py": "def helper():\n    return 1\n",
}
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def git(cwd, *args, check=True):
    result = subprocess.run(["git", *map(str, args)], cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise AssertionError(f"git {' '.join(map(str, args))} failed: {result.stderr}")
    return result.stdout.strip() if check else result


def commit(tree, path, text, message="change"):
    target = Path(tree) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(tree, "add", path)
    git(tree, "commit", "-q", "-m", message)
    return git(tree, "rev-parse", "HEAD")


def make_repository(base, files=None):
    """A bare origin whose HEAD is ``trunk`` and a main checkout tracking it."""
    base = Path(base)
    origin = base / "remote" / "origin.git"
    origin.parent.mkdir(parents=True)
    git(base, "init", "-q", "--bare", "-b", DEFAULT_BRANCH, origin)
    main = base / "checkouts" / REPOSITORY_NAME
    main.mkdir(parents=True)
    git(main, "init", "-q", "-b", DEFAULT_BRANCH)
    for path, text in (FILES if files is None else files).items():
        target = main / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "initial")
    git(main, "remote", "add", "origin", origin)
    git(main, "push", "-q", "-u", "origin", DEFAULT_BRANCH)
    git(main, "remote", "set-head", "origin", DEFAULT_BRANCH)
    return origin, main


def isolate(monkeypatch, home):
    """Point HOME at ``home`` and drop every variable that could carry the user's environment into a test."""
    for name in list(os.environ):
        if name.startswith(("XDG_", "XORCHESTRATE_", "GIT_", "SERENA")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name, value in GIT_IDENTITY.items():
        monkeypatch.setenv(name, value)


def linked_tree(main, path, branch):
    git(main, "worktree", "add", "-q", "-b", branch, path)
    return Path(path)
