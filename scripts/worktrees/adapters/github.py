"""Merged pull requests from the GitHub CLI; any failure means no evidence."""

from __future__ import annotations

import json
import subprocess


class GhCli:
    def __init__(self, cwd: str, timeout: float = 20.0) -> None:
        self.cwd = cwd
        self.timeout = timeout

    def merged_pr_head(self, branch: str) -> str | None:
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--head", branch, "--state", "merged", "--json", "headRefOid", "--limit", "1"],
                cwd=self.cwd, capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode:
            return None
        try:
            entries = json.loads(result.stdout or "[]")
        except ValueError:
            return None
        return entries[0].get("headRefOid") if entries else None
