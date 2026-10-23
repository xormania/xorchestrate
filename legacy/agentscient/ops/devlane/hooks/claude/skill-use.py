#!/usr/bin/env python3
"""A Skill invocation logs a use in the skills registry.

PreToolUse hook on the Skill tool. Until 2026-09-13 a use was counted
only when a session typed `skills.py log-use` by hand, so the report's
use column was a floor: 12 close, 7 deliverable, 6 onboard, zero for
thirteen skills, and no way to tell an unused page from an unlogged one.
This hook records every invocation with the skill's name and its
arguments, the same event the hand command writes.

Best effort by construction, the shape of miss.py: the call is always
allowed (exit 0, no output). A registry without the row, a missing
lever, an unwritable store, a timeout or a malformed payload each log
nothing and change nothing. Identity is the session's WF_AGENT when the
hook inherits one, else the hook signs as itself with the lane's name.

Written by hand under grant 208 (owner, 2026-09-13: "hand grant ok'd for
new skills #1-4 - do them in one PR"); the test is
ops/devlane/hooks/tests/test_skill_use.py, under wall grant 209.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

SKILLS_LEVER = "ops/conductor/tools/skills.py"
DETAIL_CAP = 400
TIMEOUT = 10
HOOK = "skill-use"


def skill_and_args(payload):
    """The skill name and its arguments from a Skill call, else None."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Skill":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    name = tool_input.get("skill") or tool_input.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    args = tool_input.get("args")
    detail = " ".join(str(args).split()) if isinstance(args, str) and args.strip() else ""
    return name.strip(), (detail or "(no arguments)")[:DETAIL_CAP]


def log_use(cwd, name, detail):
    """Record one invocation; never raise, never block."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from matrix import lane_conf, repo_root
        root = repo_root(cwd or os.getcwd())
        lane = lane_conf(root).get("LANE_NAME") if root else None
        if not root or not lane:
            return False
        lever = os.path.join(root, SKILLS_LEVER)
        if not os.path.isfile(lever):
            return False
        env = dict(os.environ)
        if not env.get("WF_AGENT"):
            env["WF_AGENT"] = f"{HOOK} hook <{HOOK}@{lane}>"
        proc = subprocess.run(  # noqa: S603 - fixed argv, the lane's own lever
            [sys.executable, lever, "log-use", name, detail],
            cwd=root, env=env, capture_output=True, text=True,
            timeout=TIMEOUT, check=False,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - a use that cannot be logged never blocks the call
        return False


def main():
    try:
        payload = json.loads(sys.stdin.read() or "null")
    except ValueError:
        return 0
    found = skill_and_args(payload)
    if found is None:
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    log_use(cwd, *found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
