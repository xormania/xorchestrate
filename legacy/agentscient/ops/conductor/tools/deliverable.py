#!/usr/bin/env python3
"""The lane's deliverable lock.

    deliverable.py set BRANCH... --why TEXT [--measure CMD --until TEXT]
                                                 name the branches work may aim at, and the target
    deliverable.py add BRANCH... --why TEXT [--measure CMD --until TEXT]
                                                 name more beside the ones named (a second session)
    deliverable.py show [--unmeasured]           read recorded targets, never run measurements
    deliverable.py done BRANCH                   explicitly measure once and record the result;
                                                 refuse `target` when short or unreachable
    deliverable.py remove BRANCH... [--unmet TEXT]
                                                 lift this session's branches; a short target refuses
                                                 without the reason; the last one lifts the lock
    deliverable.py clear                         lift it whole, every session's entry at once

Owner, 2026-09-11: "i want an immediate FIX to stop that - right fucking
now." While the lock names branches, the check matrix refuses any new
worktree or branch, dispatch, ladder start or PR creation aimed at a
branch not named. Several branches may be named: parallel work is
untouched; unnamed work is. Findings met on the way go to the ledger.
Writing verbs need an identity; reads never do. Refusals are one stderr
line, exit 2, stdout empty.

The target (owner, 2026-09-15, on a session that reported a plan's
slices all done with 210 of 226 test modules still off the kit:
"whatever it says - is the problem we to solve - right fucking now"):
"done" was measured against the named work, never against the owner's
target, and no gate had a floor. A target is a measure command and the
text its output must carry. `done` measures explicitly and records the
latest observation in the existing target. `remove` retains its deliberate
verification before lifting a target. Ordinary show, including the compatible
--unmeasured option, never executes commands or acquires writer identity.

PR-01: legacy commands do not declare every input they read. Their recorded
outcome is history, not proof of current applicability. Show reports source,
candidate reference and time when recorded, but keeps the target unmeasured
and applicability unknown until structured evidence supplies that binding.
A recorded success never becomes a fresh pass just because the view opened.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shlex
import sys

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, emit, identity, now, refuse, run, state_dir
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

LOCK = "deliverable.json"
GUARD = "deliverable.json.lock"
MEASURE_TIMEOUT = 120
# The measure runs from the root of the checkout whose lever runs it, so a
# census written against relative paths reads one tree from any cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def option(args, name):
    """The value after ``name`` in args, or None when the flag is absent."""
    if name not in args:
        return None
    i = args.index(name)
    return args[i + 1] if i + 1 < len(args) else ""


def positional(args, *flags):
    """The arguments that are neither a flag nor a flag's value."""
    values = {option(args, f) for f in flags}
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in flags:
            skip = True
            continue
        if a.startswith("--") or a in values:
            continue
        out.append(a)
    return out


def target_of(args):
    """(measure, until) from --measure and --until, both or neither."""
    measure, until = option(args, "--measure"), option(args, "--until")
    if (measure is None) != (until is None):
        refuse("arguments", "--measure CMD and --until TEXT together",
               f"measure={measure!r} until={until!r}", "give both, or neither")
    if measure is not None and not (measure.strip() and until.strip()):
        refuse("arguments", "a non-empty --measure and --until", f"measure={measure!r} until={until!r}",
               "name the command and the text its output must carry")
    return (measure, until) if measure is not None else None


def reading(target):
    """(met, line) for a target: the measure's first line carrying the
    until text when met, else its first non-empty line; a measure that
    cannot run, or exits non-zero, reads unreachable and is never met."""
    try:
        proc = run(shlex.split(target["measure"]), cwd=REPO_ROOT, timeout=MEASURE_TIMEOUT)
    except (Refusal, ValueError) as error:
        return False, f"unreachable ({error})"
    if proc.returncode != 0:
        return False, f"unreachable (exit {proc.returncode})"
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    for line in lines:
        if target["until"] in line:
            return True, line
    return False, (lines[0] if lines else "no output")


def target_line(branch, target):
    """A recorded observation, never a current proof decision."""
    owner = f" (by {target['by']})" if target.get("by") else ""
    record = target.get("measurement")
    applicability = "unknown (verification inputs not bound)"
    if record is None:
        history = "measurement absent; source unknown; candidate unknown; at unknown"
    elif not isinstance(record, dict) or record.get("schema") != 1:
        history = "measurement unavailable; source unknown; candidate unknown; at unknown"
    else:
        if record.get("measure") != target["measure"] or record.get("until") != target["until"]:
            applicability = "stale (target definition changed)"
        history = (f"measurement recorded; recorded-outcome={record.get('outcome', 'unknown')}; "
                   f"source {record.get('source') or 'unknown'}; "
                   f"candidate {record.get('candidate') or 'unknown'}; at {record.get('at') or 'unknown'}")
    return " ".join((f"target {branch}: until {target['until']}; reading unmeasured; unmeasured; "
                     f"{history}; applicability {applicability}{owner}").split())


