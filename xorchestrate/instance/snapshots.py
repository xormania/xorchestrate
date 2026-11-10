"""Telemetry snapshots at chosen moments (D17) and their archive into the companion.

The telemetry option ``snapshots`` selects the moments:

```json
"telemetry": {"options": {"snapshots": {"triggers": ["before_upgrade", "before_sync"], "archive": true}}}
```

| Trigger | When |
| --- | --- |
| ``before_upgrade`` (default) | after ``upgrade`` stops the server and before the release procedure starts |
| ``before_sync`` | before a render that changes the rendered configuration |
| ``before_approve`` | before a newly approved tracked configuration is first rendered |
| ``manual`` | always available: ``xorchestrate telemetry snapshot`` |

A snapshot is the running configuration's ``telemetry_snapshot`` bundle,
written under its ``export_root`` as ``<trigger>-<UTC time>``. With
``archive: true`` each triggered snapshot is also archived.

``archive`` copies a complete, verified bundle into the companion through the
``Companion`` port as records of kind ``telemetry`` keyed
``<instance>/<bundle_id>/<file>`` (the git-directory adapter keeps them at
``telemetry/<project>/<instance>/<bundle_id>/``). The archived manifest gains an
``archive`` object naming the configuration that produced it:
``companion_commit``, ``config_sha256`` (the approval hash of the tracked
configuration), ``overlay_sha256``, ``release`` and ``instance_id``. That object
is not part of the bundle's identity, so the archived copy still verifies and
restores. The manifest is written last, so a bundle without one is incomplete;
nothing is committed for the user.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.telemetry_snapshot import ARCHIVE_FIELD, SnapshotError, verify_bundle
from ..composition import build_telemetry
from ..model import OrchestrationError
from ..telemetry import MANUAL_SNAPSHOT, SNAPSHOT_TRIGGERS, snapshot_policy
from . import companion as records

TELEMETRY_KIND = "telemetry"
MANIFEST = "manifest.json"


def policy(config):
    """``(triggers, archive)`` of a rendered configuration; telemetry disabled selects no triggers."""
    section = config.get("telemetry") if isinstance(config, dict) else None
    if isinstance(section, dict) and section.get("enabled") is False:
        return (), False
    options = section.get("options") if isinstance(section, dict) else None
    return snapshot_policy(options.get("snapshots") if isinstance(options, dict) else None)


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _bundle_directory(telemetry, destination):
    return Path(telemetry.bundle_source(destination)["database_path"]).parent


def snapshot(config, context, triggers, *, destination=None, companion=None, instance=None, tracked=None,
             archive=None):
    """Snapshot the corpus of ``config`` for ``triggers`` (``manual`` or configured names).

    Returns a report; ``{"status": "skipped", "reason": ...}`` when the
    configuration collects no telemetry. ``archive`` overrides the
    configuration's ``archive`` setting.
    """
    triggers = list(triggers)
    if not triggers or any(item not in (*SNAPSHOT_TRIGGERS, MANUAL_SNAPSHOT) for item in triggers):
        raise OrchestrationError("Unknown snapshot trigger.", "invalid_arguments", {"triggers": triggers})
    configured_archive = policy(config)[1]
    name, bundle, telemetry = destination or f"{triggers[0]}-{_stamp()}", None, None
    try:
        telemetry = build_telemetry(config, project=context)
        if telemetry is None:
            return {"status": "skipped", "triggers": triggers, "reason": "telemetry_disabled"}
        bundle = _bundle_directory(telemetry, name)
        manifest = telemetry.snapshot(name)
    except OrchestrationError as error:
        raise OrchestrationError(
            f"The {', '.join(triggers)} telemetry snapshot failed ({error.code}: {error}). Fix the cause, or run the "
            f"command again with --skip-snapshots to proceed without it.", "snapshot_failed",
            {"triggers": triggers, "bundle": None if bundle is None else str(bundle),
             "cause": {"code": error.code, "message": str(error)}, "skip": "--skip-snapshots"}) from error
    finally:
        if telemetry is not None:
            telemetry.close()
    path = bundle
    report = {"status": "complete", "triggers": triggers, "bundle": str(path), "bundle_id": manifest["bundle_id"],
              "event_count": manifest["source"]["event_count"]}
    if (configured_archive if archive is None else archive):
        if companion is None or instance is None or tracked is None:
            raise OrchestrationError("Archiving a snapshot needs the project's companion.", "companion_required")
        report["archive"] = archive_bundle(path, companion=companion, instance=instance, tracked=tracked,
                                           config=config, context=context)
    return report


def _files(directory):
    """The bundle's regular files as ``{relative POSIX path: bytes}``; symbolic links are refused."""
    files = {}
    for current, directories, names in os.walk(directory, followlinks=False):
        for name in [*directories, *names]:
            if (Path(current) / name).is_symlink():
                raise OrchestrationError("Telemetry bundles may not contain symbolic links.", "telemetry_integrity",
                                         {"path": str(Path(current) / name)})
        for name in names:
            path = Path(current) / name
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def overlay_sha256(companion, project):
    overlay = companion.records(project).read(records.OVERLAY, records.OVERLAY_KEY)
    return None if overlay is None else hashlib.sha256(overlay).hexdigest()


