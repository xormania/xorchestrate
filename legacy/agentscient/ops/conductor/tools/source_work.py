"""Working-source receipts shared by code providers and named project checks.

Coordination owns assignments. This module only records which working bytes a
call/check observed, in the lane's existing SQLite store. Historical CI results
are not rewritten or promoted to evidence about an uncommitted working tree.
"""

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

import coordination_mcp as coordination
import instrument
from instrument import refuse

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_workspaces(
    workspace TEXT PRIMARY KEY, generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_events(
    id INTEGER PRIMARY KEY, workspace TEXT NOT NULL, participant INTEGER NOT NULL,
    assignment TEXT, provider_epoch TEXT NOT NULL, before_fingerprint TEXT NOT NULL,
    after_fingerprint TEXT, outcome TEXT NOT NULL, changes TEXT,
    started_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS source_checks(
    workspace TEXT NOT NULL, name TEXT NOT NULL, fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL, passed INTEGER NOT NULL, evidence TEXT NOT NULL,
    PRIMARY KEY(workspace, name));
"""


def bind(config, name, workspace=None, owner=None):
    """One participant/worktree binding for editors and project operations."""
    tool = coordination.Tool(name, "", False, {}, (), "")
    coordination.check_capability(config, tool)
    conn, participant = coordination.resolve_binding(config, tool)
    try:
        profile = instrument.hook_profile()
        root = Path(profile["cwd"]) if profile else Path(workspace or Path.cwd())
        root = root.resolve(strict=True)
        if workspace and Path(workspace).resolve() != root:
            refuse("workspace", "the profile's assigned worktree", str(workspace),
                   "use the launcher's worktree binding")
        if config and config.get("worktree") and Path(config["worktree"]).resolve() != root:
            refuse("workspace", "the coordination binding's worktree", str(root), "repair the launch binding")
        assignment = config.get("assignment") if config else None
        selected = (str(root), participant["id"], participant["session_id"], assignment)
        if owner is not None and owner != selected:
            refuse("binding", "the original participant and worktree", "binding changed",
                   "start a separate MCP session for the new context")
        return selected
    finally:
        if conn is not None:
            conn.close()


def read_check(root, name):
    """Read an existing backend receipt without creating a store."""
    conn = connect()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM source_checks WHERE workspace=? AND name=?", (str(root), name)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def git(root, *args):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    executable = shutil.which("git")
    if not executable:
        refuse("source", "Git for the dev-lane source reader", "unavailable", "install the backend Git dependency")
    result = subprocess.run(  # noqa: S603 - fixed read operations, never model argv
        [executable, "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        env=env, capture_output=True, timeout=30, check=False)
    if result.returncode:
        refuse("source", "a readable assigned Git worktree", "Git source inspection failed",
               "inspect the worktree with the dev-lane source owner")
    return result.stdout


def snapshot(root):
    """Include working bytes, deletions, untracked files, modes and symlink targets."""
    root = Path(root).resolve(strict=True)
    top = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if top != root:
        refuse("workspace", "the assigned worktree root", str(root), "bind its Git root")
    files = {}
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for raw in sorted(set(names.split(b"\0")) - {b""}):
        name = os.fsdecode(raw)
        path = root / name
        if not path.parent.resolve().is_relative_to(root):
            refuse("source", "source paths inside the assigned worktree", name,
                   "repair the out-of-worktree parent link")
        try:
            info = path.lstat()
        except FileNotFoundError:
            files[name] = ["missing"]
            continue
        if stat.S_ISLNK(info.st_mode):
            value = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            value = digest.hexdigest()
        else:
            refuse("source", "regular source files or symlinks", name, "inspect this source entry")
        files[name] = [info.st_mode, value]
    head = git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    fingerprint = hashlib.sha256(json.dumps([head, files], sort_keys=True).encode()).hexdigest()
    return {"fingerprint": fingerprint, "revision": head, "files": files}


def connect(*, write=False):
    path = instrument.state_db()
    if not write and not path.exists():
        return None
    conn = instrument.connect_store(path, mode="rwc" if write else "ro")
    conn.row_factory = sqlite3.Row
    if write:
        conn.executescript(SCHEMA)
    elif not conn.execute("SELECT 1 FROM sqlite_schema WHERE name='source_workspaces'").fetchone():
        conn.close()
        return None
    return conn


@contextmanager
def workspace_lock(root):
    """Serialize participating source operations without holding the lane DB lock."""
    key = hashlib.sha256(str(root).encode()).hexdigest()
    directory = instrument.state_dir() / "source-locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / (key + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            refuse("busy", "one source operation per worktree", "another operation is active",
                   "retry after the active operation finishes")
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def status(root, current=None):
    current = current or snapshot(root)
    result = {"source_fingerprint": current["fingerprint"], "revision": current["revision"],
              "generation": 0, "verified_at_current_source": [], "last_event": None}
    conn = connect()
    if conn is None:
        return result
    try:
        row = conn.execute("SELECT generation FROM source_workspaces WHERE workspace=?", (str(root),)).fetchone()
        if row:
            result["generation"] = row["generation"]
        result["verified_at_current_source"] = [row["name"] for row in conn.execute(
            "SELECT name FROM source_checks WHERE workspace=? AND fingerprint=? AND generation=? AND passed=1 ORDER BY name",
            (str(root), current["fingerprint"], result["generation"]))]
        row = conn.execute("SELECT id,outcome,provider_epoch FROM source_events WHERE workspace=? ORDER BY id DESC LIMIT 1",
                           (str(root),)).fetchone()
        if row:
            result["last_event"] = dict(row)
        return result
    finally:
        conn.close()


def begin(root, participant, assignment, epoch, before):
    """Invalidate prior checks durably BEFORE a potentially mutating cell."""
    conn = connect(write=True)
    try:
        with instrument.transaction(conn):
            conn.execute("INSERT INTO source_workspaces VALUES (?,1) ON CONFLICT(workspace) DO UPDATE SET generation=generation+1",
                         (str(root),))
            event = conn.execute(
                "INSERT INTO source_events(workspace,participant,assignment,provider_epoch,before_fingerprint,outcome,started_at) "
                "VALUES (?,?,?,?,?,'running',?)",
                (str(root), participant, assignment, epoch, before["fingerprint"], instrument.stamp())).lastrowid
        return event
    finally:
        conn.close()


def finish(event, before, after, outcome):
    changes = (sorted(name for name in set(before["files"]) | set(after["files"])
                      if before["files"].get(name) != after["files"].get(name)) if after else None)
    conn = connect(write=True)
    try:
        with instrument.transaction(conn):
            conn.execute("UPDATE source_events SET after_fingerprint=?,outcome=?,changes=?,finished_at=? WHERE id=?",
                         (after["fingerprint"] if after else None, outcome, json.dumps(changes), instrument.stamp(), event))
    finally:
        conn.close()
    return changes


def record_check(root, name, *, fingerprint, generation, passed, evidence):
    """Internal receipt API for dev-lane checks; never exposed as a model claim."""
    current = status(root)
    if (current["source_fingerprint"], current["generation"]) != (fingerprint, generation):
        refuse("stale", "a check of the current working source", "source or generation changed",
               "rerun the named check against current source")
    conn = connect(write=True)
    try:
        with instrument.transaction(conn):
            row = conn.execute("SELECT generation FROM source_workspaces WHERE workspace=?", (str(root),)).fetchone()
            if (row["generation"] if row else 0) != generation:
                refuse("stale", "the checked source generation", "a code operation started", "rerun the named check")
            conn.execute("INSERT OR REPLACE INTO source_checks VALUES (?,?,?,?,?,?)",
                         (str(root), name, fingerprint, generation, int(passed), evidence))
    finally:
        conn.close()
