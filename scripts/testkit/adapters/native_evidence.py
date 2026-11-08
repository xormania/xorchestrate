"""Private run-owned corpus collection using the product's preservation adapter.

The test-kit remains the owner of verdicts and lifecycle. This adapter verifies
retained byte closure; it neither authenticates a harness nor promotes a result.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from copy import deepcopy
from pathlib import Path

from .evidence import write_json


def _path(path):
    path = Path(os.path.abspath(path))
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Evidence paths cannot traverse symbolic links")
    return path


def _json(path):
    descriptor = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
            raise ValueError("Evidence metadata must be a bounded regular file")
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ValueError("Evidence metadata must be an object")
    return result


def _digest(path):
    result = hashlib.sha256()
    with _path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _member(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Evidence member escapes its collection")
    path = _path(root / relative)
    if not path.is_relative_to(root):
        raise ValueError("Evidence member escapes its collection")
    return path


def collect_native_evidence(directory, output):
    """Snapshot declared run-owned corpora after available runtime shutdown.

    Explicit external storage remains selected but is not scanned or copied.
    Each attempt owns new destinations; interruption cannot replace a prior
    collection or turn source coverage into a complete native qualification.
    """
    directory, output = _path(directory), _path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    native = _json(directory / "plan.json").get("mode") == "native"
    entries, gaps, seen = [], [], set()
    sources = sorted(directory.rglob("telemetry-runtime.json")) if native else []
    for source in sources:
        if source.is_relative_to(directory / "exports"):
            continue
        entry = {"source": str(source), "source_completion": "unknown", "status": "unavailable"}
        try:
            selected = _json(source)
            if selected.get("schema_version") != 1:
                raise ValueError("Unsupported runtime descriptor")
            if selected.get("status") == "disabled":
                entry.update(status="disabled", reason=selected.get("reason", "capture_explicitly_disabled"))
            elif selected.get("status") != "configured":
                entry["reason"] = selected.get("reason", "external_capture_policy_not_collected")
            else:
                root = _path(selected["runtime_root"])
                database, artifacts = _path(selected["database"]), _path(selected["artifacts"])
                if (not root.is_relative_to(directory) or not database.is_relative_to(root)
                        or not artifacts.is_relative_to(root)):
                    raise ValueError("Capture storage is not owned by this run")
                identity = str(database)
                if identity in seen:
                    continue
                seen.add(identity)
                from agent_telemetry.artifacts import FileArtifactStore

                from xorchestrate.adapters.telemetry_snapshot import create_snapshot, restore_bundle

                name = hashlib.sha256(str(source.relative_to(directory)).encode()).hexdigest()[:24]
                bundle = output / "bundles" / name
                restored = output / "restored" / name
                manifest = create_snapshot(database, FileArtifactStore(artifacts), bundle)
                for key in ("dataset_id", "collector_id"):
                    if manifest["source"][key] != selected.get(key):
                        raise ValueError("Capture lineage differs from its runtime declaration")
                projects = manifest["source"]["projects"]
                if projects and projects != [selected.get("project_id")]:
                    raise ValueError("Capture contains a different project")
                restored_result = restore_bundle(bundle, restored)
                if restored_result["manifest"] != manifest:
                    raise ValueError("Restored evidence identity differs")
                entry.update(status="verified", bundle=str(bundle), restored=str(restored),
                    bundle_relative=bundle.relative_to(output).as_posix(),
                    restored_relative=restored.relative_to(output).as_posix(),
                    bundle_id=manifest["bundle_id"], source_cutoff=manifest["source"]["cutoff"],
                    event_count=manifest["source"]["event_count"], completeness=manifest["completeness"],
                    dataset_id=manifest["source"]["dataset_id"], collector_id=manifest["source"]["collector_id"])
        except Exception as error:
            # Do not relay source payloads or arbitrary credential-bearing
            # exception text into the operator's public result.
            entry.update(status="unavailable", reason=type(error).__name__)
        entries.append(entry)
        if entry["status"] == "unavailable":
            gaps.append({"source": entry["source"], "reason": entry["reason"]})
    if native and not sources:
        gaps.append({"reason": "no_declared_capture_sources"})
    status = ("not_applicable" if not native else "partial" if gaps else
              "disabled" if entries and all(entry["status"] == "disabled" for entry in entries) else "verified")
    archive = output / "telemetry.zip"
    index = {"schema_version": 1, "status": status, "index": str(output / "telemetry-index.json"),
             "archive": str(archive) if any(entry["status"] == "verified" for entry in entries) else None,
             "entries": entries, "gaps": gaps, "native_acceptance": "not_established_by_telemetry",
             "verification_scope": "retained telemetry byte closure",
             "content_policy": "protected local corpus only; credential configurations and foreign histories excluded"}
    if index["archive"]:
        descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("index.json", json.dumps(index, sort_keys=True, indent=2) + "\n")
            for entry in entries:
                if entry["status"] != "verified":
                    continue
                root = Path(entry["bundle"])
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        bundle.write(_path(path), (Path(entry["bundle_relative"]) / path.relative_to(root)).as_posix())
        index["archive_sha256"] = _digest(archive)
    write_json(output / "telemetry-index.json", index)
    return index


def verify_native_evidence(index_path):
    """Read the saved attempt, verify exact bundles, and keep verdicts separate."""
    index_path = _path(index_path)
    index = _json(index_path)
    if index.get("schema_version") != 1 or index.get("native_acceptance") != "not_established_by_telemetry":
        raise ValueError("Unsupported native evidence index")
    result = deepcopy(index)
    root = index_path.parent
    errors = []
    if index.get("status") == "verified" and not any(
            entry.get("status") == "verified" for entry in index.get("entries", [])):
        errors.append({"reason": "verified_index_has_no_verified_sources"})
    if index.get("archive"):
        try:
            archive = _member(root, "telemetry.zip")
            if _digest(archive) != index.get("archive_sha256"):
                raise ValueError("Archive identity differs")
            with zipfile.ZipFile(archive) as bundle:
                if bundle.namelist().count("index.json") != 1 or bundle.getinfo("index.json").file_size > 8 * 1024 * 1024:
                    raise ValueError("Archived index must be one bounded member")
                archived = json.loads(bundle.read("index.json"))
            if archived != {key: value for key, value in index.items() if key != "archive_sha256"}:
                raise ValueError("Archived source population differs")
        except Exception as error:
            errors.append({"reason": "archive_index_unavailable", "error": type(error).__name__})
    elif index.get("status") == "verified":
        errors.append({"reason": "verified_index_has_no_archive"})
    for entry in result.get("entries", []):
        if entry.get("status") != "verified":
            continue
        try:
            from xorchestrate.adapters.telemetry_snapshot import verify_bundle

            manifest = verify_bundle(_member(root, entry["bundle_relative"]))
            restored = verify_bundle(_member(root, entry["restored_relative"]))
            if manifest != restored or manifest["bundle_id"] != entry["bundle_id"]:
                raise ValueError("Saved evidence identity differs")
            if any(manifest["source"][key] != entry[key] for key in ("dataset_id", "collector_id", "event_count")):
                raise ValueError("Saved evidence source differs")
            if manifest["source"]["cutoff"] != entry["source_cutoff"]:
                raise ValueError("Saved evidence cutoff differs")
        except Exception as error:
            entry.update(status="unavailable", reason=type(error).__name__)
            errors.append({"source": entry.get("source"), "reason": entry["reason"]})
    if errors:
        result.update(status="partial", gaps=[*result.get("gaps", []), *errors])
    return result


def verify_run(directory):
    directory = _path(directory)
    indexes = list((directory / "exports").glob("*/telemetry-index.json"))
    if not indexes:
        raise ValueError("No completed telemetry collection; run proof.sh collect first")
    result = verify_native_evidence(max(indexes, key=lambda path: path.stat().st_mtime_ns))
    state = _json(directory / "state.json")
    return {**result, "proof": state.get("proof", "unknown"), "managed_outcome": state.get("outcome", "pending")}


def collection_supplement(directory, output):
    """Product-specific binding passed to the generic managed archive store."""
    index = collect_native_evidence(directory, output)
    return {"status": "partial" if index["status"] == "partial" else "completed", "metadata": {"telemetry": index}}
