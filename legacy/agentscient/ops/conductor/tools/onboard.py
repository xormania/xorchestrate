#!/usr/bin/env python3
"""Print working guidance, live state, and the handoff board."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, emit, lane_name, refuse, state_root
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

def _bound(name: str, fallback: int) -> int:
    """A read's bound in seconds. Overridable so a loaded host, and the
    rows that prove the retry, can move it; anything that is not a
    positive whole number is ignored, because a brief that refuses to
    start over an environment variable helps nobody."""
    try:
        seconds = int(os.environ.get(name, ""))
    except ValueError:
        return fallback
    return seconds if seconds > 0 else fallback


TIMEOUT = _bound("ONBOARD_TIMEOUT", 5)
RETRY_TIMEOUT = _bound("ONBOARD_RETRY_TIMEOUT", 20)
TIMED_OUT = "timed out"
LAW = (
    ("AGENTS.md", "the operating contract"),
    ("CONTRIB.md", "naming, PR bodies, fix commits, the review wire"),
    (
        "ops/conductor/instrument-conventions.md",
        "the lane's base contract: wire, identity, exits",
    ),
    (
        "ops/conductor/stack.md",
        "the stack: layers, incumbents, open seats",
    ),
    ("ops/conductor/coverage-zones.md", "what gates what"),
    ("ops/conductor/discovery-lever.md", "profile discovery, source custody, and uncertainty"),
    ("ops/conductor/conductor-start-plan.md", "the conductor startup sequence and admission boundaries"),
)


def run(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> tuple[str | None, str]:
    """Run one bounded read, returning output or a printable absence reason.

    A reason that begins TIMED_OUT says the read could not look, which is
    not the same as a sibling that looked and reported nothing.
    """
    bound = TIMEOUT if timeout is None else timeout
    try:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=bound,
            env=env,
        )
    except FileNotFoundError:
        return None, f"{command[0]} was not found"
    except subprocess.TimeoutExpired:
        return None, f"{TIMED_OUT} after {bound}s"
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        reason = detail or f"exit {result.returncode} with no output"
        return None, reason.replace("\n", " | ")
    return result.stdout.strip(), ""


def bounded_read(
    command: list[str],
    cwd: Path | None,
    label: str,
    env: dict[str, str] | None = None,
    unavailable: str = "unavailable",
) -> tuple[str | None, str]:
    """One bounded read and, when it did not answer, the line that says so.

    LD-48 taught the lock line to tell a read that could not look from a
    lever that looked and reported a failure: under concurrent sessions
    the first bound is what expires, and `unavailable` in that position
    is taken for an answer by the reader after it. The line each read
    prints then differs in the word only, so the rule is spelled once
    here and the other three lines were the gap that repair declared:
    one retry with the longer bound when the first expired, then
    UNREACHABLE, which no reader takes for an answer.

    A lever that ran and failed keeps `unavailable`: it looked. Callers
    whose contract names another word for that case pass it.
    """
    output, reason = run(command, cwd, env)
    if output is None and reason.startswith(TIMED_OUT):
        first = reason
        output, reason = run(command, cwd, env, timeout=RETRY_TIMEOUT)
        if output is None and reason.startswith(TIMED_OUT):
            return None, f"{label}: UNREACHABLE: {first}, retried and {reason}"
    if output is None:
        return None, f"{label}: {unavailable}: {reason}"
    return output, ""


def repository_root() -> Path:
    output, reason = run(["git", "rev-parse", "--show-toplevel"])
    if output is None:
        refuse(
            "repo",
            "a working directory inside a git work tree",
            reason,
            "run onboard.py from inside the repository",
        )
    return Path(output)


def register_line(root: Path) -> tuple[str, bool]:
    tool = root / "ops/conductor/tools/decisions.py"
    output, reason = run([sys.executable, str(tool), "report"], root)
    if output is None:
        return f"register: {reason}", False
    try:
        report = json.loads(output)
        counts = report["counts"]
        blocked = [row["id"] for row in report["blocked"]]
        count_text = ", ".join(f"{key}={value}" for key, value in counts.items())
        blocked_text = ",".join(blocked) if blocked else "none"
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        return f"register: invalid report: {error}", False
    return f"register: {count_text}; blocked={blocked_text}", True




def prs_line(root: Path) -> tuple[str, bool]:
    command = [
        "gh", "pr", "list", "--state", "open",
        "--limit", "200", "--json", "number,title",
    ]
    gh_env = os.environ.copy()
    # Recent gh versions mint a telemetry device id on reads. Point that
    # optional state at an existing non-store location so onboarding remains
    # a read; authentication continues to come from the caller's normal env.
    gh_env["XDG_STATE_HOME"] = os.devnull
    output, absence = bounded_read(
        command, root, "prs", gh_env, unavailable="gh unavailable"
    )
    if output is None:
        return absence, False
    try:
        rows = json.loads(output)
        if not isinstance(rows, list):
            raise TypeError("expected a JSON list")
        entries = [f"#{row['number']} {row['title']}" for row in rows]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        return f"prs: gh unavailable: invalid report: {error}", False
    if len(entries) >= 200:
        return f"prs: {len(entries)} open; truncated at the 200 limit", False
    detail = "; ".join(entries) if entries else "none"
    return f"prs: {len(entries)} open; {detail}", True


def lock_lines(root: Path) -> tuple[list[str], bool]:
    """The deliverable lock as deliverable.py show --unmeasured prints it:
    the branches and the owner's words on one line, then every target
    with its until text (owner, 2026-09-15: a brief that never printed the
    lock let a plan's paraphrase of the order stand in for the order).
    No measure runs here: show ran every target's measure in turn, 1m54s
    for 44 targets, and no brief finished inside its bounds (LD-146).
    The reading is `deliverable.py done <branch>`."""
    # The siblings' own root: the launch binding's XDG_STATE_HOME in a
    # member whose HOME is private, else XDG_STATE_HOME or ~/.local/state.
    # Read through Path.home() there, the lock read none (LD-146).
    state_home = state_root()
    lock = state_home / lane_name() / "deliverable.json"
    if not lock.is_file():
        # Checked before the lever runs: a read never creates the state
        # directory (the doctor's read-only probe reads it that way).
        return ["lock: none"], True
    tool = root / "ops/conductor/tools/deliverable.py"
    # LD-48, where the retry and the UNREACHABLE word were first needed;
    # bounded_read holds the rule now, and every other read shares it.
    output, absence = bounded_read(
        [sys.executable, str(tool), "show", "--unmeasured"], root, "lock"
    )
    if output is None:
        return [absence], False
    if output.strip() == "no lock":
        return ["lock: none"], True
    lines = output.splitlines()
    targets = [line for line in lines if line.startswith("target ")]
    body = "\n".join(line for line in lines if not line.startswith("target "))
    try:
        data = json.loads(body)
        branches = ", ".join(str(b) for b in data.get("branches") or []) or "nothing"
        why = str(data.get("why") or "").strip() or "no why"
    except (json.JSONDecodeError, AttributeError) as error:
        return [f"lock: invalid report: {error}"], False
    return [f"lock: {branches} — {why}", *targets], True


def dispatch_is_running(path: Path) -> bool:
    if (path / "exit").exists():
        return False
    try:
        record = json.loads((path / "record.json").read_text(encoding="utf-8"))
        state = json.loads((path / "state.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("status") != "launched":
            return False
        if not isinstance(state, dict):
            return False
        pid = state["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        # Signal zero observes PID existence, not worker identity or progress.
        os.kill(pid, 0)
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
        return False
    return True


def dispatches_line() -> tuple[str, bool]:
    # The siblings' own root: the launch binding's XDG_STATE_HOME in a
    # member whose HOME is private, else XDG_STATE_HOME or ~/.local/state.
    # Read through Path.home() there, the lock read none (LD-146).
    state_home = state_root()
    store = state_home / lane_name() / "dispatch"
    if not store.is_dir():
        if not store.exists() and not store.is_symlink():
            # The contract: an absent store says so — a measured fact,
            # not an unmeasurable source.
            return f"dispatches: ABSENT {store} (nothing running)", True
        # The path is there and cannot be listed. That is neither the
        # ABSENT above, which is a measured nothing, nor an empty store.
        return f"dispatches: UNREACHABLE: {store} is not a readable directory", False
    try:
        records = sorted(
            (path for path in store.iterdir() if path.is_dir()), key=lambda path: path.name
        )
    except OSError as error:
        # A store that will not list is a store nobody read: running=none
        # would be an answer, and there is none to give.
        return f"dispatches: UNREACHABLE: {error}", False
    running = [path.name for path in records if dispatch_is_running(path)]
    closed = [path for path in records if (path / "exit").is_file()]
    newest = "none"
    if closed:
        record = closed[-1]
        try:
            code = (record / "exit").read_text(encoding="utf-8").strip()
        except OSError as error:
            # exit=ABSENT is the reading for a file that holds nothing;
            # a file that could not be read has no reading at all.
            return f"dispatches: UNREACHABLE: {error}", False
        newest = f"{record.name} exit={code or 'ABSENT'}"
    active = ",".join(running) if running else "none"
    return f"dispatches: running={active}; newest-closed={newest}", True


def handoffs_lines(root: Path) -> tuple[list[str], bool]:
    """The handoff manager's brief, or a measured absence. The store is
    checked before the sibling runs: a read must never create it."""
    # The siblings' own root: the launch binding's XDG_STATE_HOME in a
    # member whose HOME is private, else XDG_STATE_HOME or ~/.local/state.
    # Read through Path.home() there, the lock read none (LD-146).
    state_home = state_root()
    store = state_home / lane_name() / "state.db"
    if not store.is_file():
        return [f"handoffs: ABSENT {store} (none recorded)"], True
    tool = root / "ops/conductor/tools/handoff.py"
    output, absence = bounded_read(
        [sys.executable, str(tool), "brief"], root, "handoffs"
    )
    if output is None:
        return [absence], False
    lines = output.splitlines()
    if not lines or not lines[0].startswith("handoffs:"):
        return ["handoffs: invalid report: the brief does not open with handoffs:"], False
    return lines, True


def branch_line(root: Path) -> tuple[str, bool]:
    branch, branch_reason = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    sha, sha_reason = run(["git", "rev-parse", "--short", "HEAD"], root)
    if branch is None or sha is None:
        return f"branch: {branch_reason or sha_reason}", False
    return f"branch: {branch} {sha}", True


def main() -> int:
    try:
        return brief()
    except Refusal as refusal:
        return emit(refusal)


def brief() -> int:
    if len(sys.argv) != 1:
        refuse(
            "arguments",
            "no command-line arguments",
            repr(sys.argv[1:]),
            "run onboard.py without arguments",
        )
    root = repository_root()
    absences = 0

    print("== guidance")
    for name, description in LAW:
        present = (root / name).is_file() and (root / name).stat().st_size > 0
        state = "OK" if present else "ABSENT"
        absences += not present
        print(f"{state} {name} — {description}")

    print("== state")
    state_lines = (
        register_line(root),
        prs_line(root),
        dispatches_line(),
        branch_line(root),
    )
    for line, measured in state_lines:
        print(line)
        absences += not measured
    lock, lock_measured = lock_lines(root)
    for line in lock:
        print(line)
    absences += not lock_measured

    print("== handoffs")
    handoff_lines, handoffs_measured = handoffs_lines(root)
    for line in handoff_lines:
        print(line)
    absences += not handoffs_measured

    suffix = "" if absences == 0 else f", {absences} absence(s)"
    print(f"brief complete{suffix}")
    return 0 if absences == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
