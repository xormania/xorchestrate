#!/usr/bin/env python3
"""Managed sessions: one managed native participant per member (slice 1),
and its persistent native conversation (slice 2).

Contract: ops/conductor/sessions-lever.md. Inherits
ops/conductor/instrument-conventions.md: the refusal wire, the four exit
classes, identity for writes. Runs on the state engine (machine.py): the
run and member rows live in `state.db`, never in a file. Stdlib only; no
Symfony, no vendor. It never edits a global harness configuration: every
native home is the private profile `profiles.py prepare-interactive` makes.

    sessions.py doctor [--team FILE]
    sessions.py start --member NAME [--team FILE] [--run R-n] [--worktree PATH] [--mcp-only] [--serena-python FILE]
                      [--project-config FILE] [--ready-timeout SEC] [--tmux-socket NAME]
    sessions.py status [--run R-n] [--member NAME]
    sessions.py attach --member NAME [--run R-n]
    sessions.py stop [--run R-n] [--terminate] [--drain SEC]
    sessions.py export [R-n | --run R-n] [--to DIR]
    sessions.py bridge --run R-n --member NAME [--tick SEC]
    sessions.py join --run R-n --member NAME --participant PT-n [--team FILE]
    sessions.py recover [--run R-n]
    sessions.py input --run R-n --member NAME TEXT
    sessions.py trial objective R-n --deliverable ID --clause FILE:LINE ... --candidate REF
                      --case omitted:FILE:LINE --case changed:REF [--fake NAME ...]
    sessions.py trial record PATH
    sessions.py trial accept R-n --candidate SHA --record PATH
    Every verb takes --profile PATH, as coordination.py does.

Slice 2 (sessions-lever.md, "Slice 2"): a codex or grok member's pane runs
`bridge`, which spawns the harness through the profile, speaks its
protocol through `session_adapters/channel.py` and carries the member's
messages on the native route; a claude member's route is slice 1's
stdout courier. Readiness reads tools, ingress and response. Dispatch
actions are slice 3; a verb this page does not list refuses `arguments`.
Reads need no identity and never create the store.

The tmux handling adapts the proof's operator-owned tmux lifecycle
(Source: /home/scientdb/projects/poc/multi-harness-proof@a944f31,
mhproof/sessions.py `Tmux`): the owner option read on the exact session
component, the owned session and window created detached with the pane id
printed, and attach by `attach-session` or `switch-client`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import queue
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from functools import cache
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import coordination
    import instrument
    import machine
    from coordination import ROLES
    from instrument import (
        EXIT_ABORT,
        EXIT_DONE,
        EXIT_FINDING,
        Refusal,
        emit,
        identity,
        parse_stamp,
        refuse,
        stamp,
        state_dir,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py, machine.py and coordination.py beside this tool; "
        "found absent; needed restore them beside ops/conductor/tools/sessions.py",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ADAPTERS = HERE / "session_adapters"
COORDINATION = HERE / "coordination.py"
PROFILES = REPO / "ops" / "devlane" / "hooks" / "profiles.py"
DEFAULT_TEAM = REPO / "ops" / "conductor" / "team.default.json"
LOCAL_TEAM = REPO / ".dev" / "coordination" / "team.json"

SCHEMA_NAME = "agentscient-team/1"
HARNESSES = ("claude", "codex", "grok")
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
RUN_ID = re.compile(r"^R-(\d+)$")
OWNER_OPTION = "@agentscient_owner"
READINESS = ("process", "profile", "identity", "participant", "role", "tools", "ingress", "response")
LIVE = ("launched", "bound", "ready")
STEP_TIMEOUT = 120
TMUX_TIMEOUT = 10
POLL = 0.2
# A unix socket path of this many bytes or more cannot be bound (sun_path).
SOCKET_LIMIT = 108
# The bridge carries every TICK seconds; a delivery error backs off from
# BACKOFF_START, doubling to BACKOFF_MAX (sessions-lever.md, The bridge).
TICK = 2
BACKOFF_START = 2
BACKOFF_MAX = 60
# The bridge hears at most this many frames before it looks at operator
# input and its carry clock, so a streaming turn never starves either.
FRAMES_PER_PASS = 32
SERVER = "coordination"
# Claude Code's debug log line for the acting server's connection; the
# member's own file first (--debug-file), then the home's debug directory.
CLAUDE_DEBUG = "claude-debug.txt"
TOOLS_LINE = re.compile(r'MCP server "coordination".*connected', re.IGNORECASE)
# Keys the pane never inherits: the operator's own coordination
# capability and harness identity, the tmux client's own markers, and the
# operator's git repository overrides, which would point the member's git
# at whatever tree the operator's shell carried instead of its own (A-538).
WITHHELD = (
    "COORDINATION_CAPABILITY", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SESSION_ID", "GROK_SESSION_ID",
    "AGENTSCIENT_HOOK_PROFILE", "AGENTSCIENT_LAUNCH_SESSION", "TMUX", "TMUX_PANE",
    "GIT_DIR", "GIT_WORK_TREE",
)
SHELL_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NATIVE_REFUSAL = re.compile(r"native: expected the launch session id \S+; found ([^;\s]+);")
LAUNCHER = 'set -a; . "$0"; set +a; rm -f -- "$0"; exec "$@"'

T = machine.Transition
MEMBER = machine.Machine(
    name="session-member",
    version=1,
    initial="reserved",
    places=("reserved", "launched", "bound", "ready", "stopped", "lost"),
    # The contract's stop and lose each leave three places; the engine
    # declares one source per transition, so each is one row per source.
    transitions=(
        T("launch", "reserved", "launched", "any", "required", "place", "keep"),
        T("bind", "launched", "bound", "any", "required", "native", "keep"),
        T("ready", "bound", "ready", "any", "required", "readiness", "keep"),
        *(T(f"stop-{place}", place, "stopped", "any", "required", "place", "keep") for place in LIVE),
        *(T(f"lose-{place}", place, "lost", "any", "required", "place", "keep") for place in LIVE),
    ),
    ttl_s=8 * 3600,
)
RUN = machine.Machine(
    name="session-run",
    version=1,
    initial="open",
    places=("open", "stopping", "stopped"),
    transitions=(
        T("stop", "open", "stopping", "any", "required", "place", "keep"),
        T("close", "stopping", "stopped", "any", "required", "place", "keep"),
    ),
    ttl_s=8 * 3600,
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS session_run(
    id INTEGER PRIMARY KEY AUTOINCREMENT, subject INTEGER NOT NULL UNIQUE, name TEXT NOT NULL, team_file TEXT NOT NULL,
    team_sha256 TEXT NOT NULL, tmux_socket TEXT, tmux_session TEXT NOT NULL,
    started_by TEXT NOT NULL, started_at TEXT NOT NULL, started_participant INTEGER);
CREATE TABLE IF NOT EXISTS session_member(
    id INTEGER PRIMARY KEY AUTOINCREMENT, subject INTEGER NOT NULL UNIQUE, run INTEGER NOT NULL, name TEXT NOT NULL,
    harness TEXT NOT NULL, roles TEXT NOT NULL, generation INTEGER NOT NULL,
    launch_id TEXT, native_id TEXT, participant INTEGER, pid INTEGER,
    start_ticks TEXT, pane TEXT, worktree TEXT NOT NULL, profile TEXT,
    evidence TEXT NOT NULL, readiness TEXT, outcome TEXT,
    started_by TEXT NOT NULL, started_at TEXT NOT NULL);
"""
TABLES = ("session_run", "session_member")


# -- arguments ---------------------------------------------------------------

def usage() -> str:
    return "see the verbs in the module doc of sessions.py"


def parse(rest, *, required=(), optional=(), flags=()):
    """--options only; anything else refuses `arguments`."""
    options = {}
    tokens = list(rest)
    while tokens:
        token = tokens.pop(0)
        if not token.startswith("--"):
            refuse("arguments", "no positional argument", token, usage())
        name = token[2:]
        if name in flags:
            options[name] = True
            continue
        if name not in required and name not in optional:
            refuse("arguments", "a known option", token, usage())
        if not tokens:
            refuse("arguments", f"a value after --{name}", "end of arguments", usage())
        options[name] = tokens.pop(0)
    for name in required:
        if name not in options:
            refuse("arguments", f"--{name}", "absent", usage())
    return options


def seconds(options, name: str, default: int) -> int:
    raw = options.get(name, str(default))
    if not raw.isdigit():
        refuse("arguments", f"--{name} as whole seconds", raw, usage())
    return int(raw)


def run_number(text: str) -> int:
    match = RUN_ID.match(text)
    if match is None:
        refuse("arguments", "a run id like R-1", text, "the run id start or status printed")
    return int(match.group(1))


# -- the team file -------------------------------------------------------------

def team_path(options) -> Path:
    if "team" in options:
        return Path(options["team"]).absolute()
    return LOCAL_TEAM if LOCAL_TEAM.is_file() else DEFAULT_TEAM


def load_team(path: Path) -> tuple[dict, str]:
    """The team file, validated whole, and its sha256; else refuse `team`."""
    needed = f"fix {path} to schema {SCHEMA_NAME} (sessions-lever.md, The team file)"
    try:
        raw = path.read_bytes()
        team = json.loads(raw)
    except (OSError, ValueError) as error:
        refuse("team", f"a readable JSON team file at {path}", f"{type(error).__name__}: {error}", needed)
    if not isinstance(team, dict) or set(team) != {"schema", "run", "members"}:
        refuse("team", "exactly the keys schema, run, members",
               sorted(team) if isinstance(team, dict) else type(team).__name__, needed)
    if team["schema"] != SCHEMA_NAME:
        refuse("team", f"schema {SCHEMA_NAME}", team["schema"], needed)
    header = team["run"]
    if not isinstance(header, dict) or set(header) != {"name", "tmux"} or not all(
            isinstance(header[key], str) and NAME.fullmatch(header[key]) for key in ("name", "tmux")):
        refuse("team", "run with a name and a tmux prefix, each matching [a-z][a-z0-9-]{0,31}",
               header, needed)
    members = team["members"]
    if not isinstance(members, list) or not members:
        refuse("team", "a non-empty members list", members, needed)
    seen = set()
    for member in members:
        if not isinstance(member, dict) or not {"name", "harness", "roles"} <= set(member) or \
                not set(member) <= {"name", "harness", "roles", "executable"}:
            refuse("team", "a member of name, harness, roles and an optional executable", member, needed)
        name = member["name"]
        if not isinstance(name, str) or not NAME.fullmatch(name):
            refuse("team", "a member name matching [a-z][a-z0-9-]{0,31}", name, needed)
        if name in seen:
            refuse("team", "member names unique in the file", f"{name} twice", needed)
        seen.add(name)
        if member["harness"] not in HARNESSES:
            refuse("team", "a harness of " + ", ".join(HARNESSES), member["harness"], needed)
        roles = member["roles"]
        if not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles):
            refuse("team", "a non-empty roles list from " + ", ".join(ROLES), roles, needed)
        executable = member.get("executable")
        if executable is not None and (not isinstance(executable, str) or not os.path.isabs(executable)):
            refuse("team", "an executable given as an absolute path", executable, needed)
    return team, hashlib.sha256(raw).hexdigest()


def team_member(team: dict, name: str, path: Path) -> dict:
    for member in team["members"]:
        if member["name"] == name:
            return member
    names = ", ".join(member["name"] for member in team["members"])
    refuse("team", f"a member of {path}: {names}", name, "name a member the team file declares")


def executable_of(member: dict) -> str | None:
    named = member.get("executable")
    return named or shutil.which(member["harness"])


