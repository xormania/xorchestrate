"""Typed helpers for the companion record kinds the operator layer owns, over the ``Companion`` port.

The port stores opaque records by project, kind and key; each capability here
defines its kind's scope and format:

| Kind | Scope | Key | Format |
| --- | --- | --- | --- |
| ``overlay`` | project | ``overlay.json`` | a configuration merge patch (the configuration format's own ``version``) |
| ``approvals`` | project | ``approvals.json`` | ``{"schema": 1, "project", "approved": [{"hash", "files", "branch", "approved_at"}]}`` |
| ``policies`` | none | ``<name>.json`` | a configuration merge patch |
| ``launchers`` | none | file name | executable bytes |

Approvals live in the companion (D13) because only the tracked configuration
can be changed by agents or other people's commits; the companion is the
user's. These are thin functions rather than port methods so that the port
stays a storage contract: a new capability (snapshots, telemetry archives)
adds a kind and its helpers here, never methods every adapter must implement.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..composition import read_json
from ..model import OrchestrationError

OVERLAY, APPROVALS, POLICIES, LAUNCHERS = "overlay", "approvals", "policies", "launchers"
OVERLAY_KEY, APPROVALS_KEY = "overlay.json", "approvals.json"
APPROVALS_SCHEMA = 1


def _document(data, kind, key):
    try:
        value = read_json(data.decode("utf-8"))
    except (OrchestrationError, UnicodeError) as exc:
        raise OrchestrationError("A companion record is not valid JSON.", "invalid_configuration",
                                 {"kind": kind, "key": key}) from exc
    if not isinstance(value, dict):
        raise OrchestrationError("A companion record must hold a JSON object.", "invalid_configuration",
                                 {"kind": kind, "key": key})
    return value


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def overlay(companion, project):
    data = companion.records(project).read(OVERLAY, OVERLAY_KEY)
    return {} if data is None else _document(data, OVERLAY, project)


def ensure_overlay(companion, project):
    """Create an empty overlay for ``project`` if absent; return whether it was created."""
    return companion.records(project).write(OVERLAY, OVERLAY_KEY, encode({}), create_only=True)


def write_overlay(companion, project, value):
    companion.records(project).write(OVERLAY, OVERLAY_KEY, encode(value))


def policies(companion):
    records = companion.records(None)
    return [(key, _document(records.read(POLICIES, key), POLICIES, key))
            for key in records.keys(POLICIES) if key.endswith(".json")]


def write_policy(companion, name, value):
    companion.records(None).write(POLICIES, name, encode(value))


def launchers(companion):
    records = companion.records(None)
    return {key: records.read(LAUNCHERS, key) for key in records.keys(LAUNCHERS)}


def write_launcher(companion, name, data):
    companion.records(None).write(LAUNCHERS, name, data)


def approvals(companion, project):
    data = companion.records(project).read(APPROVALS, APPROVALS_KEY)
    if data is None:
        return []
    document = _document(data, APPROVALS, project)
    if document.get("schema") != APPROVALS_SCHEMA or not isinstance(document.get("approved"), list):
        raise OrchestrationError("Unsupported approvals record.", "invalid_configuration", {"project": project})
    return [item for item in document["approved"] if isinstance(item, dict)]


def approved_hashes(companion, project):
    return [item.get("hash") for item in approvals(companion, project)]


def is_approved(companion, project, config_hash):
    return config_hash in approved_hashes(companion, project)


def approved_files(companion, project, config_hash):
    for item in approvals(companion, project):
        if item.get("hash") == config_hash and isinstance(item.get("files"), dict):
            return item["files"]
    return None


def approve(companion, project, config_hash, digests, worktree):
    """Record ``config_hash`` (several hashes may be approved); return whether it was new."""
    approved = approvals(companion, project)
    if any(item.get("hash") == config_hash for item in approved):
        return False
    entry = {"hash": config_hash, "files": dict(digests), "branch": worktree.get("branch"),
             "approved_at": datetime.now(timezone.utc).isoformat()}
    document = {"schema": APPROVALS_SCHEMA, "project": project, "approved": [*approved, entry]}
    companion.records(project).write(APPROVALS, APPROVALS_KEY, encode(document))
    return True
