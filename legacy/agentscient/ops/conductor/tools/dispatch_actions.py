#!/usr/bin/env python3
"""The Dispatcher's launch facade: an ask persisted, launched and followed.

Contract: ops/conductor/sessions-lever.md, "Slice 3 — Dispatcher
integration". Inherits ops/conductor/instrument-conventions.md: the
refusal wire, the four exit classes, identity for writes.

    dispatch_actions.py launch --for A-n --job J --harness H --branch B
                               --scope-ref TEXT --input PATH [--input PATH]... --key K
    dispatch_actions.py status DI-n
    dispatch_actions.py wait DI-n [--timeout S] [--interval S]
    dispatch_actions.py collect DI-n --verify NAME
    dispatch_actions.py recover
    dispatch_actions.py reconcile DI-n

Only the participant holding the `dispatcher` role who also holds the
`dispatcher` machine acts; `status` and `wait` are reads and need no
identity. The intent is reserved and committed before the wrapper runs,
and the store is never held across the spawn: every write is its own
transaction (machine.py), so a launcher that dies leaves a row at
`launching` or `uncertain` that `recover` and `reconcile` settle from
the jobs root, never by launching again.

`VERBS` is the same work shaped as the core's verbs,
`fn(conn, participant, **arguments)`, for `dispatch_mcp.py`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import coordination
    import dispatch_intents as intents
    import laneconf
    from instrument import (
        EXIT_ABORT,
        EXIT_DONE,
        EXIT_FINDING,
        Refusal,
        emit,
        git,
        identity,
        refuse,
        run,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py, coordination.py, dispatcher.py, laneconf.py and "
        "dispatch_intents.py beside this tool; found absent; needed restore them beside "
        "ops/conductor/tools/dispatch_actions.py",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

ROOT = intents.ROOT
JOBS = ROOT / "ops/devlane/task/jobs.json"
VERIFICATIONS = ROOT / "ops/conductor/verifications.json"
RECORD = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
# A refusal line on the wire, bare or behind dispatch.sh's own prefix.
REFUSAL_LINE = re.compile(r"^(dispatch: refusal: )?([a-z][a-z0-9-]*: )?expected .+; found .+")
# The launcher's own argument parser refusing, `launch.py: error: ...`,
# passed through on the wrapper's stderr with its exit (A-524 #1).
PARSER_ERROR = re.compile(r"^\S+: error: .+")
# How long the wrapper may take to print its record: dispatch.sh waits
# DETACH_WAIT (60 s by default) for the launcher's mint, then refuses.
SPAWN_WAIT = 120
READ_WAIT = 60
WAIT_TIMEOUT = 3600
WAIT_INTERVAL = 5
UNLAUNCHED = ("reserved", "launching", "uncertain")


def usage() -> str:
    return ("launch --for A-n --job J --harness H --branch B --scope-ref TEXT --input PATH --key K"
            " | status DI-n | wait DI-n [--timeout S] [--interval S]"
            " | collect DI-n --verify NAME | recover | reconcile DI-n")


def parse(rest, count, *, required=(), optional=(), repeated=()):
    """Positionals and `--name value` options; anything else refuses."""
    names = set(required) | set(optional) | set(repeated)
    positionals, options = [], {}
    index = 0
    while index < len(rest):
        word = rest[index]
        if word.startswith("--"):
            name = word[2:]
            if name not in names:
                refuse("arguments", "the flags " + ", ".join(f"--{n}" for n in sorted(names)),
                       word, usage())
            if index + 1 >= len(rest):
                refuse("arguments", f"a value after {word}", "end of arguments", f"pass {word} VALUE")
            value = rest[index + 1]
            if name in repeated:
                options.setdefault(name, []).append(value)
            elif name in options:
                refuse("arguments", f"{word} once", f"{word} twice", "pass it once")
            else:
                options[name] = value
            index += 2
            continue
        positionals.append(word)
        index += 1
    if len(positionals) != count:
        refuse("arguments", f"{count} positional argument{'s' if count != 1 else ''}",
               " ".join(positionals) or "none", usage())
    missing = [f"--{name}" for name in required if name not in options]
    if missing:
        refuse("arguments", "the flags " + ", ".join(missing), "missing", usage())
    return positionals, options


# -- who may act ---------------------------------------------------------------

# -- the ask's parts -------------------------------------------------------------

def job_spec(job: str) -> dict:
    try:
        table = json.loads(JOBS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        refuse("job", f"the job table at {JOBS}", type(error).__name__,
               "restore ops/devlane/task/jobs.json")
    spec = table.get(job) if isinstance(table, dict) else None
    if not isinstance(spec, dict):
        names = sorted(table) if isinstance(table, dict) else []
        refuse("job", "a job named in jobs.json", job or "empty", "one of " + ", ".join(names))
    return spec


def job_timeout(spec: dict) -> str | None:
    value = (spec.get("caps") or {}).get("timeout")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    return None


def check_harness(harness: str) -> None:
    try:
        laneconf.worker(harness)
    except laneconf.ConfigError as error:
        refuse("harness", "a worker harness of lane.conf's WORKER_HARNESSES",
               harness or "empty", error.needed)


def held_assignment(conn, text: str, branch: str):
    _, aid = coordination.parse_id(text, ("A",))
    row = coordination.assignment_row(conn, aid)
    if row["place"] != "held" or row["holder"] is None:
        refuse("generation", f"A-{aid} open and held at generation {row['generation']}",
               f"A-{aid} {row['place']} at generation {row['generation']}",
               "dispatch for an assignment its holder has claimed")
    if (row["branch"] or "") != branch:
        refuse("arguments", f"--branch {row['branch']}, the branch A-{aid} is held on",
               branch or "empty", "pass the assignment's own branch")
    return row


def input_entries(paths) -> list:
    if not paths:
        refuse("arguments", "--input PATH naming the assignment's text", "none",
               "write the assignment's text to a file and name it")
    entries = []
    for given in paths:
        path = Path(given).resolve()
        try:
            data = path.read_bytes()
        except OSError as error:
            refuse("arguments", "--input naming a readable file", f"{given}: {error.strerror}",
                   "name the file holding the assignment's text")
        entries.append({"path": str(path), "sha256": sha256(data).hexdigest()})
    return entries


def one_line(value: str, flag: str) -> str:
    if not value.strip() or "\n" in value or "\r" in value:
        refuse("arguments", f"{flag} as one line of text", "empty" if not value.strip()
               else "several lines", f"pass {flag} as a single line")
    return value.strip()


# -- the launcher's readings ------------------------------------------------------

def worktree_of(branch: str) -> Path:
    """The checkout of `branch`, where launch.py finds the record; else the root."""
    listed = git("worktree", "list", "--porcelain", cwd=ROOT, timeout=READ_WAIT)
    current = None
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and current:
            return Path(current)
    return ROOT


def refusal_in(stderr: str) -> str | None:
    lines = [line.strip() for line in stderr.splitlines() if REFUSAL_LINE.match(line.strip())]
    return lines[-1] if lines else None


def launcher_refusal(stderr: str) -> str | None:
    """The wrapper's refusal line, else the launcher parser's error line."""
    parser = [line.strip() for line in stderr.splitlines() if PARSER_ERROR.match(line.strip())]
    return refusal_in(stderr) or (parser[-1] if parser else None)


def job_state(launcher, record: str | None) -> str:
    if not record:
        return "unlaunched"
    reading = run([*launcher, "status", record], cwd=ROOT, timeout=READ_WAIT)
    words = reading.stdout.split()
    if reading.returncode != 0 or len(words) < 2 or words[0] != record:
        return "unknown"
    return words[1]


def in_flight(conn, launcher) -> int:
    count = 0
    for found in intents.in_phases(conn, ("launching", "launched")):
        if found["place"] == "launching" or job_state(launcher, found["record"]) == "running":
            count += 1
    return count


def answer(found, **extra) -> dict:
    return {**intents.intent_dict(found), **extra}


# -- the verbs, core-shaped -----------------------------------------------------

def act_launch(conn, part, *, agent, assignment, job, harness, branch, scope_ref, inputs, key) -> dict:
    part = intents.the_dispatcher(conn, part)
    wrapper = intents.program("DISPATCH_WRAPPER", "launcher")
    launcher = intents.program("DISPATCH_LAUNCHER", "launcher")
    intents.program("DISPATCH_MONITOR", "store")
    held = held_assignment(conn, assignment, branch)
    entries = input_entries(inputs)
    scope_ref = one_line(scope_ref, "--scope-ref")
    key = one_line(key, "--key")
    aid, requester = held["id"], held["holder"]
    digest = intents.digest_of(assignment=aid, job=job, harness=harness, branch=branch,
                               scope_ref=scope_ref, inputs=entries)
    existing = intents.by_key(conn, requester, key)
    if existing is not None:
        return answer(intents.repeat(existing, digest), repeated=True)
    cap = intents.capacity()
    count = in_flight(conn, launcher)
    if count >= cap:
        refuse("capacity", f"fewer than {cap} launches in flight", f"{count} in flight",
               "wait for one to finish, or raise DISPATCH_CAPACITY")
    who = coordination.actor(part)
    found, repeated = intents.reserve(
        conn, who, key=key, digest=digest, requester=requester, dispatcher=part["id"],
        assignment=aid, generation=held["generation"], job=job, harness=harness,
        branch=branch, scope_ref=scope_ref, inputs=entries)
    if repeated:
        return answer(found, repeated=True)
    di = found["id"]
    # The job and the harness are judged after the row, so a refused ask
    # is still a record (the contract's `reserved` -> `refused`).
    try:
        spec = job_spec(job)
        check_harness(harness)
    except Refusal as refusal:
        intents.move(conn, di, "refuse-reserved", who, evidence=refusal.line(),
                     error=refusal.line())
        raise
    scope = intents.intent_dir(di) / "scope.txt"
    try:
        scope.parent.mkdir(parents=True, exist_ok=True)
        scope.write_text(f"DI-{di} A-{aid} {job}: {scope_ref}; the assignment's text is the "
                         f"input {entries[0]['path']}\n", encoding="utf-8")
    except OSError as error:
        line = f"scope file {scope}: {error.strerror}"
        intents.move(conn, di, "refuse-reserved", who, evidence=line, error=line)
        refuse("store", "a writable intent directory under the state directory", line,
               "make the state directory writable, then ask again with a new key")
    argv = [*wrapper, "--job", job, "--harness", harness, "--branch", branch,
            "--scope-file", str(scope)]
    for entry in entries:
        argv += ["--input", entry["path"]]
    argv += ["--for", f"A-{aid}", "--agent", part["agent"], "--detach",
             "--correlation", found["correlation"]]
    timeout = job_timeout(spec)
    if timeout is not None:
        argv += ["--timeout", timeout]
    intents.move(conn, di, "launch", who, evidence=f"scope {scope}")
    try:
        child = subprocess.Popen(  # noqa: S603 - argv is a list, never a shell string
            argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as error:
        line = f"{argv[0]}: {error.strerror}"
        intents.move(conn, di, "refuse-launching", who, evidence=line, error=line)
        refuse("launcher", f"the wrapper to launch DI-{di}", line,
               "restore the wrapper, then ask again with a new key")
    ticks = intents.start_ticks(child.pid)
    intents.note(conn, di, launcher_pid=child.pid, launcher_ticks=ticks)
    try:
        stdout, stderr = child.communicate(timeout=SPAWN_WAIT)
    except subprocess.TimeoutExpired:
        # The wrapper ends with the ask (A-525 #4): one left running could
        # mint after the caller read the abort. Its detached launcher is
        # its own session and survives; the correlation in its log is what
        # reconcile finds. A record printed before the end still mints.
        stdout, stderr = ended(child)
        if not any(line.startswith("record: ") for line in stdout.splitlines()):
            reading = (f"no record from the wrapper within {SPAWN_WAIT}s; wrapper "
                       f"pid={child.pid} ended by the ask, exit {child.returncode}")
            return answer(intents.move(conn, di, "lose", who, evidence=reading), reading=reading,
                          repeated=False)
    record = pid = None
    for line in stdout.splitlines():
        if line.startswith("record: "):
            record = line[len("record: "):].strip()
        elif line.startswith("launcher-pid: "):
            pid = line[len("launcher-pid: "):].strip()
    if record and RECORD.match(record):
        launcher_pid = int(pid) if pid and pid.isdigit() else None
        found = intents.move(conn, di, "mint", who, evidence=f"record {record}", record=record,
                             launcher_pid=launcher_pid,
                             launcher_ticks=intents.start_ticks(launcher_pid))
        return answer(found, repeated=False)
    line = launcher_refusal(stderr) if child.returncode != 0 else None
    if line is not None:
        intents.move(conn, di, "refuse-launching", who, evidence=line, error=line)
        refuse("launcher", f"the wrapper to launch DI-{di}", line,
               "act on the wrapper's own needed clause, then ask again with a new key")
    reading = f"exit {child.returncode} without a record line"
    return answer(intents.move(conn, di, "lose", who, evidence=reading), reading=reading,
                      repeated=False)


def act_status(conn, part, *, intent) -> dict:
    found = intents.row(conn, intents.parse_id(intent))
    state = "unlaunched"
    if found["record"]:
        state = job_state(intents.program("DISPATCH_LAUNCHER", "launcher"), found["record"])
    closed = found["outcome"] if found["place"] == "closed" else None
    return answer(found, job_state=state, closed=closed)


def _launched(found, verb: str):
    if found["place"] in UNLAUNCHED:
        refuse("uncertain", f"DI-{found['id']} launched before {verb}",
               f"DI-{found['id']} {found['place']}", f"dispatch_actions.py reconcile DI-{found['id']}")
    if found["place"] == "refused" or not found["record"]:
        refuse("arguments", f"DI-{found['id']} launched before {verb}",
               f"DI-{found['id']} refused: {found['error'] or 'no record'}",
               "ask again with a new key")


def act_wait(conn, part, *, intent, timeout=None, interval=None) -> dict:
    found = intents.row(conn, intents.parse_id(intent))
    _launched(found, "wait")
    launcher = intents.program("DISPATCH_LAUNCHER", "launcher")
    limit = WAIT_TIMEOUT if timeout is None else timeout
    step = WAIT_INTERVAL if interval is None else interval
    for name, value in (("timeout", limit), ("interval", step)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            refuse("arguments", f"--{name} in seconds above zero", repr(value), f"pass --{name} S")
    reading = run([*launcher, "wait", found["record"], "--timeout", f"{limit:g}",
                   "--interval", f"{step:g}"], cwd=worktree_of(found["branch"]),
                  timeout=limit + READ_WAIT)
    line = next((text for text in reversed(reading.stdout.splitlines()) if text.strip()), "")
    if reading.returncode in (0, 1) and line:
        return answer(found, reading=line.strip(), clean=reading.returncode == 0, timeout=False)
    refusal = refusal_in(reading.stderr)
    if reading.returncode == 2 and not line and refusal is None:
        return answer(found, reading=f"wait DI-{found['id']} timeout after {limit:g}",
                      clean=False, timeout=True)
    refuse("launcher", f"launch.py wait to read DI-{found['id']}",
           refusal or f"exit {reading.returncode}", "read the launcher's record by hand")


def verification(name: str) -> list:
    try:
        table = json.loads(VERIFICATIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        refuse("verify", f"the verification table at {VERIFICATIONS}", type(error).__name__,
               "restore ops/conductor/verifications.json")
    argv = table.get(name) if isinstance(table, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(word, str) for word in argv):
        names = sorted(table) if isinstance(table, dict) else []
        refuse("verify", "a verification named in verifications.json", name or "empty",
               "one of " + ", ".join(names))
    return argv


def ended(child) -> tuple[str, str]:
    """End the wrapper's process group, TERM then KILL, and read what it
    printed. The group, not the pid: a child of the wrapper holding its
    stdout would keep the read open past the wrapper's own end. The
    launcher it detached runs in its own session and is not in the group."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, sig)
        try:
            return child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            continue
    child.wait()
    return "", ""


