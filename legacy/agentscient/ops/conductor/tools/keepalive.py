#!/usr/bin/env python3
"""Hold a harness credential alive between logins.

    keepalive.py grok             one invocation in an isolated home, then the reading
    keepalive.py grok --dry-run   the reading alone, invoking nothing

Contract: ops/conductor/keepalive-lever.md, which inherits
instrument-conventions.md. Source of demand: the owner, 2026-09-16 —
"grok done - but this is third day in a row - do i have to do this
every day?" The CLI renews its refresh token during use and LD-41
carries the renewal back to the host copy, so a day with no grok job is
a day with no renewal, and the credential ages out overnight.

The reading comes from the credential, never from the invocation's exit
code: a command can run and renew nothing.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, emit, refuse, run
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/ beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

HERE = Path(__file__).resolve()
ISOLATION = HERE.parents[2] / "devlane" / "harness" / "isolation.py"
TIMEOUT = 120
# The cheapest invocation that exercises a signed-in session. Whether it
# renews is not measured (the contract says so); the lever reports what
# the credential did, so a run of `unchanged` readings is the evidence
# for changing this line.
INVOCATION = ("--version",)


def _isolation():
    """Load the harness module by path, as launch.py loads its siblings."""
    import importlib.util
    if not ISOLATION.is_file():
        refuse("tooling", f"isolation.py at {ISOLATION}", "absent",
               "restore ops/devlane/harness/isolation.py")
    spec = importlib.util.spec_from_file_location("lane_isolation", ISOLATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reading(iso, harness):
    try:
        found = iso.credential_reading(harness)
    except iso.NotIsolated:
        refuse("harness", "a harness with an isolation entry", harness,
               f"name one of {', '.join(sorted(iso.HARNESSES))}")
    if found is None:
        refuse("harness", "a harness declaring credential_age", harness,
               "name a harness whose credential has a measured lifetime")
    return found


def _usable(found):
    """A keepalive cannot log in: an absent or aged credential is the one
    case the owner must act on, so it refuses instead of reporting."""
    if found.get("unresolved"):
        refuse("credential", f"a {found['file']} to hold alive",
               found["unresolved"], f"`{found['remedy']}` on the host")
    if found["age_hours"] > found["max_hours"]:
        refuse("credential",
               f"a credential under {found['max_hours']} h old",
               f"{found['age_hours']:.1f} h ({found['field']} {found['created']})",
               f"`{found['remedy']}` on the host — a keepalive cannot log in")


def _invoke(iso, harness, root):
    """One run in a home built the way the launcher builds one, then the
    renewal carried back through the same carry_back."""
    home = iso.build_home(harness, root)
    env = dict(os.environ)
    env.update(iso.dispatch_env(harness, home))
    argv = [harness, *iso.dispatch_flags(harness), *INVOCATION]
    try:
        proc = run(argv, env=env, timeout=TIMEOUT)
    except Refusal as exc:
        refuse("keepalive-error", exc.expected, exc.found, exc.needed)
    if proc.returncode != 0:
        # An invocation that failed says why, on stderr, beside the
        # reading. Swallowing it leaves the operator an exit code and no
        # way to look.
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        for line in tail:
            print(f"keepalive {harness}: {line}", file=sys.stderr)
    carried = iso.carry_back(harness, home)
    outcomes = [item["outcome"] for item in carried
                if item["file"] == "auth.json"] or ["absent"]
    # The exit code is reported, never believed: it says the command
    # ran, not that the credential moved.
    return outcomes[0], proc.returncode


def main(argv: list[str]) -> int:
    args = list(argv)
    dry = "--dry-run" in args
    if dry:
        args.remove("--dry-run")
    if len(args) != 1:
        refuse("arguments", "keepalive.py HARNESS [--dry-run]",
               " ".join(argv) or "nothing", "name exactly one harness")
    harness = args[0]
    iso = _isolation()
    before = _reading(iso, harness)
    _usable(before)
    if dry:
        print(f"keepalive {harness}: before {before['age_hours']:.1f}h; "
              f"dry-run, nothing invoked; credential {before['path']}")
        return 0
    with tempfile.TemporaryDirectory(prefix=f"keepalive-{harness}-") as root:
        carry, code = _invoke(iso, harness, Path(root))
    after = _reading(iso, harness)
    moved = after["created"] != before["created"]
    print(f"keepalive {harness}: before {before['age_hours']:.1f}h; "
          f"after {after['age_hours']:.1f}h; "
          f"{'renewed' if moved else 'unchanged'}; carry {carry}; "
          f"exit {code}; credential {after['path']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Refusal as exc:
        raise SystemExit(emit(exc)) from None