def provenance(companion, instance, tracked):
    """What produced a bundle: the companion commit, configuration digests and the release."""
    if "overlay_sha256" in instance.record:
        # Recorded when the configuration was last rendered, beside approval_hash.
        overlay_digest = instance.record["overlay_sha256"]
    else:
        overlay_digest = overlay_sha256(companion, tracked.name)
    return {"companion_commit": companion.revision(),
            "config_sha256": instance.record.get("approval_hash") or tracked.config_hash,
            "overlay_sha256": overlay_digest,
            "release": instance.record.get("release", tracked.release),
            "project": tracked.name, "instance_id": instance.instance_id}


def _verify_archived(members, manifest):
    """Verify a bundle reassembled from archived records in a private temporary directory."""
    with tempfile.TemporaryDirectory(prefix="xorchestrate-archive-") as staging:
        root = Path(staging) / "bundle"
        for path, data in {**members, MANIFEST: manifest}.items():
            if data is None:
                raise OrchestrationError("An archived bundle member is missing.", "telemetry_integrity",
                                         {"member": path})
            target = root / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        try:
            verify_bundle(root)
        except (SnapshotError, OSError) as error:
            raise OrchestrationError("The archived bundle does not verify.", "telemetry_integrity") from error


def archive_bundle(bundle, *, companion, instance, tracked, config, context):
    """Copy a complete, verified bundle into the companion; return the archive report.

    ``bundle`` is a destination name or path inside the configuration's export
    root. Re-archiving a bundle already archived with the same identity only
    reports it.
    """
    telemetry = build_telemetry(config, project=context)
    if telemetry is None:
        raise OrchestrationError("This project collects no telemetry.", "telemetry_unavailable")
    try:
        # verify() refuses an incomplete or altered bundle before anything is copied.
        manifest = telemetry.verify(str(bundle))
        directory = _bundle_directory(telemetry, str(bundle))
    finally:
        telemetry.close()
    files = _files(directory)
    if json.loads(files.get(MANIFEST, b"null")) != manifest:
        raise OrchestrationError("The bundle changed while it was archived.", "telemetry_integrity")
    prefix = f"{instance.instance_id}/{manifest['bundle_id']}"
    store = companion.records(tracked.name)
    existing = store.read(TELEMETRY_KIND, f"{prefix}/{MANIFEST}")
    location = {"companion": companion.location, "kind": TELEMETRY_KIND, "project": tracked.name, "key": prefix}
    if existing is not None:
        return {"status": "already_archived", **location, "bundle_id": manifest["bundle_id"],
                ARCHIVE_FIELD: json.loads(existing).get(ARCHIVE_FIELD)}
    archived = {**manifest, ARCHIVE_FIELD: {**provenance(companion, instance, tracked),
                                            "archived_at": datetime.now(timezone.utc).isoformat()}}
    text = json.dumps(archived, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    members = {path: data for path, data in files.items() if path != MANIFEST}
    for path, data in sorted(members.items()):
        store.write(TELEMETRY_KIND, f"{prefix}/{path}", data)
    # Read every member back through the port and verify the reassembled bundle, with its
    # archived manifest, before the manifest makes the archived bundle complete.
    _verify_archived({path: store.read(TELEMETRY_KIND, f"{prefix}/{path}") for path in members}, text)
    store.write(TELEMETRY_KIND, f"{prefix}/{MANIFEST}", text)
    return {"status": "archived", **location, "bundle_id": manifest["bundle_id"], "files": len(files),
            ARCHIVE_FIELD: archived[ARCHIVE_FIELD],
            "next": "Review and commit the archived bundle in your companion; xorchestrate never commits for you."}
