"""Exact input snapshots and command records, including dirty/untracked inputs."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..model import Result, Timeout
from .command import SubprocessRunner


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def allocate_run(output):
    """Allocate an exclusive private run beneath a reusable output root."""
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12])
    directory.mkdir(mode=0o700)
    return directory


class FileInputs:
    def exists(self, path, *, cwd):
        return (Path(cwd) / path).exists()


class RecordingRunner:
    """The command port with private raw evidence for every executed check."""

    def __init__(self, directory, *, terminal="capture"):
        self.directory = Path(directory)
        self.terminal = terminal
        self.records = []

    def run(self, argv, *, cwd, env, timeout):
        directory = self.directory / f"{len(self.records) + 1:02d}"
        started = time.monotonic()
        # Record only explicit overrides, never the full inherited credential env.
        record = run_command(argv, cwd, directory, environment=env, timeout=timeout, terminal=self.terminal)
        self.records.append({**record, "directory": str(directory)})
        stdout, stderr = (directory / "stdout.log").read_bytes(), (directory / "stderr.log").read_bytes()
        return Result(tuple(argv), record["exit_status"] if record["exit_status"] is not None else (
                          None if record["status"] == "setup_error" else 3),
                      stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"),
                      time.monotonic() - started, str(cwd), raw_stdout=stdout, raw_stderr=stderr,
                      launched=record["status"] != "setup_error", status=record["status"])


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def file_identity(path):
    path = Path(path)
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path),
                "sha256": digest(os.readlink(path).encode())}
    if not path.is_file():
        raise ValueError("Input is not a regular file or symlink: " + str(path))
    return {"kind": "file", "sha256": digest(path.read_bytes()),
            "executable": bool(path.stat().st_mode & 0o111), "bytes": path.stat().st_size}


def candidate_inputs(root):
    root = Path(root).resolve()
    # Tracked ignored files are still inputs. Untracked ignored run/cache files
    # are excluded according to the repository's explicit ignore convention.
    raw = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = sorted(set(os.fsdecode(item) for item in raw.split(b"\0") if item))
    files = {}
    deleted = []
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe repository input path")
        path = root / relative
        if not path.exists() and not path.is_symlink():
            deleted.append(name)
        else:
            files[name] = file_identity(path)
    return {"files": files, "deleted": deleted}


def candidate_identity(root):
    root = Path(root).resolve()
    inputs = candidate_inputs(root)
    return {"root": str(root), "commit": git(root, "rev-parse", "HEAD").decode().strip(),
            "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            "branch": git(root, "branch", "--show-current").decode().strip(),
            "status": git(root, "status", "--porcelain=v1", "--untracked-files=all").decode(),
            "content_sha256": digest(canonical(inputs)), **inputs}


def capture_candidate(root, directory):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    if directory == root or root in directory.parents:
        raise ValueError("Snapshot output must be outside the measured candidate")
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    before = candidate_identity(root)
    contents = directory / "content"
    contents.mkdir(mode=0o700)
    for name, identity in before["files"].items():
        target = contents / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if identity["kind"] == "symlink":
            target.symlink_to(identity["target"])
        else:
            shutil.copy2(root / name, target)
    after = candidate_identity(root)
    if before["content_sha256"] != after["content_sha256"]:
        raise ValueError("Candidate changed during capture; snapshot is not a measurement basis")
    (directory / "working.patch").write_bytes(git(root, "diff", "--binary", "HEAD"))
    before["captured_at"] = now()
    write_json(directory / "candidate.json", before)
    return before


def verify_candidate(root, expected):
    actual = candidate_identity(root)
    return {"passed": actual["content_sha256"] == expected["content_sha256"],
            "expected": expected["content_sha256"], "actual": actual["content_sha256"],
            "changed_paths": sorted(name for name in set(actual["files"]) | set(expected["files"])
                                    if actual["files"].get(name) != expected["files"].get(name)),
            "deleted": actual["deleted"]}


def bind_files(paths):
    return {name: {"path": str(Path(path).resolve()), **file_identity(path)}
            for name, path in paths.items()}


def run_command(command, cwd, directory, *, environment=None, timeout=300, terminal="capture"):
    """Retain a private raw record. Only explicit material environment is recorded.

    Raw output may be sensitive. Publication/redaction is a separate operation.
    This never interprets nonzero status as intended fault detection.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    environment = environment or {}
    record = {"command": list(command), "cwd": str(Path(cwd).resolve()),
              "environment": environment, "started_at": now(), "timeout_seconds": timeout,
              "python": sys.version, "platform": platform.platform(), "terminal": terminal, "status": "running"}
    write_json(directory / "command.json", record)
    try:
        completed = SubprocessRunner(terminal=terminal).run(command, cwd=cwd, env={**os.environ, **environment}, timeout=timeout)
        stdout = completed.raw_stdout if completed.raw_stdout is not None else completed.stdout.encode()
        stderr = completed.raw_stderr if completed.raw_stderr is not None else completed.stderr.encode()
        record.update(exit_status=completed.returncode,
                      status="passed" if completed.returncode == 0 else "failed")
    except Timeout as exc:
        stdout = exc.raw_stdout if exc.raw_stdout is not None else (exc.stdout or "").encode()
        stderr = exc.raw_stderr if exc.raw_stderr is not None else (exc.stderr or "").encode()
        record.update(exit_status=None, status="timeout")
    except OSError as exc:
        stdout, stderr = b"", str(exc).encode()
        record.update(exit_status=None, status="setup_error", error=type(exc).__name__)
    for name, content in (("stdout.log", stdout), ("stderr.log", stderr)):
        output = directory / name
        output.write_bytes(content)
        output.chmod(0o600)
    record.update(finished_at=now(), stdout_sha256=digest(stdout), stderr_sha256=digest(stderr))
    write_json(directory / "command.json", record)
    return record
