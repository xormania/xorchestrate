"""Private managed-run storage, fenced ownership and generation-bound exports.

The supervisor owns operator state; the executor retains its existing evidence
namespace. Controls write identified requests, never replacement state snapshots.
Archives contain only explicitly committed artifacts and bounded redacted views.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .evidence import allocate_run, digest, now, write_json
from .tmux import Identity, alive, processes

TERMINAL_PHASES = {"finished", "incomplete"}
MAX_JSON_BYTES = 8 * 1024 * 1024
SENSITIVE = re.compile(r"password|credential|secret|token|api[_-]?key|authorization|environment", re.I)
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
INPUT_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\Z")


def process_identity(pid=None):
    """Reuse the process adapter's start-time identity, fenced across reboots."""
    pid = os.getpid() if pid is None else pid
    current = processes().get(pid)
    if current is None:
        raise ProcessLookupError(pid)
    return {"pid": pid, "started": current[0].started,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def owner_alive(owner):
    if not isinstance(owner, dict) or owner.get("released_at"):
        return False
    try:
        return (owner.get("boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                and alive(Identity(owner["pid"], owner["started"])))
    except (KeyError, TypeError, OSError):
        return False


def _json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Managed record exceeds its size bound")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Managed record must be an object")
    return result


def _redact(value):
    """A bounded export filter; original private records are never rewritten."""
    if isinstance(value, dict):
        return {key: "[redacted]" if SENSITIVE.search(key) else _redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        result, hide = [], False
        for item in value:
            if hide:
                result.append("[redacted]")
                hide = False
            elif isinstance(item, str) and item.startswith("--") and SENSITIVE.search(item):
                result.append(item.split("=", 1)[0] + "=[redacted]" if "=" in item else item)
                hide = "=" not in item
            else:
                result.append(_redact(item))
        return result
    if isinstance(value, str):
        return re.sub(r"(?i)((?:api[_-]?key|password|secret|token|authorization)\s*[:=]\s*)[^\s,;]+",
                      r"\1[redacted]", value)
    return value


class ManagedStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self._owner_file = None
        self._owner = None

    @staticmethod
    def allocate(root):
        return allocate_run(root)

    @contextmanager
    def _lock(self, name=".publication.lock", *, nonblocking=False):
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(self.directory / name, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
            yield descriptor
        finally:
            os.close(descriptor)

    def initialize(self, plan, inputs=None):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock():
            if (self.directory / "state.json").exists():
                raise FileExistsError("Run is already initialized")
            for name in ("requests", "responses", "generations", "exports", "inputs"):
                (self.directory / name).mkdir(mode=0o700)
            write_json(self.directory / "plan.json", plan)
            state = {"schema_version": 1, "run_id": self.directory.name, "revision": 0,
                     "created_at": now(), "phase": "allocating", "owner": None,
                     "proof": "pending", "cleanup": "pending", "collection": "pending",
                     "artifacts": []}
            self._write_state(state)
        if inputs:
            self.snapshot_inputs(inputs)
        return state

    def read_state(self):
        state = _json(self.directory / "state.json")
        if state.get("schema_version") != 1 or state.get("run_id") != self.directory.name:
            raise ValueError("Not a supported managed run directory")
        return state

    def _write_state(self, state):
        generation = self.directory / "generations" / f"{state['revision']:08d}.json"
        if generation.exists():
            current_path = self.directory / "state.json"
            current = _json(current_path) if current_path.exists() else None
            if current is not None and current["revision"] >= state["revision"]:
                raise FileExistsError("Committed generation is immutable")
            # A crash may leave a staged generation without replacing state.
            # It was never committed or observable through the state pointer.
            generation.unlink()
        write_json(generation, state)
        write_json(self.directory / "state.json", state)

    def claim_owner(self):
        """Acquire lifetime ownership once; crashed runs are inspectable, not replayed."""
        if self._owner_file is not None:
            return dict(self._owner)
        descriptor = os.open(self.directory / ".owner.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self._lock():
                state = self.read_state()
                if state.get("owner") is not None:
                    raise RuntimeError("Existing owner record cannot be replaced; start a fresh run")
                owner = {**process_identity(), "token": uuid4().hex, "claimed_at": now()}
                state.update(owner=owner, revision=state["revision"] + 1, updated_at=now())
                self._write_state(state)
            self._owner_file, self._owner = descriptor, owner
            return dict(owner)
        except BaseException:
            os.close(descriptor)
            raise

    def _require_owner(self, state, token=None):
        expected = state.get("owner")
        token = token or (self._owner or {}).get("token")
        if (not owner_alive(expected) or expected.get("token") != token
                or expected.get("pid") != os.getpid()
                or expected.get("started") != process_identity()["started"]):
            raise PermissionError("Only the fenced live supervisor may publish operator state")

    def publish(self, changes, *, expected_revision=None, owner_token=None):
        with self._lock():
            state = self.read_state()
            self._require_owner(state, owner_token)
            if expected_revision is not None and state["revision"] != expected_revision:
                raise ValueError("Stale managed state revision")
            protected = {"schema_version", "run_id", "revision", "created_at", "owner"}
            if protected.intersection(changes):
                raise ValueError("Identity and revision fields are managed by storage")
            if state["phase"] in TERMINAL_PHASES and changes.get("phase", state["phase"]) != state["phase"]:
                raise ValueError("A terminal lifecycle cannot regress")
            if state["phase"] in TERMINAL_PHASES:
                frozen = {"proof", "cleanup", "outcome", "checks", "report", "failure", "reason"}
                if any(key in changes and changes[key] != state.get(key) for key in frozen):
                    raise ValueError("A terminal proof outcome cannot be overwritten")
            state.update(deepcopy(changes))
            state.update(revision=state["revision"] + 1, updated_at=now())
            self._write_state(state)
            return state

    def release_owner(self):
        if self._owner_file is None:
            return
        try:
            with self._lock():
                state = self.read_state()
                self._require_owner(state)
                state["owner"] = {**state["owner"], "released_at": now()}
                state.update(revision=state["revision"] + 1, updated_at=now())
                self._write_state(state)
        finally:
            os.close(self._owner_file)
            self._owner_file, self._owner = None, None

    def submit(self, action, payload=None, request_id=None):
        if action not in {"stop", "collect", "close"}:
            raise ValueError("Unknown managed control action")
        request_id = request_id or uuid4().hex
        if not REQUEST_ID.fullmatch(request_id):
            raise ValueError("Invalid request identity")
        request = {"id": request_id, "action": action, "payload": payload or {}, "created_at": now()}
        with self._lock():
            self.read_state()
            path = self.directory / "requests" / (request_id + ".json")
            if path.exists():
                existing = _json(path)
                if existing["action"] != action or existing["payload"] != request["payload"]:
                    raise ValueError("Request identity conflicts with an existing request")
                return existing
            write_json(path, request)
        return request

    def requests(self):
        return sorted((_json(path) for path in (self.directory / "requests").glob("*.json")
                       if not (self.directory / "responses" / path.name).exists()),
                      key=lambda row: (row["created_at"], row["id"]))

    def complete_request(self, request_id, result):
        if not REQUEST_ID.fullmatch(request_id):
            raise ValueError("Invalid request identity")
        with self._lock():
            self._require_owner(self.read_state())
            if not (self.directory / "requests" / (request_id + ".json")).is_file():
                raise FileNotFoundError(request_id)
            path = self.directory / "responses" / (request_id + ".json")
            if path.exists():
                return _json(path)
            response = {"id": request_id, "completed_at": now(), "result": result}
            write_json(path, response)
            return response

    def request_result(self, request_id):
        if not REQUEST_ID.fullmatch(request_id):
            raise ValueError("Invalid request identity")
        path = self.directory / "responses" / (request_id + ".json")
        return _json(path) if path.exists() else None

    def _artifact(self, name):
        relative = Path(name)
        if relative.is_absolute():
            relative = relative.relative_to(self.directory)
        if not relative.parts or ".." in relative.parts:
            raise ValueError("Artifact is outside this run")
        path = self.directory
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("Symlink artifacts cannot be exported")
        if not path.is_file() or path.stat().st_uid != os.getuid():
            raise ValueError("Artifact must be an owned regular file")
        return path, relative.as_posix()

    def bind_artifacts(self, paths):
        bindings = []
        for name in paths:
            path, relative = self._artifact(name)
            hasher, size = hashlib.sha256(), 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    hasher.update(chunk)
                    size += len(chunk)
            bindings.append({"path": relative, "sha256": hasher.hexdigest(), "bytes": size})
        return bindings

    def snapshot_inputs(self, inputs):
        """Private exact bytes for fresh repeats; source base semantics stay explicit."""
        with self._lock():
            manifest_path = self.directory / "inputs" / "manifest.json"
            manifest = _json(manifest_path) if manifest_path.exists() else {}
            for name, source in inputs.items():
                if not INPUT_NAME.fullmatch(name) or name == "manifest.json":
                    raise ValueError("Invalid input name")
                source = Path(source).expanduser().resolve()
                raw = source.read_bytes()
                target = self.directory / "inputs" / name
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                manifest[name] = {"path": str(target), "source": str(source),
                                  "source_base": str(source.parent), "sha256": digest(raw), "bytes": len(raw)}
            write_json(manifest_path, manifest)
        return manifest

    def collect(self, generation=None):
        """Export one committed generation without holding a lock during ZIP work."""
        with self._lock():
            current = self.read_state()
            owner = current.get("owner")
            if owner_alive(owner):
                self._require_owner(current)
            generation = current["revision"] if generation is None else generation
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise ValueError("Invalid committed generation")
            state = _json(self.directory / "generations" / f"{generation:08d}.json")
            if generation > current["revision"] or state.get("revision") != generation:
                raise ValueError("Uncommitted evidence generation")
            plan = _json(self.directory / "plan.json")
        identifier = uuid4().hex
        output = self.directory / "exports" / identifier
        output.mkdir(mode=0o700)
        archive = output / "diagnostics.zip"
        inventory, payloads = [], {}
        for item in state.get("artifacts", []):
            relative = item.get("path", "")
            try:
                path, relative = self._artifact(relative)
                if (relative.startswith(("inputs/", "exports/")) or path.suffix != ".json"
                        or path.name == "terminal.json"):
                    raise ValueError("Private input/raw artifact excluded by export policy")
                with path.open("rb") as stream:
                    raw = stream.read(MAX_JSON_BYTES + 1)
                if len(raw) > MAX_JSON_BYTES or digest(raw) != item["sha256"]:
                    raise ValueError("Artifact changed after generation commitment")
                value = json.loads(raw)
                payloads["artifacts/" + relative] = json.dumps(_redact(value), indent=2, sort_keys=True).encode() + b"\n"
                inventory.append({**item, "status": "included", "export_sha256": digest(payloads["artifacts/" + relative])})
            except (OSError, ValueError, KeyError, TypeError) as error:
                inventory.append({"path": relative, "status": "unavailable", "reason": str(error)})
        receipt = {"schema_version": 1, "id": identifier, "run_id": state["run_id"],
                   "generation": generation, "created_at": now(), "archive": str(archive),
                   "status": "partial" if any(item["status"] != "included" for item in inventory) else "completed",
                   "inventory": inventory, "native_diagnostics": "not captured; mutable/raw artifacts remain private",
                   "redaction": "sensitive keys/known assignment syntax filtered; sensitive prose may remain"}
        safe_plan = {key: plan[key] for key in ("schema_version", "suite", "mode", "driver", "drivers", "families",
                                               "timeout", "check_timeout", "run_timeout") if key in plan}
        payloads["state.json"] = json.dumps(_redact(state), indent=2, sort_keys=True).encode() + b"\n"
        payloads["plan-summary.json"] = json.dumps(safe_plan, indent=2, sort_keys=True).encode() + b"\n"
        payloads["inventory.json"] = json.dumps(receipt, indent=2, sort_keys=True).encode() + b"\n"
        summary = (f"Run: {state['run_id']}\nGeneration: {generation}\nPhase: {state['phase']}\n"
                   f"Proof: {state.get('proof', 'unknown')}\nCollection: {receipt['status']}\n"
                   "Raw logs/configuration stay private in the run directory.\n")
        payloads["summary.txt"] = summary.encode()
        descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name, raw in payloads.items():
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    bundle.writestr(info, raw)
            receipt["archive_sha256"] = digest(archive.read_bytes())
            write_json(output / "receipt.json", receipt)
            (output / "summary.txt").write_text(summary)
            (output / "summary.txt").chmod(0o600)
        except BaseException:
            # Keep a failed attempt's private directory; a later export gets a new ID.
            raise
        return receipt
