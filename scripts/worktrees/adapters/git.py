"""Git through a subprocess, inheriting the caller's environment."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from scripts.worktrees.ports import GitError


class SubprocessGit:
    def run(self, args: Sequence[str], cwd: str) -> str:
        try:
            result = subprocess.run(["git", *map(str, args)], cwd=str(cwd), capture_output=True, text=True,
                                    check=False)
        except OSError as error:
            raise GitError([str(arg) for arg in args], -1, str(error)) from error
        if result.returncode:
            raise GitError([str(arg) for arg in args], result.returncode, result.stderr)
        return result.stdout.rstrip("\n")