def load_lock():
    """Absence is the only empty result; malformed/unreadable stores refuse."""
    path = lock_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        refuse("store", f"a readable lock at {path}", f"unreadable: {error}", "restore the lock; show never repairs it")
    try:
        data = json.loads(text)
    except ValueError as error:
        refuse("store", f"a JSON lock at {path}", f"corrupt: {error}", "restore the recorded lock")
    if (not isinstance(data, dict) or not isinstance(data.get("branches"), list)
            or not all(isinstance(b, str) for b in data["branches"])
            or not isinstance(data.get("targets", {}), dict)):
        refuse("store", "a lock with branches and optional targets", "incompatible shape", "restore a compatible lock")
    for branch, target in data.get("targets", {}).items():
        if (branch not in data["branches"] or not isinstance(target, dict)
                or not all(isinstance(target.get(k), str) and target[k].strip() for k in ("measure", "until"))):
            refuse("store", "named targets with measure and until", f"incompatible target {branch}", "restore the target")
    return data


def candidate_ref():
    """A checkout HEAD reference, not a claim that all command inputs are pinned."""
    try:
        proc = run(["git", "rev-parse", "--verify", "HEAD"], cwd=REPO_ROOT, timeout=5)
    except Refusal:
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and len(value) in (40, 64) and all(c in "0123456789abcdef" for c in value) else None


def lock_path():
    return state_dir() / LOCK


@contextlib.contextmanager
def held():
    """The lock's path, with an exclusive flock held on deliverable.json.lock
    beside it. Sessions on one host share the lock, and every writer reads,
    changes and writes it inside this, so two writers never interleave: a
    concurrent remove and add lost a branch in 3 of 60 trials without it
    (Grok, dispatch 20260913T105222Z-review-grok-00b782, finding 2)."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(GUARD), "a+", encoding="utf-8") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield path


def write(path, data):
    """Replace the lock whole, so a reader never sees half a file."""
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(part, path)


def do_set(args):
    """T-71: a lead session ran set for its closing AAR and the lock then
    read one measure against all three branches it named, so two other
    sessions read short against a target that was never theirs -- and a
    branch left unnamed was dropped from the lock outright. set re-targets
    branches the lock already names and touches nothing else; add is the
    verb that introduces one. On no lock, set still writes the first."""
    branches = positional(args, "--why", "--measure", "--until")
    why = option(args, "--why") or ""
    if not branches:
        refuse("arguments", "set BRANCH... --why TEXT", " ".join(args) or "no branches", "name at least one branch")
    if not why.strip():
        refuse("arguments", "a non-empty --why", repr(why), "say what the deliverable is")
    target = target_of(args)
    by = identity()
    stamp = now().strftime("%Y-%m-%dT%H:%M:%SZ")
    with held() as path:
        try:
            held_data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held_data = {}
        if not isinstance(held_data, dict):
            held_data = {}
        named = [str(b) for b in (held_data.get("branches") or [])]
        targets = dict(held_data.get("targets") or {})
        if named:
            missing = [b for b in branches if b not in named]
            if missing:
                refuse("absent", f"branches the lock names ({', '.join(named)})", ", ".join(missing),
                       "deliverable.py add <branch> names a new session's branch beside these")
            reasons = [r for r in [str(held_data.get("why") or ""), why] if r.strip()]
            if held_data.get("why") and why in held_data["why"]:
                reasons = [str(held_data["why"])]
            data = {"branches": named, "why": " | ".join(reasons),
                    "by": held_data.get("by") or by,
                    "at": held_data.get("at") or stamp}
        else:
            named = list(branches)
            data = {"branches": named, "why": why, "by": by, "at": stamp}
        if target:
            for branch in branches:
                targets[branch] = {"measure": target[0], "until": target[1],
                                   "by": by, "at": stamp}
        if targets:
            data["targets"] = targets
        write(path, data)
    print(f"locked {' '.join(named)}")
    return 0


def do_add(args):
    """Owner, 2026-09-12: "as long you account for the fact that i'll have
    multiple sessions running soon". Sessions on one host share this lock;
    a second session adds its branch beside the first's, never over it."""
    branches = positional(args, "--why", "--measure", "--until")
    why = option(args, "--why") or ""
    if not branches:
        refuse("arguments", "add BRANCH... --why TEXT", " ".join(args) or "no branches", "name at least one branch")
    if not why.strip():
        refuse("arguments", "a non-empty --why", repr(why), "say what this session's deliverable is")
    target = target_of(args)
    by = identity()
    stamp = now().strftime("%Y-%m-%dT%H:%M:%SZ")
    with held() as path:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"branches": [], "why": ""}
        named = [str(b) for b in data.get("branches") or []]
        for branch in branches:
            if branch not in named:
                named.append(branch)
        reasons = [r for r in [str(data.get("why") or ""), why] if r.strip()]
        if data.get("why") and why in data["why"]:
            reasons = [str(data["why"])]
        targets = dict(data.get("targets") or {})
        if target:
            # LD-49: the lock stamped one `by` at the top and add
            # overwrote it, so the last writer appeared to own every
            # branch. Each target says who set it and when; the
            # lock-level by stays with whoever opened the lock.
            for branch in branches:
                targets[branch] = {"measure": target[0], "until": target[1],
                                   "by": by, "at": stamp}
        data = {"branches": named, "why": " | ".join(reasons),
                "by": data.get("by") or by,
                "at": data.get("at") or stamp}
        if targets:
            data["targets"] = targets
        write(path, data)
    print(f"locked {' '.join(named)}")
    return 0