def collected_reading(record: str) -> str | None:
    """The record's own word that the collect happened: `result.collected`
    in <jobs root>/<id>/record.json, which launch.py writes and prints
    nothing about (A-525 #2); None when it is not there to read."""
    try:
        data = json.loads((intents.jobs_root() / record / "record.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    result = data.get("result") if isinstance(data, dict) else None
    collected = result.get("collected") if isinstance(result, dict) else None
    if not isinstance(collected, dict):
        return None
    verify = collected.get("verify") if isinstance(collected.get("verify"), dict) else {}
    return (f"{record} collected head={collected.get('head')} "
            f"verify exit={verify.get('exit')} at={collected.get('at')}")


def act_collect(conn, part, *, agent, intent, verify) -> dict:
    part = intents.the_dispatcher(conn, part)
    found = intents.row(conn, intents.parse_id(intent))
    _launched(found, "collect")
    if found["place"] == "closed":
        refuse("arguments", f"DI-{found['id']} launched and not yet collected",
               f"DI-{found['id']} closed: {found['outcome']}", "read it with status")
    argv = verification(verify)
    launcher = intents.program("DISPATCH_LAUNCHER", "launcher")
    limit = int(job_timeout(job_spec(found["job"])) or WAIT_TIMEOUT) + 10 * READ_WAIT
    reading = run([*launcher, "collect", found["record"], "--verify", shlex.join(argv)],
                  cwd=worktree_of(found["branch"]), timeout=limit)
    line = next((text for text in reversed(reading.stdout.splitlines()) if text.strip()), "")
    if reading.returncode == 0:
        # launch.py collects, exits 0 and prints nothing; the exit is the
        # act and the record its reading, never a printed line.
        line = (collected_reading(found["record"]) or line
                or f"{found['record']} collect exit 0; no result.collected in its record.json")
    elif reading.returncode != 1 or not line:
        refuse("launcher", f"launch.py collect to verify DI-{found['id']}",
               refusal_in(reading.stderr) or f"exit {reading.returncode}",
               "act on the launcher's needed clause")
    closed = intents.move(conn, found["id"], "close", coordination.actor(part),
                          evidence=line.strip(), outcome=line.strip())
    return answer(closed, reading=line.strip(), clean=reading.returncode == 0)


def _settle(conn, found, who: str) -> dict:
    """One row toward `launched` from what the host shows; nothing launched."""
    di = found["id"]
    if found["place"] == "launching":
        reading = intents.process_reading(found["launcher_pid"], found["launcher_ticks"])
        if reading.startswith("alive"):
            return answer(found, reading=f"launcher {reading}")
        found = intents.move(conn, di, "lose", who, evidence=reading)
    if found["place"] != "uncertain":
        return answer(found, reading=f"nothing to reconcile at {found['place']}")
    matches = intents.scan(found["correlation"])
    if len(matches) > 1:
        refuse("correlation", f"one job directory carrying DI-{di}'s correlation",
               ", ".join(str(path) for _, path, _ in matches),
               "the owner settles which job is the intent's; the others are strays")
    if not matches:
        return answer(found, reading=f"no job directory under {intents.jobs_root()} carries "
                      f"the correlation; ask again with a new key")
    record, path, data = matches[0]
    pid = data.get("pid") if isinstance(data.get("pid"), int) else None
    found = intents.move(conn, di, "reconcile", who, evidence=str(path), record=record,
                         launcher_pid=pid, launcher_ticks=intents.start_ticks(pid))
    return answer(found, reading=f"found {path}")


def act_reconcile(conn, part, *, agent, intent) -> dict:
    part = intents.the_dispatcher(conn, part)
    found = intents.row(conn, intents.parse_id(intent))
    if found["place"] not in ("launching", "uncertain", "launched"):
        refuse("arguments", "an intent at launching or uncertain",
               f"DI-{found['id']} {found['place']}", "read it with status")
    return _settle(conn, found, coordination.actor(part))


def act_recover(conn, part, *, agent) -> dict:
    part = intents.the_dispatcher(conn, part)
    who = coordination.actor(part)
    rows = []
    launcher = None
    for found in intents.in_phases(conn, ("launching", "uncertain", "launched")):
        if found["place"] == "launched":
            launcher = launcher or intents.program("DISPATCH_LAUNCHER", "launcher")
            rows.append(answer(found, reading=f"job {job_state(launcher, found['record'])}"))
            continue
        try:
            rows.append(_settle(conn, found, who))
        except Refusal as refusal:
            rows.append(answer(intents.row(conn, found["id"]), reading=refusal.line()))
    return {"intents": rows}


VERBS = {
    "launch": act_launch, "status": act_status, "wait": act_wait,
    "collect": act_collect, "recover": act_recover, "reconcile": act_reconcile,
}


# -- the CLI projection ---------------------------------------------------------

def _writer():
    conn = intents.writer()
    return conn, coordination.bound_participant(conn)


def line_of(result: dict) -> str:
    return f"intent {result['intent']} {result['phase']} record {result['record'] or 'none'}"


def do_launch(rest) -> int:
    _, options = parse(rest, 0, required=("for", "job", "harness", "branch", "scope-ref", "key"),
                       repeated=("input",))
    agent = identity()
    conn, part = _writer()
    result = act_launch(conn, part, agent=agent, assignment=options["for"], job=options["job"],
                        harness=options["harness"], branch=options["branch"],
                        scope_ref=options["scope-ref"], inputs=options.get("input", []),
                        key=options["key"])
    if result["repeated"]:
        print(f"intent {result['intent']} {result['phase']} {result['record'] or 'none'} (repeated)")
        return EXIT_DONE
    if result["phase"] == "launched":
        print(f"intent {result['intent']} launched {result['record']}")
        return EXIT_DONE
    # Stopped mid-act: the wrapper went without a record; the row is the
    # durable partial and reconcile settles it.
    print(f"{line_of(result)} {result.get('reading', '')}".rstrip())
    return EXIT_ABORT


def do_status(rest) -> int:
    positionals, _ = parse(rest, 1)
    result = act_status(intents.reader(), None, intent=positionals[0])
    print(f"{line_of(result)} job {result['job_state']} closed {result['closed'] or 'none'}")
    return EXIT_DONE


def _seconds(options, name):
    raw = options.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        refuse("arguments", f"--{name} in seconds", raw, f"pass --{name} S")


def do_wait(rest) -> int:
    positionals, options = parse(rest, 1, optional=("timeout", "interval"))
    result = act_wait(intents.reader(), None, intent=positionals[0],
                      timeout=_seconds(options, "timeout"), interval=_seconds(options, "interval"))
    print(result["reading"])
    return EXIT_DONE if result["clean"] else EXIT_FINDING


def do_collect(rest) -> int:
    positionals, options = parse(rest, 1, required=("verify",))
    agent = identity()
    conn, part = _writer()
    result = act_collect(conn, part, agent=agent, intent=positionals[0], verify=options["verify"])
    print(f"intent {result['intent']} closed {result['reading']}")
    return EXIT_DONE if result["clean"] else EXIT_FINDING


def do_reconcile(rest) -> int:
    positionals, _ = parse(rest, 1)
    agent = identity()
    conn, part = _writer()
    result = act_reconcile(conn, part, agent=agent, intent=positionals[0])
    print(f"{line_of(result)} {result['reading']}")
    return EXIT_DONE if result["phase"] == "launched" else EXIT_FINDING


def do_recover(rest) -> int:
    parse(rest, 0)
    agent = identity()
    conn, part = _writer()
    for result in act_recover(conn, part, agent=agent)["intents"]:
        print(f"{line_of(result)} {result['reading']}")
    return EXIT_DONE


COMMANDS = {
    "launch": do_launch, "status": do_status, "wait": do_wait, "collect": do_collect,
    "recover": do_recover, "reconcile": do_reconcile,
}


def main(argv: list[str]) -> int:
    try:
        if not argv or argv[0] not in COMMANDS:
            refuse("arguments", "a verb of " + ", ".join(COMMANDS),
                   argv[0] if argv else "none", usage())
        return COMMANDS[argv[0]](argv[1:])
    except Refusal as refusal:
        return emit(refusal)
    except sqlite3.DatabaseError as error:
        return emit(Refusal("store", f"a usable store at {intents.machine.store_path()}",
                            f"{type(error).__name__}: {error}",
                            "retry once the store is free; restore it if it is damaged"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
