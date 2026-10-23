"""A hook refusal logs a miss against the skill it names.

The skills registry (ops/conductor/tools/skills.py) counts uses, failures
and misses per skill, and a miss is "a potential use that did not
happen". Until 2026-09-11 none was ever logged: the report read zero for
every skill and meant nothing (13 of 17 skills with no event at all). A
refusal is the one moment the lane knows a page was not followed, so
each refusing hook calls ``log_miss`` once, after its decision is made.

Best effort by construction: the refusal is printed whether or not the
miss lands. A missing lever, a registry without the skill's row, a
timeout, an unwritable store: each returns False and changes nothing.
The lever's own contract holds; this module never opens the store.

Which skill: the ``deliverable`` row names the ``deliverable`` page;
every other class names ``refused``, the page that lists the accepted
spelling per class. Identity: the session's ``WF_AGENT`` when the hook
inherits one, else the hook signs as itself with the lane's name, so
the event says who recorded it and nothing is written unsigned.
"""
from __future__ import annotations

import os
import subprocess
import sys

SKILLS_LEVER = "ops/conductor/tools/skills.py"
DEFAULT_SKILL = "refused"
MISS_SKILL = {"deliverable": "deliverable"}
DETAIL_CAP = 400
TIMEOUT = 10


def skill_for(klass):
    return MISS_SKILL.get(klass, DEFAULT_SKILL)


def found_clause(reason):
    """The ``found`` half of a wire line, else the line's head."""
    text = " ".join(str(reason).split())
    if "; found " in text:
        text = text.split("; found ", 1)[1]
        text = text.split("; needed ", 1)[0]
    return text


def log_miss(cwd, klass, found, hook="matrix"):
    """Record one miss for ``klass`` against the skill it names; never raise."""
    if not klass or klass == "hook-error":
        return False
    try:
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
            env["WF_AGENT"] = f"{hook} hook <{hook}@{lane}>"
        detail = f"{klass}: {found_clause(found)}"[:DETAIL_CAP]
        proc = subprocess.run(  # noqa: S603 - fixed argv, the lane's own lever
            [sys.executable, lever, "log-miss", skill_for(klass), detail],
            cwd=root, env=env, capture_output=True, text=True,
            timeout=TIMEOUT, check=False,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - a miss that cannot be logged is not a refusal
        return False