def do_remove(args):
    """Owner, 2026-09-13 ("minimize parallel work conflicts"; LD-28): a
    session that lands lifts its own branches and leaves every other
    session's in place; clear was the only exit and wiped them all."""
    branches = positional(args, "--unmet")
    unmet = option(args, "--unmet")
    if not branches:
        refuse("arguments", "remove BRANCH... [--unmet TEXT]", " ".join(args) or "no branches",
               "name the branches to lift")
    if unmet is not None and not unmet.strip():
        refuse("arguments", "a non-empty --unmet", repr(unmet), "say why the target is left short")
    identity()
    with held() as path:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        named = [str(b) for b in (data.get("branches") if isinstance(data, dict) else None) or []]
        missing = [b for b in branches if b not in named]
        if missing:
            refuse("absent", f"branches the lock names ({', '.join(named) or 'none'})", ", ".join(missing),
                   "deliverable.py show; remove only what is named")
        targets = dict(data.get("targets") or {})
        short = []
        for branch in branches:
            if branch in targets:
                met, line = reading(targets[branch])
                if not met:
                    short.append(f"{branch} reads {line}")
        if short and unmet is None:
            refuse("target", "every target of the branches lifted met, or --unmet TEXT saying why not",
                   "; ".join(short), "finish the deliverable (deliverable.py done BRANCH), or remove --unmet '<why>'")
        tail = f" unmet ({unmet})" if short else ""
        for branch in branches:
            targets.pop(branch, None)
        left = [b for b in named if b not in branches]
        if not left:
            path.unlink()
            print(f"removed {' '.join(branches)}{tail}; no lock")
            return 0
        data["branches"] = left
        if targets:
            data["targets"] = targets
        else:
            data.pop("targets", None)
        write(path, data)
    print(f"removed {' '.join(branches)}{tail}; locked {' '.join(left)}")
    return 0


def do_done(args):
    """Measure once, then record only if the measured target is still held."""
    if len(args) != 1 or args[0].startswith("--"):
        refuse("arguments", "done BRANCH", " ".join(args) or "no branch", "name one branch")
    branch = args[0]
    by = identity()
    data = load_lock() or {}
    named = data.get("branches", [])
    if branch not in named:
        refuse("absent", f"a branch the lock names ({', '.join(named) or 'none'})", branch,
               "deliverable.py show; done is asked of a named branch")
    target = data.get("targets", {}).get(branch)
    if not target:
        refuse("target", f"a measured target on {branch} (--measure CMD --until TEXT on set or add)",
               "none: the lock names the branch and no reading", "set the target, then ask again; done is a reading")
    candidate = candidate_ref()
    met, line = reading(target)
    record = {"schema": 1, "source": REPO_ROOT, "candidate": candidate,
              "at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by,
              "measure": target["measure"], "until": target["until"],
              "outcome": "met" if met else "short", "reading": line}
    with held() as path:
        latest = load_lock() or {}
        if latest.get("targets", {}).get(branch) != target:
            refuse("target-changed", "the target measured still held unchanged", branch,
                   "read the current target and explicitly measure it; the old result was not recorded")
        latest["targets"][branch]["measurement"] = record
        write(path, latest)
    if not met:
        refuse("target", f"the measure to read {target['until']}", line,
               "keep going; done is the reading, never the named slices")
    print(f"done {branch}: {line}")
    return 0


def do_show(args):
    if args not in ([], ["--unmeasured"]):
        refuse("arguments", "show, or show --unmeasured", " ".join(args),
               "name no option, or --unmeasured alone")
    data = load_lock()
    if data is None:
        print("no lock")
        return 0
    print(json.dumps(data, indent=2))
    for branch, target in data.get("targets", {}).items():
        print(target_line(branch, target))
    return 0


def do_clear(_args):
    identity()
    with held() as path:
        try:
            path.unlink()
        except FileNotFoundError:
            print("no lock")
            return 0
    print("cleared")
    return 0


VERBS = {"set": do_set, "add": do_add, "show": do_show, "done": do_done, "remove": do_remove, "clear": do_clear}


def main(argv):
    try:
        if len(argv) < 2 or argv[1] not in VERBS:
            refuse("arguments", "one of set, add, show, done, remove, clear",
                   argv[1] if len(argv) > 1 else "no verb", "name a verb")
        return VERBS[argv[1]](argv[2:])
    except Refusal as error:
        return emit(error)
    except OSError as error:
        return emit(Refusal("store", "a writable lock for explicit mutation", str(error), "restore or retry the lock"))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
