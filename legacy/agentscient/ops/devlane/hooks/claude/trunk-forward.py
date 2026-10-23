#!/usr/bin/env python3
"""trunk-forward.py -- SessionStart: fast-forward the main checkout's trunk.

LD-38 (2026-09-14): every hook in .claude/settings.json runs from the main
checkout through CLAUDE_PROJECT_DIR, nothing moved that checkout's trunk
when a PR landed, and it sat 44 commits behind the remote trunk with a
stale matrix and settings while the trunk-commit row refused the
fast-forward to every agent. Owner, 2026-09-13: "i'll do options 1 and 2 on next
session". This is option 1.

At every session start it fetches the trunk from origin and fast-forwards
the checkout named by CLAUDE_PROJECT_DIR only when that checkout is on
the trunk, carries no uncommitted change to a tracked file, and git
reports HEAD an ancestor of origin/<trunk>; otherwise it prints one line
saying why not. It prints the old and new sha, because hooks change under
sessions already running. It never blocks a session: exit 0 always. A
fetch it cannot make is could-not-look, said on the line, never a pass.
The trunk is read from ops/lane.conf; an absent TRUNK_BRANCH moves
nothing and says so.
"""

import json
import os
import re
import subprocess
import sys

CONF_LINE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)=(?:'([^']*)'|\"([^\"]*)\"|(\S*))\s*$")


def conf_value(root, key):
    try:
        with open(os.path.join(root, "ops", "lane.conf"), encoding="utf-8") as fh:
            for line in fh:
                match = CONF_LINE.match(line)
                if match and match.group(1) == key:
                    return match.group(2) or match.group(3) or match.group(4) or ""
    except OSError:
        return ""
    return ""


def git(root, *args, timeout=30):
    return subprocess.run(["git", "-C", root, *args],  # noqa: S603,S607 - fixed argv
                          capture_output=True, text=True, timeout=timeout, check=False)


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:160]
    return "no output"


def forward(root):
    """One line, always starting with 'trunk-forward: '."""
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return f"trunk-forward: not a git checkout at {root}; not moved"
    root = top.stdout.strip()
    trunk = conf_value(root, "TRUNK_BRANCH")
    if not trunk:
        return f"trunk-forward: no TRUNK_BRANCH in {root}/ops/lane.conf; not moved"
    branch = git(root, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "a detached HEAD"
    if branch != trunk:
        return f"trunk-forward: {root} is on {branch}, not {trunk}; not moved"
    status = git(root, "status", "--porcelain", "--untracked-files=no").stdout
    dirty = [line for line in status.splitlines() if line.strip()]
    if dirty:
        return f"trunk-forward: {trunk} in {root} carries {len(dirty)} uncommitted change(s); not moved"
    old = git(root, "rev-parse", "HEAD").stdout.strip()
    fetch = git(root, "fetch", "-q", "origin", trunk, timeout=90)
    if fetch.returncode != 0:
        return (f"trunk-forward: fetch of origin/{trunk} failed ({first_line(fetch.stderr)}); "
                f"could not look, {trunk} stays at {old[:8]}; not moved")
    remote = git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{trunk}")
    if remote.returncode != 0:
        return f"trunk-forward: no remote-tracking ref origin/{trunk} in {root}; not moved"
    new = remote.stdout.strip()
    if new == old:
        return f"trunk-forward: {trunk} already at {old[:8]}, the tip of origin/{trunk}"
    if git(root, "merge-base", "--is-ancestor", "HEAD", f"origin/{trunk}").returncode != 0:
        return (f"trunk-forward: {trunk} at {old[:8]} has diverged from origin/{trunk} at {new[:8]}; "
                f"not moved (no commit is made here)")
    merge = git(root, "merge", "--ff-only", "-q", f"origin/{trunk}", timeout=90)
    if merge.returncode != 0:
        return f"trunk-forward: git merge --ff-only origin/{trunk} failed ({first_line(merge.stderr)}); not moved"
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    count = git(root, "rev-list", "--count", f"{old}..{head}").stdout.strip() or "?"
    return (f"trunk-forward: {trunk} {old[:8]}..{head[:8]} ({count} commits) in {root}; "
            f"the hooks now run from {head[:8]}")


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    try:
        line = forward(root)
    except (OSError, subprocess.TimeoutExpired) as error:
        line = f"trunk-forward: could not look ({type(error).__name__}: {error}); not moved"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
