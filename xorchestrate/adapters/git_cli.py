"""``VersionControl`` through the ``git`` command line.

Options: ``executable`` (default ``git``) and ``timeout`` seconds (default 30).
Every call runs git with literal arguments and without the repository-selecting
``GIT_*`` environment variables, so the caller's environment cannot redirect it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..model import OrchestrationError
from ..ports import WorktreeLocation
from .process import positive_number


class GitCLI:
    def __init__(self, options=None):
        options = {} if options is None else options
        if not isinstance(options, dict) or options.keys() - {"executable", "timeout"}:
            raise OrchestrationError("The git adapter accepts executable and timeout.", "invalid_configuration")
        executable = options.get("executable", "git")
        if not isinstance(executable, str) or not executable:
            raise OrchestrationError("git executable must be a program name or path.", "invalid_configuration")
        self.executable = executable
        self.timeout = positive_number(options, "timeout", 30)

    def _run(self, *arguments, cwd):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        try:
            result = subprocess.run([self.executable, *arguments], cwd=cwd, env=environment, capture_output=True,
                                    text=True, timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            raise OrchestrationError("git is required to identify a project.", "git_unavailable") from exc
        return result.stdout.strip() if result.returncode == 0 else None

    def worktree(self, path):
        directory = Path(path)
        if not directory.is_dir():
            directory = directory.parent
        if not directory.is_dir():
            return None
        root = self._run("rev-parse", "--show-toplevel", cwd=directory)
        private = self._run("rev-parse", "--absolute-git-dir", cwd=directory)
        common = self._run("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=directory)
        if not root or not private or not common:
            return None
        branch = self._run("symbolic-ref", "--short", "-q", "HEAD", cwd=directory) or None
        return WorktreeLocation(str(Path(root).resolve()), str(Path(private).resolve()), str(Path(common).resolve()),
                                branch)

    def root_commits(self, path):
        if not Path(path).is_dir():
            return frozenset()
        output = self._run("rev-list", "--max-parents=0", "--all", cwd=path)
        return frozenset(output.split()) if output else frozenset()

    def remote_url(self, path, name="origin"):
        if not Path(path).is_dir():
            return None
        return self._run("remote", "get-url", name, cwd=path) or None

    def revision(self, path):
        if not Path(path).is_dir():
            return None
        return self._run("rev-parse", "--verify", "-q", "HEAD", cwd=path) or None

    def initialize(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        if self._run("init", "-q", str(path), cwd=path) is None:
            raise OrchestrationError("git init failed.", "git_unavailable", {"path": str(path)})