@cache
def routing():
    """session_adapters/routing.py: the route each member takes, and the
    refusal when two members of a run would share what one instance owns."""
    spec = importlib.util.spec_from_file_location("session_routing", ADAPTERS / "routing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_bindings(conn, run, sid: int) -> list:
    """The run's other members holding a profile, each with its binding: what
    a new member's profile must share nothing with. A reserved member with a
    profile is a launch in flight and counts. A binding that cannot be read
    is passed as {}, so its profile and evidence directories are still
    compared; never dropped as if the member were absent."""
    others = []
    for row in member_rows(conn, rid=run["id"]):
        if row["id"] == sid or not row["profile"] or row["place"] not in ("reserved", *LIVE):
            continue
        try:
            binding = json.loads((Path(row["profile"]) / "binding.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            binding = {}
        others.append((dict(row), binding if isinstance(binding, dict) else {}))
    return others


@cache
def load_adapter(harness: str):
    path = ADAPTERS / f"{harness}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"session_adapter_{harness}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Adapter()


# -- the store ---------------------------------------------------------------

def writer() -> sqlite3.Connection:
    try:
        # The coordination core owns its schema; a cold store gains it here
        # so the profile's SessionStart finds a store to bind into. No
        # participant is written.
        conn = coordination.writer()
        conn.executescript(SCHEMA)
        held = {row[1] for row in conn.execute("PRAGMA table_info(session_run)")}
        if "started_participant" not in held:
            conn.execute("ALTER TABLE session_run ADD COLUMN started_participant INTEGER")
    except sqlite3.DatabaseError as error:
        refuse("store", f"a readable SQLite store at {machine.store_path()}", str(error),
               "restore or move the store aside; the engine never rebuilds one to order")
    machine.declare(conn, RUN)
    machine.declare(conn, MEMBER)
    return conn


def reader() -> sqlite3.Connection | None:
    """The store read-only, or None when it holds no runs; never created."""
    path = machine.store_path()
    if not path.is_file():
        return None
    try:
        conn = instrument.connect_store(path, timeout=machine.BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        held = {row["name"] for row in
                conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    except sqlite3.DatabaseError as error:
        refuse("store", f"a readable SQLite store at {path}", str(error),
               "restore or move the store aside; a read never rebuilds it")
    if not all(table in held for table in TABLES):
        conn.close()
        return None
    return conn


PARTICIPANT_BY = {
    "session_id": "SELECT * FROM participants WHERE session_id = ?",
    "capability_digest": "SELECT * FROM participants WHERE capability_digest = ?",
}


def participant_by(conn, column: str, value: str):
    """A coordination participant row, or None when the store has none."""
    if conn is None:
        return None
    try:
        return conn.execute(PARTICIPANT_BY[column], (value,)).fetchone()
    except sqlite3.OperationalError:
        return None


def coordination_reader() -> sqlite3.Connection | None:
    path = machine.store_path()
    if not path.is_file():
        return None
    try:
        conn = instrument.connect_store(path, timeout=machine.BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.DatabaseError:
        return None


def run_row(conn, rid: int):
    return conn.execute(
        "SELECT r.*, s.place FROM session_run r JOIN subjects s ON s.id = r.subject WHERE r.id = ?",
        (rid,),
    ).fetchone()


def member_rows(conn, *, rid=None, name=None):
    sql = ("SELECT m.*, s.place FROM session_member m JOIN subjects s ON s.id = m.subject "
           "WHERE 1 = 1")
    args: list = []
    if rid is not None:
        sql += " AND m.run = ?"
        args.append(rid)
    if name is not None:
        sql += " AND m.name = ?"
        args.append(name)
    return conn.execute(sql + " ORDER BY m.id", args).fetchall()


def chosen_run(conn, options, *, places=("open", "stopping")):
    """The run named by --run, else the newest run in one of `places`."""
    if "run" in options:
        rid = run_number(options["run"])
        row = run_row(conn, rid) if conn is not None else None
        if row is None:
            refuse("absent", "a run of this store", options["run"], "sessions.py status lists the runs")
        return row
    if conn is not None:
        for row in conn.execute(
                "SELECT r.*, s.place FROM session_run r JOIN subjects s ON s.id = r.subject "
                "ORDER BY r.id DESC"):
            if row["place"] in places:
                return row
    refuse("absent", "a run in place " + " or ".join(places), "none", "sessions.py start --member NAME")


def member_dir(rid: int, name: str, generation: int) -> Path:
    base = state_dir() / "sessions" / f"R-{rid}" / name
    return base if generation == 1 else base / f"gen-{generation}"


# -- processes and tmux ----------------------------------------------------------

start_ticks = coordination.process_ticks


def socket_args(socket: str | None) -> list[str]:
    if not socket:
        return []
    return ["-S", socket] if "/" in socket else ["-L", socket]


def quiet_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in WITHHELD}


def tmux(socket: str | None, *args: str, timeout: float = TMUX_TIMEOUT) -> subprocess.CompletedProcess:
    check_socket(socket)
    binary = shutil.which("tmux")
    if binary is None:
        refuse("tmux", "tmux on PATH", "absent", "install tmux; sessions run in an owned tmux session")
    try:
        return subprocess.run(  # noqa: S603 - argv is a list, never a shell string
            [binary, *socket_args(socket), *args], capture_output=True, text=True,
            timeout=timeout, env=quiet_env(), check=False,
        )
    except subprocess.TimeoutExpired:
        refuse("tmux", f"tmux answering within {timeout}s", f"no answer to {args[0]}",
               "inspect the tmux server on the named socket")


def ownership(run) -> str:
    """`absent` for no such session, `owned`, or the owner option actually found."""
    name = run["tmux_session"]
    if tmux(run["tmux_socket"], "has-session", "-t", f"={name}").returncode != 0:
        return "absent"
    # Option commands parse a target pane; the colon makes the exact
    # session component explicit (the proof's Tmux.owned).
    shown = tmux(run["tmux_socket"], "show-options", "-qv", "-t", f"={name}:", OWNER_OPTION)
    value = shown.stdout.strip()
    return "owned" if value == f"R-{run['id']}" else (value or "unset")


def refuse_unowned(run, found: str) -> None:
    refuse("ownership", f"tmux session {run['tmux_session']} with {OWNER_OPTION} R-{run['id']}",
           f"{OWNER_OPTION} {found}",
           "the supervisor never sets, kills or attaches a session it does not own; "
           "rename or close that session yourself, or start another run")


def clear_dead_window(run, name: str) -> None:
    """One window per member, named by the member: a later generation takes
    the name from its predecessor's dead window, whose capture is already
    in that generation's pane.log. tmux cannot resolve `=session:=name`
    while a dead window and a new one share the name (measured, tmux 3.4),
    so the dead one goes first; a live pane under the name refuses."""
    session = run["tmux_session"]
    listed = tmux(run["tmux_socket"], "list-windows", "-t", f"={session}:",
                  "-F", "#{window_id} #{pane_dead} #{pane_id} #{window_name}")
    if listed.returncode != 0:
        refuse("tmux", f"tmux to list the windows of {session}",
               listed.stderr.strip() or f"exit {listed.returncode}",
               "inspect the tmux server on the run's socket")
    for line in listed.stdout.splitlines():
        window, dead, pane, found = (line.split(" ", 3) + ["", "", ""])[:4]
        if found != name:
            continue
        if dead != "1":
            refuse("place", f"no live pane in window {name} of {session}", f"pane {pane} live",
                   f"sessions.py stop --run R-{run['id']} first")
        killed = tmux(run["tmux_socket"], "kill-window", "-t", window)
        if killed.returncode != 0:
            refuse("tmux", f"tmux to close the dead window {window} of {name}",
                   killed.stderr.strip() or f"exit {killed.returncode}",
                   "inspect the tmux server on the run's socket")


def launcher(argv: list[str], directory: Path, extra=None) -> tuple[list[str], Path]:
    """The pane's command: source a private env file, delete it, exec the argv.

    Every environment value reaches the pane through a mode-600 file, never
    a command line; the operator's capability and harness ids never reach it.
    `extra` overlays the environment: a bridge's profile authority values.
    """
    lines = ["unset " + " ".join(WITHHELD) + " 2>/dev/null"]
    for key, value in sorted({**quiet_env(), **(extra or {})}.items()):
        if SHELL_KEY.fullmatch(key) and key not in ("_", "PWD", "OLDPWD", "SHLVL"):
            lines.append(f"{key}={shlex.quote(value)}")
    path = directory / "launch.env"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return ["/bin/sh", "-c", LAUNCHER, str(path), *argv], path


# -- readiness -----------------------------------------------------------------

def profile_reading(profile: str | None) -> str:
    if not profile:
        return "waiting profile"
    binding = Path(profile) / "binding.json"
    checked = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
        [sys.executable, str(PROFILES), "check", "--binding", str(binding)],
        capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False,
    )
    if checked.returncode == 0:
        return f"ok {profile}"
    line = (checked.stderr or checked.stdout).strip().splitlines()
    return "refused profile " + (line[0] if line else f"exit {checked.returncode}")


def profile_digest(profile: str | None) -> str | None:
    if not profile:
        return None
    try:
        binding = json.loads((Path(profile) / "binding.json").read_text(encoding="utf-8"))
        return hashlib.sha256(binding["coordination_capability"].encode("ascii")).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def native_refusal(evidence: str) -> str | None:
    """The native id the profile's SessionStart refused, read from the pane.

    `initialize` refuses a native id that is not the launch id before it
    binds anything, so the store never shows it; the refusal line is in
    the member's pane capture.
    """
    try:
        text = (Path(evidence) / "pane.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = NATIVE_REFUSAL.search(text)
    return match.group(1) if match else None


def process_reading(pid: int | None, ticks: str | None) -> str:
    """Liveness is the pid and its recorded start ticks, both present and matching."""
    if pid is None:
        return "waiting launch"
    if ticks is None:
        return f"refused no start ticks recorded for pid={pid}"
    current = start_ticks(pid)
    if current is None:
        return f"refused gone pid={pid} start={ticks}"
    if current != ticks:
        return f"refused reused pid={pid} start={current}, recorded {ticks}"
    return f"ok pid={pid} start={ticks}"


def alive(pid: int | None, ticks: str | None) -> bool:
    return process_reading(pid, ticks).startswith("ok")


def readiness(member, *, check_profile: bool = True) -> dict[str, str]:
    """The eight dimensions, read now (sessions-lever.md, Readiness, and
    Slice 2's tools, ingress and response)."""
    found: dict[str, str] = {}
    managed = not joined(member)
    if managed:
        found["process"] = process_reading(member["pid"], member["start_ticks"])
        found["profile"] = profile_reading(member["profile"]) if check_profile else (
            f"ok {member['profile']}" if member["profile"] else "waiting profile")
    else:
        found["process"] = "unmanaged"
        found["profile"] = "unmeasured unmanaged"
    adapter = load_adapter(member["harness"])
    conn = coordination_reader()
    try:
        # The native id is the adapter's to observe; the participant row
        # is then read under that id. A joined member's is the one recorded.
        if not managed:
            native = member["native_id"]
        else:
            native = (adapter.observe_identity(conn, member["launch_id"])
                      if adapter is not None and conn is not None and member["launch_id"] else None)
        bound = participant_by(conn, "session_id", native) if native else None
        digest = profile_digest(member["profile"])
        other = participant_by(conn, "capability_digest", digest) if digest else None
        delivery = delivery_readiness(member, adapter, bound, conn)
    finally:
        if conn is not None:
            conn.close()
    refused = native_refusal(member["evidence"])
    # A bridge binds under the native id its handshake returned, which it
    # records before the bind; a TUI binds under the launch id.
    ours = {member["launch_id"], member["native_id"]} - {None}
    if other is not None and other["session_id"] not in ours:
        found["identity"] = f"refused native {other['session_id']}, launched as {member['launch_id']}"
    elif refused is not None and bound is None:
        found["identity"] = f"refused native {refused}, launched as {member['launch_id']}"
    elif bound is not None:
        found["identity"] = f"ok {bound['session_id']}"
    else:
        found["identity"] = f"waiting bind of {member['launch_id'] or 'no launch'}"
    if bound is None:
        found["participant"] = "waiting bind"
        found["role"] = "waiting participant"
    else:
        found["participant"] = f"ok PT-{bound['id']}"
        held = set(bound["roles"].split(","))
        missing = [role for role in member["roles"].split(",") if role not in held]
        found["role"] = (f"ok {bound['roles']}" if not missing
                         else f"waiting grant PT-{bound['id']} --roles {','.join(missing)}")
    found.update(delivery)
    return found


SLICE1 = ("process", "profile", "identity", "participant")
SLICE2 = ("tools", "ingress", "response")


def is_ready(found: dict[str, str]) -> bool:
    """Slice 1's dimensions ok, role ok or waiting a lead's grant, and each of
    slice 2's three ok or unmeasured; a waiting or refused one is not ready
    (XO 87's ruling of 2026-09-23 on sessions-lever.md, Slice 2)."""
    head = all(found[d].startswith("ok") for d in SLICE1)
    role = found["role"].startswith("ok") or found["role"].startswith("waiting grant")
    return head and role and all(found[d].startswith(("ok", "unmeasured")) for d in SLICE2)


def enforcement(member, found: dict[str, str]) -> str:
    """The ninth line status prints (slice 4, the owner's terminals): `ok
    <profile>` only once the hook profile is observed, never by default.

    Observed means the profile checks ok and the participant was bound
    under the member's own launch id: only the profile's SessionStart hook
    binds so (`initialize` compares the launch id), so the bind is the
    hook's act. A bridge binds under the native id its handshake returned,
    and a joined member has no profile of ours; both read unmeasured."""
    if joined(member):
        return "unmeasured joined, no hook profile of ours"
    if (found["profile"].startswith("ok") and member["launch_id"]
            and found["identity"] == f"ok {member['launch_id']}"):
        return f"ok {member['profile']}"
    return "unmeasured no bind under the launch id by the profile's hook seen"


def readiness_lines(found: dict[str, str]) -> list[str]:
    return [f"  {dimension} {found[dimension]}" for dimension in READINESS]


# -- verbs ---------------------------------------------------------------------

def do_doctor(rest):
    options = parse(rest, optional=("team",))
    path = team_path(options)
    team, _ = load_team(path)
    lines = []
    for tool in ("tmux", "bwrap"):
        found = shutil.which(tool)
        if found is None:
            lines.append(f"missing {tool}: not on PATH")
            continue
        flag = "-V" if tool == "tmux" else "--version"
        try:
            version = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
                [found, flag], capture_output=True, text=True, timeout=TMUX_TIMEOUT, check=False,
            ).stdout.strip() or "version unread"
        except (OSError, subprocess.TimeoutExpired) as error:
            version = f"version unread: {error}"
        lines.append(f"ok {tool}: {found} {version}")
    store = machine.store_path()
    if not store.is_file():
        lines.append(f"ok state store: absent at {store}")
        lines.append("ok coordination store: absent, so no lead is bound")
    else:
        conn = coordination_reader()
        try:
            conn.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchall()
            lines.append(f"ok state store: {store}")
            try:
                leads = [row["id"] for row in conn.execute("SELECT id, roles FROM participants")
                         if "lead" in row["roles"].split(",")]
                lines.append("ok coordination store: " + (
                    "lead " + ", ".join(f"PT-{pid}" for pid in leads) + " bound" if leads
                    else "no lead bound"))
            except sqlite3.OperationalError:
                lines.append("ok coordination store: no participants table, so no lead is bound")
        except (sqlite3.DatabaseError, AttributeError) as error:
            lines.append(f"refused state store: {store} unreadable: {error}")
        finally:
            if conn is not None:
                conn.close()
    lines.append(f"ok python: {sys.executable} {sys.version.split()[0]}")
    for member in team["members"]:
        label = f"{member['name']} ({member['harness']})"
        exe = executable_of(member)
        if exe and os.path.isfile(exe):
            digest = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
            lines.append(f"ok executable {label}: {exe} sha256={digest}")
        else:
            lines.append(f"missing executable {label}: {exe or member['harness'] + ' not on PATH'}")
        module = ADAPTERS / f"{member['harness']}.py"
        if not module.is_file():
            lines.append(f"missing adapter {label}: no session_adapters/{member['harness']}.py; "
                         "slice 2 closes it")
            continue
        lines.append(f"ok adapter {label}: {module.relative_to(REPO)}")
        if exe and os.path.isfile(exe):
            # The harness is probed by its adapter, never by the supervisor.
            reading = load_adapter(member["harness"]).probe(exe)
            version = str(reading.get("version", "unresolved no version read"))
            state = "refused" if version.startswith("unresolved") else "ok"
            lines.append(f"{state} probe {label}: {version}; capability {reading.get('capability')}")
    for line in lines:
        print(line)
    return EXIT_DONE if all(line.startswith("ok ") for line in lines) else EXIT_FINDING


def check_authority(member: dict) -> str | None:
    """None, or the lead's own PT-n when this shell carries the lead capability.

    A role above worker is the live lead's to grant: from the lead's own
    shell here, else by coordination.py grant afterwards. A capability that
    is not the lead's refuses `authority`.
    """
    above = [role for role in member["roles"] if role != "worker"]
    capability = os.environ.get("COORDINATION_CAPABILITY", "")
    if not above or not capability:
        return None
    remedy = (f"start without COORDINATION_CAPABILITY and let the lead run "
              f"coordination.py grant PT-n --roles {','.join(above)} once {member['name']} binds, "
              "or run start from the lead's own shell")
    if not re.fullmatch(r"[0-9a-f]{64}", capability):
        refuse("authority", "the lead's capability for " + ",".join(above), "a malformed capability", remedy)
    conn = coordination_reader()
    try:
        row = participant_by(conn, "capability_digest",
                             hashlib.sha256(capability.encode("ascii")).hexdigest())
    finally:
        if conn is not None:
            conn.close()
    if row is None or "lead" not in row["roles"].split(","):
        held = "no participant" if row is None else f"PT-{row['id']} holding {row['roles']}"
        refuse("authority", f"the lead's capability to grant {','.join(above)} to {member['name']}",
               f"a capability of {held}", remedy)
    return f"PT-{row['id']}"


def check_worktree(options) -> Path:
    if "worktree" not in options:
        return REPO
    path = Path(options["worktree"]).absolute()
    needed = "name a git worktree of this repository (git worktree list)"
    if not path.is_dir():
        refuse("worktree", f"a git worktree of {REPO}", f"{path}: no such directory", needed)
    common = []
    for where in (path, REPO):
        done = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
            ["git", "-C", str(where), "rev-parse",  # noqa: S607 - git from PATH, as every lane tool
             "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False,
        )
        common.append(Path(done.stdout.strip()).resolve() if done.returncode == 0 else None)
    if common[0] is None or common[0] != common[1]:
        refuse("worktree", f"a git worktree of {REPO}", f"{path}: not a worktree of it", needed)
    return path


def launched_elsewhere(conn, name: str):
    return conn.execute(
        "SELECT m.id, m.run, s.place FROM session_member m JOIN subjects s ON s.id = m.subject "
        "JOIN session_run sr ON sr.id = m.run JOIN subjects r ON r.id = sr.subject "
        "WHERE m.name = ? AND s.place IN ('launched', 'bound', 'ready') "
        "AND r.place IN ('open', 'stopping') ORDER BY m.id DESC LIMIT 1", (name,),
    ).fetchone()


def step(argv: list[str], log: Path) -> subprocess.CompletedProcess:
    done = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
        argv, capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False,
    )
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {shlex.join(argv)}\nexit {done.returncode}\n{done.stderr}")
    return done


def last_line(done: subprocess.CompletedProcess) -> str:
    lines = (done.stderr or done.stdout).strip().splitlines()
    return lines[-1] if lines else f"exit {done.returncode}, no output"


def do_start(rest):
    options = parse(rest, required=("member",),
                    optional=("team", "run", "worktree", "ready-timeout", "tmux-socket", "serena-python", "project-config"), flags=("mcp-only",))
    if options.get("serena-python") and not options.get("mcp-only"):
        refuse("arguments", "--mcp-only with --serena-python", "unselected private MCP profile",
               "select --mcp-only for prod-lane code work")
    if options.get("project-config") and not options.get("mcp-only"):
        refuse("arguments", "--mcp-only with --project-config", "unselected project integration",
               "select --mcp-only for named project operations")
    check_socket(options.get("tmux-socket") or os.environ.get("AGENTSCIENT_TMUX_SOCKET"))
    agent = identity()
    path = team_path(options)
    team, team_sha = load_team(path)
    member = team_member(team, options["member"], path)
    timeout = seconds(options, "ready-timeout", 120)
    if shutil.which("tmux") is None:
        refuse("tmux", "tmux on PATH", "absent", "install tmux; sessions run in an owned tmux session")
    worktree = check_worktree(options)
    adapter = load_adapter(member["harness"])
    if adapter is None:
        refuse("team", "a member whose harness has an adapter under session_adapters/",
               f"{member['name']} on {member['harness']}", "add the harness's adapter")
    lead = check_authority(member)
    starter = starter_participant()
    starter_id = starter["id"] if starter is not None else None
    conn = writer()
    with conn:
        clash = launched_elsewhere(conn, member["name"])
        if clash is not None:
            refuse("place", f"{member['name']} not launched in an open run",
                   f"SM-{clash['id']} in R-{clash['run']} at {clash['place']}",
                   f"sessions.py stop --run R-{clash['run']} first")
        if "run" in options:
            run = run_row(conn, run_number(options["run"]))
            if run is None:
                refuse("absent", "a run of this store", options["run"], "sessions.py status lists the runs")
            if run["place"] != "open":
                refuse("place", "an open run", f"R-{run['id']} at {run['place']}", "start a new run")
            # A run is one started team: its file and that file's sha256.
            if (run["team_file"], run["team_sha256"]) != (str(path), team_sha):
                refuse("team", f"the team file R-{run['id']} was started from, "
                       f"{run['team_file']} sha256 {run['team_sha256']}",
                       f"{path} sha256 {team_sha}",
                       f"pass --team {run['team_file']} unchanged, or start a new run without --run")
        else:
            subject = machine.create(conn, RUN, agent, note=f"team {path}")
            socket = options.get("tmux-socket") or os.environ.get("AGENTSCIENT_TMUX_SOCKET") or None
            rid = conn.execute(
                "INSERT INTO session_run(subject, name, team_file, team_sha256, tmux_socket, tmux_session, "
                "started_by, started_at, started_participant) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)",
                (subject, team["run"]["name"], str(path), team_sha, socket, agent, stamp(), starter_id),
            ).lastrowid
            conn.execute("UPDATE session_run SET tmux_session = ? WHERE id = ?",
                         (f"{team['run']['tmux']}-R-{rid}", rid))
            run = run_row(conn, rid)
        owner = ownership(run)
        if owner not in ("absent", "owned"):
            if "run" not in options:
                machine.move(conn, RUN, run["subject"], "stop", agent, evidence="ownership refused before launch")
                machine.move(conn, RUN, run["subject"], "close", agent, evidence="no members")
            refuse_unowned(run, owner)
        previous = member_rows(conn, rid=run["id"], name=member["name"])
        generation = previous[-1]["generation"] + 1 if previous and previous[-1]["place"] != "reserved" else 1
        if previous and previous[-1]["place"] == "reserved":
            sid, generation = previous[-1]["id"], previous[-1]["generation"]
        else:
            subject = machine.create(conn, MEMBER, agent, note=f"R-{run['id']} {member['name']}")
            sid = conn.execute(
                "INSERT INTO session_member(subject, run, name, harness, roles, generation, worktree, "
                "evidence, started_by, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subject, run["id"], member["name"], member["harness"], ",".join(member["roles"]),
                 generation, str(worktree), str(member_dir(run["id"], member["name"], generation)),
                 agent, stamp()),
            ).lastrowid
    return launch(conn, agent, team, member, run, sid, generation, worktree, adapter, lead, timeout,
                  mcp_only=options.get("mcp-only", False), serena_python=options.get("serena-python"), project_config=options.get("project-config"))


def launch(conn, agent, team, member, run, sid, generation, worktree, adapter, lead, timeout, *, mcp_only=False, serena_python=None, project_config=None):
    directory = member_dir(run["id"], member["name"], generation)
    if directory.exists() and any(directory.iterdir()):
        directory.rename(directory.with_name(f"{directory.name}.attempt-{time.time_ns()}"))
    directory.mkdir(parents=True, exist_ok=True)
    stderr_log = directory / "stderr.log"
    executable = executable_of(member)
    if not executable:
        refuse("profile", f"a {member['harness']} executable on PATH or in the team file",
               "absent", f"install {member['harness']} or name its absolute path as executable")
    prepared = step([sys.executable, str(PROFILES), "prepare-interactive", "--harness", member["harness"],
                     "--destination", str(directory), "--cwd", str(worktree),
                     "--executable", executable, "--agent", agent, *(["--mcp-only"] if mcp_only else []),
                     *(["--serena-python", serena_python] if serena_python else []),
                     *(["--project-config", project_config] if project_config else [])], stderr_log)
    if prepared.returncode != 0:
        refuse("profile", "profiles.py prepare-interactive to prepare the private profile",
               last_line(prepared),"fix what the profile refusal names, then start again")
    binding_path = Path(prepared.stdout.strip().splitlines()[-1])
    checked = step([sys.executable, str(PROFILES), "check", "--binding", str(binding_path)], stderr_log)
    if checked.returncode != 0:
        refuse("profile", "profiles.py check to accept the prepared profile",
               last_line(checked),"fix what the profile refusal names, then start again")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    profile = str(binding_path.parent)
    # Several members of one harness in one run: each routes by its own
    # instance, and a profile sharing anything with another refuses here,
    # before a pane opens (sessions-lever.md slice 2, "A route").
    route = routing().select({**dict(member_row(conn, sid)), "profile": profile}, binding,
                             adapter.transport(), others=run_bindings(conn, run, sid))
    launch_id = str(uuid.uuid4())
    extra = {}
    if adapter.transport() == "protocol":
        # The pane runs the member's bridge, which spawns the harness; the
        # bridge acts under the profile's authority values. PATH stays the
        # operator's: the bridge resolves bwrap and nothing else by it.
        argv = [sys.executable, str(Path(__file__).resolve()), "bridge",
                "--run", f"R-{run['id']}", "--member", member["name"]]
        extra = {key: value for key, value in binding["authority_environment"].items()
                 if key != "PATH"}
    else:
        argv = [str(part) for part in adapter.argv(member, binding, launch_id)]
    pane_log = directory / "pane.log"
    pane_log.touch(mode=0o600)
    # The bridge reads its launch and profile from the row, so both are
    # written before the pane can start.
    with conn:
        conn.execute("UPDATE session_member SET launch_id = ?, profile = ? WHERE id = ?",
                     (launch_id, profile, sid))
    command, env_file = launcher(argv, directory, extra)
    session = run["tmux_session"]
    window = f"={session}:={member['name']}"
    owned = ownership(run)
    if owned == "owned":
        try:
            clear_dead_window(run, member["name"])
        except Refusal:
            env_file.unlink(missing_ok=True)
            raise
        # The dead window may have been the session's last: then the
        # session is gone and this start opens it again, owned.
        owned = ownership(run)
    if owned == "owned":
        opening = ["new-window", "-d", "-P", "-F", "#{pane_id} #{pane_pid}", "-t", f"={session}:",
                   "-n", member["name"], "-c", tmux_literal(str(worktree)), *command]
    elif owned == "absent":
        opening = ["new-session", "-d", "-P", "-F", "#{pane_id} #{pane_pid}", "-s", session,
                   "-n", member["name"], "-c", tmux_literal(str(worktree)), *command,
                   ";", "set-option", "-t", f"={session}:", OWNER_OPTION, f"R-{run['id']}"]
    else:
        env_file.unlink(missing_ok=True)
        refuse_unowned(run, owned)
    # One tmux invocation, so the window keeps its dead pane and its
    # capture before the process in it can exit. tmux expands `-c` and the
    # pipe-pane command as formats (measured, tmux 3.4), so `#` is doubled.
    created = tmux(run["tmux_socket"], *opening,
                   ";", "set-option", "-w", "-t", window, "remain-on-exit", "on",
                   ";", "pipe-pane", "-o", "-t", window,
                   tmux_literal("cat >> " + shlex.quote(str(pane_log))))
    printed = created.stdout.split()
    if created.returncode != 0 or len(printed) < 2 or not printed[1].isdigit():
        env_file.unlink(missing_ok=True)
        refuse("tmux", f"tmux to open window {member['name']} in {session}",
               (created.stderr or created.stdout).strip() or f"exit {created.returncode}",
               "inspect the tmux server on the run's socket")
    pane, pid = printed[0], int(printed[1])
    ticks = None
    deadline = time.monotonic() + 5
    while ticks is None and time.monotonic() < deadline:
        ticks = start_ticks(pid)
        if ticks is None:
            time.sleep(0.02)
    record = {
        "run": f"R-{run['id']}", "member": member["name"], "sm": f"SM-{sid}",
        "generation": generation, "pane": pane, "pid": pid, "start_ticks": ticks,
        "launch_id": launch_id, "argv": argv, "tmux_socket": run["tmux_socket"],
        "tmux_session": session, "worktree": str(worktree), "profile": profile,
        "route": route["route"], "launched_at": stamp(), "launched_by": agent,
    }
    private_write(directory / "launch.json", json.dumps(record, indent=1) + "\n")
    with conn:
        conn.execute(
            "UPDATE session_member SET launch_id = ?, pid = ?, start_ticks = ?, pane = ?, profile = ? "
            "WHERE id = ?", (launch_id, pid, ticks, pane, profile, sid))
        machine.move(conn, MEMBER, member_row(conn, sid)["subject"], "launch", agent, evidence=f"pane {pane} pid {pid}")
    return settle(conn, agent, member, run, sid, generation, pane, lead, timeout)


def member_row(conn, sid: int):
    return conn.execute(
        "SELECT m.*, s.place FROM session_member m JOIN subjects s ON s.id = m.subject WHERE m.id = ?",
        (sid,),
    ).fetchone()


def settle(conn, agent, member, run, sid, generation, pane, lead, timeout):
    """Wait for identity and participant; bind, probe, grant through the lead, ready."""
    deadline = time.monotonic() + timeout
    granted = False
    starter = starter_participant()
    while True:
        row = member_row(conn, sid)
        if row["place"] == "bound" and row["participant"] is not None:
            send_probe(agent, row, run, starter)
        found = readiness(row, check_profile=False)
        if found["identity"].startswith("refused native"):
            record_readiness(conn, sid, found)
            refuse("native", f"the native session the launch named, {row['launch_id']}",
                   found["identity"].removeprefix("refused native "),
                   "relaunch the profile; never rebind it (sessions.py stop --terminate, then start)")
        if row["place"] == "launched" and found["participant"].startswith("ok"):
            with conn:
                conn.execute("UPDATE session_member SET native_id = ?, participant = ? WHERE id = ?",
                             (found["identity"].split()[1],
                              int(found["participant"].split("PT-")[1]), sid))
                machine.move(conn, MEMBER, row["subject"], "bind", agent,
                             evidence=f"native {found['identity'].split()[1]} {found['participant'].split()[1]}")
            continue
        if found["role"].startswith("waiting grant") and lead and not granted:
            granted = True
            missing = found["role"].split("--roles ")[1]
            subprocess.run(  # noqa: S603 - argv is a list, never a shell string
                [sys.executable, str(COORDINATION), "grant", found["participant"].split()[1],
                 "--roles", missing], capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False,
            )
            continue
        if row["place"] == "bound" and found["process"].startswith("ok"):
            found["profile"] = profile_reading(row["profile"])
            if is_ready(found):
                with conn:
                    machine.move(conn, MEMBER, row["subject"], "ready", agent,
                                 evidence="; ".join(f"{d} {found[d]}" for d in READINESS))
                return report(conn, member, run, sid, generation, pane, found, EXIT_DONE)
        if not found["process"].startswith("ok"):
            # A pane that exited never reads ready, whatever else bound.
            record_readiness(conn, sid, found)
            refuse("readiness", f"process ok for {member['name']} SM-{sid} until ready",
                   f"process {found['process']}; " + "; ".join(
                       f"{d} {found[d]}" for d in READINESS if d != "process"),
                   f"read {member_dir(run['id'], member['name'], generation) / 'pane.log'}, "
                   "then sessions.py stop --terminate and start again")
        if time.monotonic() >= deadline:
            if row["place"] == "bound":
                found["profile"] = profile_reading(row["profile"])
            return report(conn, member, run, sid, generation, pane, found, EXIT_FINDING)
        time.sleep(POLL)


def record_readiness(conn, sid: int, found: dict[str, str]) -> None:
    with conn:
        conn.execute("UPDATE session_member SET readiness = ? WHERE id = ?", (json.dumps(found), sid))


def report(conn, member, run, sid, generation, pane, found, code):
    record_readiness(conn, sid, found)
    participant = found["participant"].split()[1] if found["participant"].startswith("ok") else "-"
    print(f"started {member['name']} SM-{sid} as {participant} in R-{run['id']} "
          f"pane {pane} gen {generation}")
    for line in readiness_lines(found):
        print(line)
    return code


def do_status(rest):
    options = parse(rest, optional=("run", "member"))
    conn = reader()
    if conn is None:
        print("no runs")
        return EXIT_DONE
    with conn:
        if "run" in options:
            runs = [chosen_run(conn, options)]
        else:
            runs = conn.execute(
                "SELECT r.*, s.place FROM session_run r JOIN subjects s ON s.id = r.subject ORDER BY r.id"
            ).fetchall()
        if not runs:
            print("no runs")
            return EXIT_DONE
        for run in runs:
            print(f"R-{run['id']} {run['place']} {run['name']} tmux {run['tmux_session']}")
            for member in member_rows(conn, rid=run["id"], name=options.get("member")):
                found = readiness(member, check_profile=member["place"] in LIVE and not joined(member))
                alive = ("ok" if found["process"].startswith("ok")
                         else found["process"] if found["process"] == "unmanaged" else "gone")
                participant = f"PT-{member['participant']}" if member["participant"] else "-"
                print(f"R-{run['id']} {member['name']} SM-{member['id']} {member['place']} "
                      f"{participant} gen {member['generation']} pane {member['pane'] or '-'} "
                      f"process {alive}")
                for line in readiness_lines(found):
                    print(line)
                print(f"  enforcement {enforcement(member, found)}")
                print(f"  {route_line(conn, member['participant'])}")
                print(f"  {attempts_line(conn, member['participant'])}")
                for line in ambiguous_lines(conn, member["participant"]):
                    print(f"  {line}")
    return EXIT_DONE


def newest_member(conn, run, name: str):
    rows = member_rows(conn, rid=run["id"], name=name)
    if not rows:
        refuse("absent", f"member {name} in R-{run['id']}", "none", "sessions.py status --run R-n")
    return rows[-1]


def do_attach(rest):
    options = parse(rest, required=("member",), optional=("run",))
    identity()
    conn = reader()
    try:
        run = chosen_run(conn, options, places=("open",))
        member = newest_member(conn, run, options["member"])
    finally:
        if conn is not None:
            conn.close()
    if joined(member):
        refuse("unmanaged", f"a pane the supervisor owns for {member['name']}",
               f"SM-{member['id']} joined as PT-{member['participant']}",
               "attach to that participant's own terminal")
    if not member["pane"]:
        refuse("absent", f"a pane for {member['name']} SM-{member['id']}", "none",
               "sessions.py start --member NAME")
    needed = f"sessions.py status --run R-{run['id']}; start {member['name']} again after stop"
    owner = ownership(run)
    if owner == "absent":
        refuse("absent", f"the pane of {member['name']} SM-{member['id']} in tmux session "
               f"{run['tmux_session']}", "no such session", needed)
    if owner != "owned":
        refuse_unowned(run, owner)
    window = f"={run['tmux_session']}:={member['name']}"
    shown = tmux(run["tmux_socket"], "select-window", "-t", window)
    if shown.returncode != 0:
        refuse("absent", f"the pane of {member['name']} SM-{member['id']} in tmux session "
               f"{run['tmux_session']}", shown.stderr.strip() or f"exit {shown.returncode}", needed)
    # The exact window was proved above; tmux resolves a window name
    # exactly before it tries a prefix, so the attach names it plainly.
    target = f"={run['tmux_session']}:{member['name']}"
    verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
    binary = shutil.which("tmux")
    os.execv(binary, [binary,  # noqa: S606 - attach hands this terminal to tmux, argv a list
                      *socket_args(run["tmux_socket"]), verb, "-t", target])
    return EXIT_DONE


def do_stop(rest):
    options = parse(rest, optional=("run", "drain"), flags=("terminate",))
    agent = identity()
    drain = seconds(options, "drain", 0)
    terminate = bool(options.get("terminate"))
    looked = reader()
    if looked is None:
        refuse("absent", "a run of this store", options.get("run", "none"), "sessions.py status")
    try:
        run = chosen_run(looked, options)
    finally:
        looked.close()
    if run["place"] == "stopped":
        refuse("place", "an open or stopping run", f"R-{run['id']} at stopped", "nothing to stop")
    # Ownership is judged before any act: a session of the run's name
    # that this run does not own refuses before a move, a signal or a line.
    owner = ownership(run)
    if owner not in ("owned", "absent"):
        refuse_unowned(run, owner)
    starter = starter_participant()
    conn = writer()
    with conn:
        run = run_row(conn, run["id"])
        if run["place"] == "open":
            machine.move(conn, RUN, run["subject"], "stop", agent,
                         evidence="terminate" if terminate else f"drain {drain}s")
        members = [row for row in member_rows(conn, rid=run["id"]) if row["place"] in LIVE]
    asked: dict[int, int] = {}
    if starter is not None:
        # From a bound shell each live member hears the stop before the drain.
        for row in members:
            if row["participant"] is not None:
                sent = inform(agent, row["participant"], f"stop requested R-{run['id']}")
                print(f"R-{run['id']} {row['name']} SM-{row['id']} stop requested: {sent}")
                if re.fullmatch(r"M-\d+", sent):
                    asked[row["id"]] = int(sent[2:])
    # The drain waits for the managed members to exit and for each joined
    # member's own reply to its stop request, whichever is later, up to --drain.
    deadline = time.monotonic() + drain
    while not terminate and time.monotonic() < deadline and any(
            alive(row["pid"], row["start_ticks"])
            or (joined(row) and acknowledgment(conn, row, asked) == "unacknowledged")
            for row in members):
        time.sleep(POLL)
    outcomes = []
    for row in members:
        adapter = load_adapter(row["harness"])
        if joined(row):
            # A joined participant is not the supervisor's to signal: it
            # leaves the run and its own terminal keeps it.
            outcome = "unresolved unmanaged"
            outcome = f"{outcome} {acknowledgment(conn, row, asked)}"
            with conn:
                machine.move(conn, MEMBER, row["subject"], f"stop-{row['place']}", agent, evidence=outcome)
                conn.execute("UPDATE session_member SET outcome = ? WHERE id = ?", (outcome, row["id"]))
            outcomes.append(outcome)
            print(f"R-{run['id']} {row['name']} SM-{row['id']} {outcome}")
            continue
        if row["pid"] is None:
            outcome = "unresolved no pid recorded"
        elif row["start_ticks"] is None:
            outcome = f"unresolved no start ticks recorded for pid {row['pid']}, never signalled"
        elif not terminate:
            outcome = ("stopped exited" if not alive(row["pid"], row["start_ticks"])
                       else f"unresolved running pid {row['pid']}")
        else:
            outcome = adapter.stop({"pid": row["pid"], "start": row["start_ticks"]}, True)
        # Re-read at the act: the window is killed only while still owned.
        if terminate and ownership(run) == "owned":
            window = f"={run['tmux_session']}:={row['name']}"
            if tmux(run["tmux_socket"], "has-session", "-t", window).returncode == 0:
                tmux(run["tmux_socket"], "kill-window", "-t", window)
        with conn:
            if outcome.startswith("stopped"):
                machine.move(conn, MEMBER, row["subject"], f"stop-{row['place']}", agent, evidence=outcome)
            elif terminate:
                machine.move(conn, MEMBER, row["subject"], f"lose-{row['place']}", agent, evidence=outcome)
            conn.execute("UPDATE session_member SET outcome = ? WHERE id = ?", (outcome, row["id"]))
        outcomes.append(outcome)
        print(f"R-{run['id']} {row['name']} SM-{row['id']} {outcome}")
    with conn:
        left = [row for row in member_rows(conn, rid=run["id"]) if row["place"] in LIVE]
        if not left:
            settled = ", ".join(f"SM-{row['id']} {row['place']}" for row in member_rows(conn, rid=run["id"]))
            machine.move(conn, RUN, run["subject"], "close", agent, evidence=settled or "no members")
            print(f"R-{run['id']} stopped")
    return EXIT_DONE if all(outcome.startswith("stopped") for outcome in outcomes) and not left \
        else EXIT_FINDING


def acknowledgment(conn, row, asked: dict[int, int]) -> str:
    """Whether a joined member acknowledged its stop request (step 8).

    The "stop requested" inform asks for no ack, so the reading is the
    member's own reply in the store: an `inform` it sent with `--reply-to`
    naming the request, `acknowledged <its queued_at>`, else `unacknowledged`,
    never a default (the 2026-09-19 debrief's F6)."""
    message = asked.get(row["id"])
    if message is None:
        return "unacknowledged no stop request reached it"
    reply = conn.execute(
        "SELECT queued_at FROM messages WHERE reply_to = ? AND sender = ? AND purpose = 'inform' "
        "ORDER BY id LIMIT 1", (message, row["participant"]),
    ).fetchone()
    return f"acknowledged {reply['queued_at']}" if reply else "unacknowledged"


def secrets_of(profile: str | None) -> list[str]:
    """The binding's capability and every credential_environment value."""
    if not profile:
        return []
    try:
        binding = json.loads((Path(profile) / "binding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    values = [binding.get("coordination_capability")]
    values += list((binding.get("credential_environment") or {}).values())
    return sorted({str(value) for value in values if value}, key=len, reverse=True)


def scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[omitted]")
    return text


def run_starter(run) -> int | None:
    """The participant that started the run; None on a row from a store
    made before session_run carried it."""
    return run["started_participant"] if "started_participant" in run.keys() else None  # noqa: SIM118 - sqlite3.Row's `in` tests values


def window(conn, run) -> tuple[str, str]:
    """The run's window (slice 4, the run export): from its started_at to
    the earliest stamp that exists of its stop request (the move to
    stopping), the next run its starter started, and now."""
    ends = [stamp()]
    stopped = conn.execute(
        "SELECT MIN(at) AS at FROM marking WHERE machine = ? AND subject = ? AND transition = 'stop'",
        (RUN.name, run["subject"])).fetchone()
    if stopped is not None and stopped["at"]:
        ends.append(stopped["at"])
    starter = run_starter(run)
    if starter is not None:
        later = conn.execute(
            "SELECT MIN(started_at) AS at FROM session_run WHERE started_participant = ? "
            "AND started_at > ? AND id != ?", (starter, run["started_at"], run["id"])).fetchone()
        if later is not None and later["at"]:
            ends.append(later["at"])
    return run["started_at"], min(ends)


def launched_records(conn, span: tuple[str, str], participants: set[int]) -> list:
    """The dispatch records the run launched: the store's coord_launch rows
    (slice 3's intents) that reached a record, requested or dispatched by
    one of the run's participants, reserved inside the run's window. A
    store with no intents table has launched none."""
    if not participants:
        return []
    marks = ",".join("?" for _ in participants)
    try:
        return conn.execute(
            "SELECT id, record, requester, dispatcher FROM dispatch_intents "  # noqa: S608 - placeholders only
            f"WHERE record IS NOT NULL AND reserved_at >= ? AND reserved_at < ? "
            f"AND (requester IN ({marks}) OR dispatcher IN ({marks})) ORDER BY id",
            (*span, *participants, *participants)).fetchall()
    except sqlite3.OperationalError:
        return []


def run_rows(conn, sql: str, participants: set[int], span: tuple[str, str]) -> list[dict]:
    """A coordination table's rows for the run's participants inside the
    run's window, or none; the window's two stamps bind last."""
    if not participants:
        return []
    marks = ",".join("?" for _ in participants)
    try:
        bound = [*participants] * sql.count("{marks}") + [*span]
        return [dict(row) for row in conn.execute(sql.format(marks=marks), bound)]
    except sqlite3.OperationalError:
        return []


def do_export(rest):
    """The run export (slice 4, the shapes): every managed member's pane log
    and every dispatch record the run launched, named, or refused `export`
    before anything is written. A joined member has no pane log and is
    listed `joined PT-n no pane log`, a declared absence."""
    rest = list(rest)
    positional = rest.pop(0) if rest and not rest[0].startswith("--") else None
    options = parse(rest, optional=("run", "to"))
    if positional is not None:
        if "run" in options and options["run"] != positional:
            refuse("arguments", "one run", f"{positional} and --run {options['run']}", usage())
        options["run"] = positional
    identity()
    conn = reader()
    try:
        run = chosen_run(conn, options, places=("open", "stopping", "stopped"))
        members = member_rows(conn, rid=run["id"])
        participants = {row["participant"] for row in members if row["participant"] is not None}
        if run_starter(run) is not None:
            participants.add(run_starter(run))
        span = window(conn, run)
        records = launched_records(conn, span, participants)
        decisions = run_rows(conn, "SELECT * FROM decisions WHERE (asked_by IN ({marks}) "
                             "OR decided_by IN ({marks})) AND asked_at >= ? AND asked_at < ? "
                             "ORDER BY id", participants, span)
        observations = run_rows(conn, "SELECT * FROM observations WHERE producer IN ({marks}) "
                                "AND at >= ? AND at < ? ORDER BY id", participants, span)
    finally:
        if conn is not None:
            conn.close()
    import dispatch_intents
    jobs = dispatch_intents.jobs_root()
    missing = []
    for row in members:
        if not joined(row) and not (Path(row["evidence"]) / "pane.log").is_file():
            missing.append(f"{row['name']} SM-{row['id']} has no pane.log at {Path(row['evidence']) / 'pane.log'}")
    for found in records:
        if not (jobs / found["record"]).is_dir():
            missing.append(f"DI-{found['id']} record {found['record']} is not under {jobs}")
    if missing:
        refuse("export", "every managed member's pane log and every record the run launched",
               "; ".join(missing), "restore them, or export before the jobs root is reclaimed")
    dest = (Path(options["to"]) if "to" in options
            else state_dir() / "runs" / f"R-{run['id']}" / "export").absolute()
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    secrets = sorted({s for row in members for s in secrets_of(row["profile"])}, key=len, reverse=True)
    failures: list[str] = []
    files = 0
    named: list[str] = []

    def write(relative: str, text: str) -> None:
        nonlocal files
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(target, scrub(text, secrets))
        files += 1

    described = []
    for row in members:
        found = readiness(row, check_profile=False)
        entry = dict(row)
        entry["readiness"] = found
        entry["process_identity"] = {"pid": row["pid"], "start_ticks": row["start_ticks"],
                                     "alive": found["process"].startswith("ok")}
        described.append(entry)
        if joined(row):
            named.append(f"{row['name']} joined PT-{row['participant']} no pane log")
            continue
        folder = row["name"] if row["generation"] == 1 else f"{row['name']}-gen-{row['generation']}"
        adapter = load_adapter(row["harness"])
        retained = list(adapter.diagnostics(row["evidence"]) if adapter is not None else [])
        pane = str(Path(row["evidence"]) / "pane.log")
        if pane not in retained:
            retained.append(pane)
        exported = []
        for source in retained:
            name = Path(source).name
            try:
                text = Path(source).read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                failures.append(f"{row['name']} {name}: {type(error).__name__}: {error}")
                continue
            write(f"{folder}/{name}", text)
            exported.append(f"{folder}/{name}")
            if name == "pane.log":
                named.append(f"{row['name']} pane log {dest / folder / name}")
        write(f"{folder}/diagnostics.json", json.dumps(exported, indent=1) + "\n")
    for found in records:
        named.append(f"DI-{found['id']} record {found['record']}")
    summary = {
        "run": dict(run),
        "team_sha256": run["team_sha256"],
        "tmux": {"socket": run["tmux_socket"], "session": run["tmux_session"]},
        "members": described,
        "window": {"from": span[0], "before": span[1], "participants": sorted(participants)},
        "records": [dict(found) for found in records],
        "decisions": decisions,
        "observations": observations,
        "telemetry": "not exported: the run's telemetry is read from each record under the jobs root",
        "exported_at": stamp(),
        "omitted": "each binding's coordination_capability and credential_environment values",
    }
    write("run.json", json.dumps(summary, indent=1, default=str) + "\n")
    for line in named:
        print(line)
    if failures:
        print(f"partial {dest} ({len(members)} members, {files} files); first failure: {failures[0]}")
        return EXIT_FINDING
    print(f"exported {dest} ({len(members)} members, {files} files) {len(records)} records")
    return EXIT_DONE


# -- slice 2: shared helpers -------------------------------------------------------

def tmux_literal(text: str) -> str:
    """A string tmux reads as a format with every `#` kept: `##` is a
    literal `#`, so a path naming one never expands (A-492)."""
    return text.replace("#", "##")


def socket_path(socket: str) -> Path:
    """Where tmux binds a socket named by -S (a path) or -L (a name)."""
    if "/" in socket:
        return Path(socket).absolute()
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"  # noqa: S108 - tmux's own default directory
    return Path(base) / f"tmux-{os.getuid()}" / socket


def check_socket(socket: str | None) -> None:
    """A unix socket path of SOCKET_LIMIT bytes or more refuses `socket`
    before tmux is called: the kernel would refuse the bind."""
    if not socket:
        return
    path = socket_path(socket)
    size = len(str(path).encode("utf-8"))
    if size >= SOCKET_LIMIT:
        refuse("socket", f"a unix socket path under {SOCKET_LIMIT} bytes",
               f"{size} bytes: {path}",
               "name a shorter --tmux-socket, or set TMUX_TMPDIR to a shorter directory")


def private_write(path: Path, text: str) -> None:
    """A file only its owner reads, whatever the umask (A-492)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)


def joined(row) -> bool:
    """A member `join` recorded: a participant with no launch of ours."""
    return row["launch_id"] is None and row["native_id"] is not None


def bridge_state(member) -> dict:
    try:
        state = json.loads((Path(member["evidence"]) / "bridge.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def route_of(conn, participant: int | None):
    """The live-or-dead route row of a participant, or None."""
    if conn is None or participant is None:
        return None
    try:
        return conn.execute("SELECT * FROM delivery_routes WHERE participant = ?",
                            (participant,)).fetchone()
    except sqlite3.OperationalError:
        return None


def route_line(conn, participant: int | None) -> str:
    row = route_of(conn, participant)
    if row is None:
        return "route none"
    state = "alive" if coordination.holder_alive(row) else "dead"
    return f"route {row['route']} pid={row['pid']} since {row['since']}, {state}"


def attempts_line(conn, participant: int | None) -> str:
    counts = dict.fromkeys(("reserved", "written", "accepted", "uncertain"), 0)
    if conn is not None and participant is not None:
        try:
            rows = conn.execute("SELECT outcome FROM delivery_attempts WHERE participant = ?",
                                (participant,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            word = (row["outcome"] or "reserved").split()[0]
            if word in counts:
                counts[word] += 1
    return "attempts " + " ".join(f"{word} {count}" for word, count in counts.items())


def ambiguous_lines(conn, participant: int | None) -> list[str]:
    """`ambiguous recipient <role>: PT-a, PT-b` for each role message two
    live holders could carry; read as the member's own participant."""
    if conn is None or participant is None:
        return []
    try:
        rows = conn.execute(
            "SELECT m.id, m.recipient FROM messages m JOIN subjects s ON s.id = m.id "
            "WHERE m.delivered_at IS NULL AND s.place = 'open' ORDER BY m.id").fetchall()
        mine = conn.execute("SELECT roles FROM participants WHERE id = ?", (participant,)).fetchone()
    except sqlite3.OperationalError:
        return []
    if mine is None:
        return []
    lines = []
    for row in rows:
        if row["recipient"] not in mine["roles"].split(","):
            continue
        holders = coordination.live_holders(conn, row["recipient"])
        if len(holders) > 1:
            lines.append(f"ambiguous recipient {row['recipient']}: "
                         + ", ".join(f"PT-{pid}" for pid in holders) + f" (M-{row['id']})")
    return lines


@cache
def adapter_common():
    """The adapters' shared module, for the envelope the bridge submits."""
    path = ADAPTERS / "__init__.py"
    spec = importlib.util.spec_from_file_location("session_adapters_bridge_common", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def starter_participant():
    """The participant this shell is bound as, or None when it is unbound
    or its binding does not authenticate."""
    conn = coordination_reader()
    if conn is None:
        return None
    try:
        return coordination.maybe_participant(conn)
    except (Refusal, sqlite3.Error):
        return None
    finally:
        conn.close()


def inform(agent: str, participant: int, body: str) -> str:
    """Queue an `inform` from this shell's participant; the M-n, or the refusal line."""
    try:
        conn = coordination.writer()
    except sqlite3.Error as error:
        return f"store: {error}"
    try:
        shown = coordination.CORE["send"](conn, coordination.bound_participant(conn),
                                          agent=agent, to=f"PT-{participant}",
                                          purpose="inform", body=body)
    except Refusal as refusal:
        return refusal.line()
    except sqlite3.Error as error:
        return f"store: {error}"
    finally:
        conn.close()
    return shown["message"]


# -- slice 2: the three delivery readings ---------------------------------------------

def claude_tools(member) -> str:
    """The coordination server's connection line in Claude Code's debug log
    for this launch, under the profile's home or the authority home."""
    if not member["profile"] or not member["launch_id"]:
        return "waiting tools"
    try:
        binding = json.loads((Path(member["profile"]) / "binding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "waiting tools"
    homes = [binding.get("home"), (binding.get("authority_environment") or {}).get("HOME")]
    logs = [Path(member["evidence"]) / CLAUDE_DEBUG]
    logs += [Path(home) / ".claude" / "debug" / f"{member['launch_id']}.txt"
             for home in dict.fromkeys(home for home in homes if home)]
    read = False
    for log in logs:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        read = True
        if TOOLS_LINE.search(text):
            return f"ok {SERVER} connected, {log}"
    # No log at all is a reading the lane cannot take yet, not a wait: the
    # profile does not admit --debug-file (XO 87, 2026-09-23).
    return "waiting tools" if read else "unmeasured no debug log"


def arms_courier(member) -> bool:
    """Whether the member's profile names `courier.py watch` in any of its
    files: the start line a Claude member arms its route from (Required of
    the wall, 3)."""
    if not member["profile"]:
        return False
    for path in Path(member["profile"]).glob("*.json"):
        try:
            if "courier.py watch" in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def response_reading(member, conn) -> str:
    try:
        probe = json.loads((Path(member["evidence"]) / "probe.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "waiting probe"
    message = probe.get("message")
    if not message:
        why = probe.get("why") or "no starter participant"
        # A send the store refused is not a measurement: not ready, and
        # send_probe sends again (A-522 #4).
        if why.startswith(PROBE_REFUSED):
            return f"waiting probe, the send refused: {why.removeprefix(PROBE_REFUSED)}"
        return f"unmeasured {why}"
    try:
        row = conn.execute("SELECT queued_at, acked_at, acked_by FROM messages WHERE id = ?",
                           (int(message.split("-")[1]),)).fetchone() if conn is not None else None
    except (sqlite3.OperationalError, ValueError, IndexError):
        row = None
    if row is None or not row["acked_at"] or row["acked_by"] != member["participant"]:
        return f"waiting ack {message}"
    latency = (parse_stamp(row["acked_at"]) - parse_stamp(row["queued_at"])).total_seconds()
    return f"ok {message} {latency:.1f}s"


def delivery_readiness(member, adapter, bound, conn) -> dict[str, str]:
    """tools, ingress and response (sessions-lever.md, slice 2)."""
    found: dict[str, str] = {}
    protocol = adapter is not None and adapter.transport() == "protocol"
    state = bridge_state(member)
    if joined(member):
        found["tools"] = "unmeasured unmanaged"
    elif protocol:
        found["tools"] = state.get("tools") or "waiting tools"
    else:
        found["tools"] = claude_tools(member)
    last = str(state.get("last_attempt") or "")
    route = route_of(conn, bound["id"]) if bound is not None else None
    if protocol and last.startswith("unsupported"):
        found["ingress"] = f"refused channel {last}"
    elif route is not None and coordination.holder_alive(route):
        found["ingress"] = f"ok {route['route']} pid={route['pid']}"
    elif not protocol and not joined(member) and not arms_courier(member):
        found["ingress"] = "unmeasured the profile arms no courier"
    else:
        found["ingress"] = "waiting courier"
    found["response"] = response_reading(member, conn)
    return found


PROBE_REFUSED = "probe refused: "


def send_probe(agent: str, row, run, starter) -> None:
    """After `bound`, the readiness probe: an `inform` to the member from
    the starter's own participant, recorded in probe.json either way. A
    send the store refused is sent again on the next call, in this start's
    wait or the next start's; any other record stands."""
    directory = Path(row["evidence"])
    path = directory / "probe.json"
    if path.exists():
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = {}
        if not str(held.get("why") or "").startswith(PROBE_REFUSED):
            return
    record = {"run": f"R-{run['id']}", "member": f"SM-{row['id']}", "at": stamp()}
    if starter is None:
        record.update(message=None, why="no starter participant")
    else:
        nonce = uuid.uuid4().hex[:12]
        sent = inform(agent, row["participant"],
                      f"readiness probe R-{run['id']} SM-{row['id']} {nonce}")
        if sent.startswith("M-"):
            record.update(message=sent, nonce=nonce, starter=f"PT-{starter['id']}")
        else:
            record.update(message=None, why=f"{PROBE_REFUSED}{sent}")
    directory.mkdir(parents=True, exist_ok=True)
    private_write(path, json.dumps(record, indent=1) + "\n")


# -- slice 2: the bridge ------------------------------------------------------------

def guard_session_start(binding, native: str, directory: Path) -> None:
    """The profile's own SessionStart with the bridge's payload, so
    `initialize` binds the participant under the profile's capability."""
    payload = {"hook_event_name": "SessionStart", "session_id": native,
               "cwd": binding["cwd"], "source": "startup"}
    env = {key: value for key, value in os.environ.items() if key not in WITHHELD}
    env["AGENTSCIENT_LAUNCH_SESSION"] = native
    argv = ["/bin/sh", str(binding["helper"]), "SessionStart"]
    try:
        done = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
            argv, input=json.dumps(payload), capture_output=True, text=True,
            timeout=STEP_TIMEOUT, env=env, check=False,
        )
        text = f"$ {shlex.join(argv)}\nexit {done.returncode}\n{done.stdout}{done.stderr}"
    except (OSError, subprocess.TimeoutExpired) as error:
        text = f"$ {shlex.join(argv)}\n{type(error).__name__}: {error}\n"
    fd = os.open(str(directory / "guard.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(text)


def act_as(binding, native: str) -> None:
    """This process acts as the member's participant from here on: the
    profile's capability and the native id, nothing inherited."""
    for key in WITHHELD:
        os.environ.pop(key, None)
    os.environ["COORDINATION_CAPABILITY"] = binding["coordination_capability"]
    os.environ["CLAUDE_SESSION_ID"] = native


class Bridge:
    """The loop of one protocol member: frames, operator lines, carries."""

    def __init__(self, adapter, rpc, member, directory: Path, tick: int, *, mcp_only=False):
        self.adapter = adapter
        self.rpc = rpc
        self.member = member
        self.directory = directory
        self.tick = tick
        self.mcp_only = mcp_only
        self.busy = False
        self.mid_line = False
        self.backoff = 0
        self.lines: queue.Queue = queue.Queue()
        self.state = {"native": adapter.native, "tools": None, "last_attempt": None,
                      "degraded": None, "backoff": 0, "updated_at": stamp()}

    def save(self) -> None:
        self.state["updated_at"] = stamp()
        self.state["backoff"] = self.backoff
        private_write(self.directory / "bridge.json", json.dumps(self.state, indent=1) + "\n")

    def say(self, text: str) -> None:
        if self.mid_line:
            print(flush=True)
            self.mid_line = False
        print(text, flush=True)

    def read_stdin(self) -> None:
        for line in sys.stdin:
            text = line.rstrip("\n")
            if text:
                self.lines.put(text)

    def heard(self, reading: str) -> None:
        if reading.startswith("output "):
            print(reading[len("output "):], end="", flush=True)
            self.mid_line = True
            return
        if reading.startswith(f"tool {SERVER}"):
            self.state["tools"] = f"ok {reading[len('tool '):]}"
            self.save()
        if reading == "activity active":
            self.busy = True
            return
        if reading.startswith("done") or reading == "activity idle":
            self.busy = False
        self.say(f"· {reading}")

    def submit(self, row) -> str:
        outcome = self.adapter.submit(self.rpc, adapter_common().envelope(row, mcp_only=self.mcp_only), busy=self.busy)
        if outcome.startswith(("accepted", "written")):
            self.busy = True
        self.say(f"· carried {row['message']}: {outcome}")
        return outcome

    def observe(self, message: str) -> str:
        return self.adapter.observe(self.rpc, {"reconcile": message})

    def carry(self) -> float:
        """One pass; the seconds until the next. A delivery error never
        ends the bridge: it records, backs off and reads degraded."""
        import courier
        try:
            courier.route_step(coordination, "pass")
            passed = courier.carry(coordination, self.submit, observe=self.observe,
                                   generation=self.member["generation"])
        except Refusal as refusal:
            if refusal.kind == "route":
                raise
            passed = [{"message": None, "outcome": f"failed {refusal.line()}"}]
            self.say(f"· {refusal.line()}")
        except sqlite3.OperationalError as error:
            # A store another session holds past the busy timeout ("database
            # is locked") is a delivery error: back off, never exit.
            passed = [{"message": None, "outcome": f"failed store: {error}"}]
            self.say(f"· store: {error}")
        settled = [entry["outcome"] for entry in passed if entry.get("attempt")]
        if settled:
            self.state["last_attempt"] = settled[-1]
        bad = [outcome for outcome in (entry["outcome"] for entry in passed)
               if outcome.split()[0] in ("unsupported", "failed", "uncertain")]
        if bad:
            self.backoff = min(max(BACKOFF_START, self.backoff * 2), BACKOFF_MAX)
            self.state["degraded"] = bad[-1]
        elif settled:
            self.backoff = 0
            self.state["degraded"] = None
        self.save()
        return self.backoff or self.tick

    def loop(self) -> int:
        threading.Thread(target=self.read_stdin, name="bridge-stdin", daemon=True).start()
        due = time.monotonic()
        while True:
            heard = 0
            while heard < FRAMES_PER_PASS:
                frame = self.rpc.next_frame(POLL if heard == 0 else 0)
                if frame is None:
                    break
                self.heard(self.adapter.observe(self.rpc, frame))
                heard += 1
            if heard == 0 and self.rpc.dead.is_set():
                self.say(f"bridge: the harness closed: {self.rpc.failure}")
                return EXIT_ABORT
            while not self.lines.empty():
                outcome = self.adapter.submit(self.rpc, self.lines.get(), busy=self.busy)
                if outcome.startswith(("accepted", "written")):
                    self.busy = True
                self.say(f"· input {outcome}")
            if time.monotonic() >= due:
                due = time.monotonic() + self.carry()


def stop_signal(signum, frame):
    raise KeyboardInterrupt


def do_bridge(rest):
    """A codex or grok member's own process, in its pane. `start` launches it
    under the profile's authority values except PATH, which stays the
    operator's: the bridge resolves bwrap by it and nothing else."""
    options = parse(rest, required=("run", "member"), optional=("tick",))
    identity()
    tick = seconds(options, "tick", TICK)
    conn = writer()
    run = chosen_run(conn, options, places=("open",))
    member = newest_member(conn, run, options["member"])
    adapter = load_adapter(member["harness"])
    transport = adapter.transport() if adapter is not None else "none"
    if transport != "protocol":
        refuse("transport", "a protocol member, codex or grok, for the bridge",
               f"{member['name']} on {member['harness']}, transport {transport}",
               "a claude member has no bridge: its route is courier.py watch in its own pane")
    if joined(member):
        refuse("unmanaged", f"a member the supervisor launched for the bridge of {member['name']}",
               f"SM-{member['id']} joined as PT-{member['participant']}",
               "a joined participant carries its own route")
    if not member["profile"]:
        refuse("profile", f"a prepared profile for {member['name']} SM-{member['id']}", "none",
               "sessions.py start --member NAME prepares it and launches the bridge")
    binding = json.loads((Path(member["profile"]) / "binding.json").read_text(encoding="utf-8"))
    directory = Path(member["evidence"])
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(directory / "channel.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGHUP, stop_signal)
    with os.fdopen(fd, "ab") as channel_log:
        rpc = adapter.connect(adapter.argv(member, binding, member["launch_id"]),
                              cwd=member["worktree"], stderr=channel_log)
        try:
            return bridge_session(conn, adapter, rpc, member, run, binding, directory, tick)
        except KeyboardInterrupt:
            return EXIT_DONE
        finally:
            rpc.close()


def bridge_session(conn, adapter, rpc, member, run, binding, directory, tick):
    import courier
    native = adapter.handshake(rpc)
    with conn:
        conn.execute("UPDATE session_member SET native_id = ? WHERE id = ?", (native, member["id"]))
    private_write(directory / "native.json", json.dumps(
        {"native": native, "launch_id": member["launch_id"], "harness": member["harness"],
         "at": stamp()}, indent=1) + "\n")
    guard_session_start(binding, native, directory)
    looked = coordination_reader()
    try:
        bound = participant_by(looked, "session_id", native)
    finally:
        if looked is not None:
            looked.close()
    digest = profile_digest(member["profile"])
    if bound is None or bound["capability_digest"] != digest:
        found = ("no participant" if bound is None
                 else f"PT-{bound['id']} under another capability")
        refuse("native", f"a participant bound to native {native} under this profile's capability",
               found, f"read {directory / 'guard.log'}; the profile's SessionStart binds it")
    act_as(binding, native)
    held = courier.take_route(coordination, "native", member=f"SM-{member['id']}")
    bridge = Bridge(adapter, rpc, member, directory, tick, mcp_only=binding.get("mcp_only", False))
    bridge.save()
    print(f"bridge {member['name']} SM-{member['id']} R-{run['id']} native {native} "
          f"as PT-{bound['id']}; route native pid={held['pid'] if held else 'none'}", flush=True)
    try:
        return bridge.loop()
    finally:
        if held is not None:
            try:
                courier.route_step(coordination, "release")
            except Refusal as refusal:
                print(refusal.line(), flush=True)


# -- slice 2: join, recover, input ------------------------------------------------------

def do_join(rest):
    options = parse(rest, required=("run", "member", "participant"), optional=("team",))
    agent = identity()
    path = team_path(options)
    team, _ = load_team(path)
    member = team_member(team, options["member"], path)
    _, pid = coordination.parse_id(options["participant"], "PT")
    looked = coordination_reader()
    try:
        held = (looked.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
                if looked is not None else None)
    except sqlite3.OperationalError:
        held = None
    finally:
        if looked is not None:
            looked.close()
    if held is None:
        refuse("absent", "a bound participant of this store", options["participant"],
               "coordination.py participants lists them")
    if held["harness"] not in (None, member["harness"]):
        refuse("team", f"a {member['harness']} participant for {member['name']}",
               f"PT-{pid} on {held['harness']}", "join the member the participant's harness runs")
    conn = writer()
    with conn:
        run = run_row(conn, run_number(options["run"]))
        if run is None:
            refuse("absent", "a run of this store", options["run"], "sessions.py status lists the runs")
        if run["place"] != "open":
            refuse("place", "an open run", f"R-{run['id']} at {run['place']}", "start a new run")
        clash = launched_elsewhere(conn, member["name"])
        if clash is not None:
            refuse("place", f"{member['name']} not live in an open run",
                   f"SM-{clash['id']} in R-{clash['run']} at {clash['place']}",
                   f"sessions.py stop --run R-{clash['run']} first")
        previous = member_rows(conn, rid=run["id"], name=member["name"])
        generation = previous[-1]["generation"] + 1 if previous else 1
        subject = machine.create(conn, MEMBER, agent, note=f"R-{run['id']} {member['name']} joined")
        sid = conn.execute(
            "INSERT INTO session_member(subject, run, name, harness, roles, generation, native_id, "
            "participant, worktree, evidence, started_by, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (subject, run["id"], member["name"], member["harness"], ",".join(member["roles"]),
             generation, held["session_id"], pid, str(REPO),
             str(member_dir(run["id"], member["name"], generation)), agent, stamp()),
        ).lastrowid
    machine.move(conn, MEMBER, subject, "launch", agent,
                 evidence=f"joined PT-{pid}: not launched by the supervisor")
    machine.move(conn, MEMBER, subject, "bind", agent, evidence=f"native {held['session_id']} PT-{pid}")
    print(f"joined {member['name']} SM-{sid} as PT-{pid} in R-{run['id']} at bound gen {generation}")
    return EXIT_DONE


def pane_reading(run, row) -> str:
    if not row["pane"]:
        return "pane none"
    if shutil.which("tmux") is None:
        return "pane unread: tmux absent"
    shown = tmux(run["tmux_socket"], "display-message", "-p", "-t", row["pane"], "#{pane_dead}")
    if shown.returncode != 0:
        return f"pane {row['pane']} gone"
    return f"pane {row['pane']} " + ("dead" if shown.stdout.strip() == "1" else "live")


def do_recover(rest):
    """The observer restart: read every live member; a gone process is lost."""
    options = parse(rest, optional=("run",))
    agent = identity()
    conn = writer()
    if "run" in options:
        runs = [chosen_run(conn, options)]
    else:
        runs = [row for row in conn.execute(
            "SELECT r.*, s.place FROM session_run r JOIN subjects s ON s.id = r.subject ORDER BY r.id")
            if row["place"] in ("open", "stopping")]
    if not runs:
        print("no open runs")
        return EXIT_DONE
    every = True
    for run in runs:
        for row in member_rows(conn, rid=run["id"]):
            if row["place"] not in LIVE:
                continue
            head = f"R-{run['id']} {row['name']} SM-{row['id']}"
            route = route_line(conn, row["participant"])
            if joined(row):
                print(f"{head} unmanaged PT-{row['participant']}; {route}")
                continue
            reading = process_reading(row["pid"], row["start_ticks"])
            pane = pane_reading(run, row)
            if reading.startswith("ok"):
                print(f"{head} alive {reading.removeprefix('ok ')}; {pane}; {route}")
                continue
            every = False
            evidence = f"recover: process {reading}; {pane}; {route}"
            machine.move(conn, MEMBER, row["subject"], f"lose-{row['place']}", agent, evidence=evidence)
            with conn:
                conn.execute("UPDATE session_member SET outcome = ? WHERE id = ?",
                             (f"lost {reading}", row["id"]))
            print(f"{head} lost {reading}; {pane}; {route}")
    return EXIT_DONE if every else EXIT_FINDING


def do_input(rest):
    """Operator text to a protocol member's bridge: its pane's stdin."""
    tokens = list(rest)
    if not tokens or tokens[-1].startswith("--") or len(tokens) % 2 == 0:
        refuse("arguments", "input --run R-n --member NAME TEXT, the text last",
               " ".join(tokens) or "no arguments", usage())
    text = tokens.pop()
    options = parse(tokens, required=("run", "member"))
    identity()
    conn = reader()
    try:
        run = chosen_run(conn, options, places=("open",))
        member = newest_member(conn, run, options["member"])
    finally:
        if conn is not None:
            conn.close()
    if joined(member):
        refuse("unmanaged", f"a pane the supervisor owns for {member['name']}",
               f"SM-{member['id']} joined as PT-{member['participant']}", "type in that participant's own terminal")
    adapter = load_adapter(member["harness"])
    if adapter is None or adapter.transport() != "protocol":
        refuse("transport", "a protocol member, codex or grok, for input",
               f"{member['name']} on {member['harness']}",
               f"type in the pane: sessions.py attach --member {member['name']}")
    if not member["pane"]:
        refuse("absent", f"a pane for {member['name']} SM-{member['id']}", "none",
               "sessions.py start --member NAME")
    owner = ownership(run)
    if owner == "absent":
        refuse("absent", f"tmux session {run['tmux_session']}", "no such session",
               f"sessions.py status --run R-{run['id']}")
    if owner != "owned":
        refuse_unowned(run, owner)
    typed = tmux(run["tmux_socket"], "send-keys", "-t", member["pane"], "-l", text,
                 ";", "send-keys", "-t", member["pane"], "Enter")
    if typed.returncode != 0:
        refuse("tmux", f"tmux to type into pane {member['pane']}",
               typed.stderr.strip() or f"exit {typed.returncode}", "inspect the run's tmux server")
    print(f"input to {member['name']} SM-{member['id']} pane {member['pane']}: {len(text)} characters")
    return EXIT_DONE


# -- slice 4: the trial -------------------------------------------------------------

CLAUSE = re.compile(r"\S+:\d+")


def objective_path(rid: int) -> Path:
    return state_dir() / "runs" / f"R-{rid}" / "objective.json"


def gathered(rest, *, single=(), repeated=()):
    """--options where a repeated one gathers every value after it up to
    the next --option; anything else refuses `arguments`."""
    found: dict = {name: [] for name in repeated}
    current = None
    for token in rest:
        if token.startswith("--"):
            name = token[2:]
            if name not in single and name not in repeated:
                refuse("arguments", "a known option", token, usage())
            if name in single and name in found:
                refuse("arguments", f"--{name} once", f"--{name} twice", usage())
            current = name
            if name in single:
                found[name] = None
            continue
        if current is None:
            refuse("arguments", "an --option before each value", token, usage())
        if current in single:
            if found[current] is not None:
                refuse("arguments", f"one value after --{current}", token, usage())
            found[current] = token
        else:
            found[current].append(token)
    return found


def trial_objective(rest):
    """The objective record (slice 4, the shapes), written by the CO before
    any assignment; a later call rewrites it. Refused `objective` when a part
    is missing."""
    if not rest or rest[0].startswith("--"):
        refuse("arguments", "the run id first, trial objective R-n", rest[0] if rest else "none", usage())
    rid = run_number(rest[0])
    options = gathered(rest[1:], single=("deliverable", "candidate"), repeated=("clause", "case", "fake"))
    agent = identity()
    missing = [part for part, held in (("--deliverable", options.get("deliverable")),
                                        ("--clause", options["clause"]),
                                        ("--candidate", options.get("candidate"))) if not held]
    cases = {}
    for case in options["case"]:
        kind, _, value = case.partition(":")
        if kind not in ("omitted", "changed") or not value or kind in cases:
            refuse("objective", "each case once, omitted:<clause> and changed:<ref>", case,
                   "pass --case omitted:<file:line> --case changed:<ref>")
        cases[kind] = value
    missing += [f"--case {kind}:" for kind in ("omitted", "changed") if kind not in cases]
    if missing:
        refuse("objective", "the deliverable, a clause, the candidate and both controlled cases",
               "no " + ", no ".join(missing), "pass every part")
    malformed = [clause for clause in options["clause"] if not CLAUSE.fullmatch(clause)]
    if malformed:
        refuse("objective", "each clause as file:line", " ".join(malformed), "name each clause by its line")
    if cases["omitted"] not in options["clause"]:
        refuse("objective", "the omitted case naming one of the clauses", cases["omitted"],
               "pass --case omitted:<one of the --clause values>")
    conn = reader()
    try:
        run = chosen_run(conn, {"run": f"R-{rid}"}, places=("open", "stopping", "stopped"))
    finally:
        if conn is not None:
            conn.close()
    path = objective_path(run["id"])
    record = {
        "run": f"R-{run['id']}",
        "deliverable": options["deliverable"],
        "clauses": options["clause"],
        "candidate": options["candidate"],
        "cases": [f"{kind}:{cases[kind]}" for kind in ("omitted", "changed")],
        "fakes": options["fake"],
        "written_by": agent,
        "written_at": stamp(),
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(path, json.dumps(record, indent=1) + "\n")
    print(f"objective R-{run['id']} {options['deliverable']} {len(options['clause'])} clauses "
          f"{len(options['fake'])} fakes written {path}")
    return EXIT_DONE


TRIAL_IDS = tuple(f"I-{n:02d}" for n in range(1, 15))
TRIAL_ROW = re.compile(r"I-\d{2}")
NOT_MEASURED = re.compile(r"not measured\b\s*(.*)", re.IGNORECASE)
VERIFIED = re.compile(r"(?<![\w-])verified ([0-9a-f]{40})(?![0-9a-f])")
LIVE_CELL = re.compile(r"(?<![\w/.-])live(?![\w/.-])")
MOCK = re.compile(r"(?<![\w/.-])mock\s+\S+")


def checked_record(path_text: str) -> dict:
    """The trial record's fourteen rows by id, `id | required result |
    evidence | reading`, each read or declared `not measured <why>`; the
    first row that is not refuses `evidence`. Other tables (the AC
    crosswalk) are not read."""
    path = Path(path_text)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        refuse("evidence", "a readable trial record", f"{path}: {exc.__class__.__name__}",
               "pass the record's path, ops/conductor/handoff/<date>-slice4-trial.md")
    rows: dict = {}
    for number, line in enumerate(lines, 1):
        body = line.strip()
        if not body.startswith("|"):
            continue
        body = body[1:-1] if body.endswith("|") else body[1:]
        cells = [cell.strip() for cell in body.split("|")]
        if not TRIAL_ROW.fullmatch(cells[0]):
            continue
        key = cells[0]
        if key not in TRIAL_IDS:
            refuse("evidence", "rows I-01 to I-14", f"{key} at line {number}", "one row per acceptance id")
        if key in rows:
            refuse("evidence", f"one {key} row", f"{key} again at line {number}", "keep one row per id")
        if len(cells) != 4:
            refuse("evidence", f"{key} as id | required result | evidence | reading",
                   f"{len(cells)} cells at line {number}", "four cells per row")
        rows[key] = {"required": cells[1], "evidence": cells[2], "reading": cells[3], "line": number}
    for key in TRIAL_IDS:
        if key not in rows:
            refuse("evidence", "a row for each of I-01 to I-14", f"no {key} row in {path}",
                   f"add {key} with its reading or not measured <why>")
        reading = rows[key]["reading"]
        declared = NOT_MEASURED.match(reading)
        if not reading or (declared and not declared.group(1).strip()):
            refuse("evidence", f"{key} with a reading or not measured <why>",
                   f"{key} reading {reading!r} at line {rows[key]['line']}",
                   "write the measured line, or not measured and why")
    return rows


def trial_record(rest):
    """`trial record PATH`: a read; exit 0 on a complete trial record."""
    if len(rest) != 1:
        refuse("arguments", "trial record PATH", " ".join(rest) or "no path", usage())
    rows = checked_record(rest[0])
    unmeasured = sum(1 for row in rows.values() if NOT_MEASURED.match(row["reading"]))
    print(f"record {rest[0]} {len(rows)} rows read, {unmeasured} not measured")
    return EXIT_DONE


def cited(clause: str, rows: dict) -> bool:
    token = re.compile(r"(?<![\w/.-])" + re.escape(clause) + r"(?!\d)")
    return any(token.search(row["evidence"]) or token.search(row["reading"]) for row in rows.values())


def trial_accept(rest):
    """`trial accept R-n --candidate SHA --record PATH`: the acceptance
    claim, checked against the run's objective; a read. Refused
    `acceptance` on a candidate that is not I-14's verified sha, an
    objective clause cited by no row, or a mock written as live."""
    if not rest or rest[0].startswith("--"):
        refuse("arguments", "the run id first, trial accept R-n", rest[0] if rest else "none", usage())
    rid = run_number(rest[0])
    options = gathered(rest[1:], single=("candidate", "record"))
    missing = [f"--{name}" for name in ("candidate", "record") if not options.get(name)]
    if missing:
        refuse("arguments", "trial accept R-n --candidate SHA --record PATH", "no " + ", no ".join(missing),
               usage())
    conn = reader()
    try:
        run = chosen_run(conn, {"run": f"R-{rid}"}, places=("open", "stopping", "stopped"))
    finally:
        if conn is not None:
            conn.close()
    objective_file = objective_path(run["id"])
    try:
        objective = json.loads(objective_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        refuse("objective", f"R-{run['id']}'s objective record", f"{objective_file}: {exc.__class__.__name__}",
               f"sessions.py trial objective R-{run['id']} before any acceptance")
    rows = checked_record(options["record"])
    evidence = rows["I-14"]["evidence"]
    verified = VERIFIED.search(evidence)
    if verified is None:
        refuse("acceptance", "I-14's evidence cell as verified <40-hex sha>, the verifier's row",
               f"I-14 evidence {evidence!r}, no verified sha", "the verifier writes verified <sha> in I-14")
    candidate = options["candidate"]
    if candidate.lower() != verified.group(1):
        refuse("acceptance", f"the candidate to be the verified sha {verified.group(1)}",
               f"candidate {candidate}", "verify the candidate as it stands, or accept the verified sha")
    uncited = [clause for clause in objective.get("clauses", []) if not cited(clause, rows)]
    if uncited:
        refuse("acceptance", "every objective clause cited by its file:line in a row's evidence or reading",
               "no row cites " + ", ".join(uncited), "measure the clause and cite it, or the candidate omits it")
    fakes = objective.get("fakes", [])
    for key in TRIAL_IDS:
        row = rows[key]
        if not LIVE_CELL.search(row["evidence"]):
            continue
        if MOCK.search(row["evidence"]):
            refuse("acceptance", f"{key}'s evidence as mock <name> or live, one of them",
                   f"{key} evidence {row['evidence']!r}", "a mock's pass is written mock <name>, never live")
        planted = [fake for fake in fakes
                   if re.search(r"(?<![\w-])" + re.escape(fake) + r"(?![\w-])", row["reading"])]
        if planted:
            refuse("acceptance", f"{key}'s live evidence read from no planted program",
                   f"{key} reading names {', '.join(planted)}, declared planted in the objective's fakes",
                   f"write {key}'s evidence as mock {planted[0]}, or measure it live")
    print(f"accepted R-{run['id']} {verified.group(1)} record {options['record']} "
          f"{len(objective.get('clauses', []))} clauses cited")
    return EXIT_DONE


TRIAL = {"objective": trial_objective, "record": trial_record, "accept": trial_accept}


def do_trial(rest):
    verb = rest[0] if rest else ""
    if verb not in TRIAL:
        refuse("arguments", "trial " + " | ".join(TRIAL), verb or "no verb", usage())
    return TRIAL[verb](rest[1:])


VERBS = {
    "doctor": do_doctor, "start": do_start, "status": do_status,
    "attach": do_attach, "stop": do_stop, "export": do_export,
    "bridge": do_bridge, "join": do_join, "recover": do_recover, "input": do_input,
    "trial": do_trial,
}


def main(argv: list[str]) -> int:
    verb = argv[1] if len(argv) > 1 else ""
    try:
        if verb not in VERBS:
            refuse("arguments", "one of " + ", ".join(VERBS), verb or "no verb",
                   "name one verb; the module doc has the arguments")
        return VERBS[verb](coordination.profile_named(argv[2:])) or EXIT_DONE
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
