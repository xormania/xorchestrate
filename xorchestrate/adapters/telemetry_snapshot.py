"""Portable preservation of the local telemetry corpus.

This is a SQLite/file adapter, not another collector. Live input is read with the
collector's SQLite corpus adapter and the SQLite backup API; only the completed
backup determines the cutoff and object closure. A manifest marked incomplete is
never restorable. Bundles written by the earlier standalone collector, whose
manifest also names that package's version, verify and restore unchanged.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from xorchestrate import __version__

from .telemetry_collector.artifacts import FileArtifactStore
from .telemetry_collector.domain import ArtifactRef, TelemetryError, canonical
from .telemetry_collector.sqlite import SQLiteRepository

FORMAT = "xorchestrate-telemetry-bundle-v1"
ARCHIVE_FIELD = "archive"
"""Manifest field holding archive provenance (companion commit, configuration digests, release)."""


class SnapshotError(ValueError):
    """The evidence bundle is incomplete, unsafe, or fails verification."""


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink():
            raise SnapshotError("symbolic links are not supported in bundles")
    return path


def _write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _manifest(path, value):
    temporary = path / ".manifest.pending"
    _write(temporary, _canonical(value))
    os.replace(temporary, path / "manifest.json")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def _finalize_authorization(authorize):
    finalize = getattr(authorize, "finalize", None)
    if callable(finalize):
        finalize()


def invalidate_bundle(path):
    """Remove evidence from a newly created destination after failed release."""
    path = _safe_path(path)
    for name in ("telemetry.sqlite3", "telemetry.sqlite3-wal", "telemetry.sqlite3-shm", "telemetry.sqlite3-journal"):
        (path / name).unlink(missing_ok=True)
    shutil.rmtree(path / "artifacts", ignore_errors=True)
    _manifest(path, {"format": FORMAT, "status": "incomplete"})


def bundle_receipt(path, manifest, *, authorize=None):
    """Return a bounded public receipt referring to the exact saved manifest."""
    path = _safe_path(path)
    data = _read(path / "manifest.json")
    if _canonical(json.loads(data)) != _canonical(manifest):
        raise SnapshotError("saved manifest changed before its receipt")
    result = {"status": manifest["status"], "format": manifest["format"],
              "bundle_id": manifest["bundle_id"], "completeness": manifest["completeness"],
              "source_cutoff": manifest["source"]["cutoff"], "event_count": manifest["source"]["event_count"],
              "artifact_count": len(manifest["artifacts"]),
              "unavailable_artifact_count": len(manifest["unavailable_artifacts"]),
              "unresolved_link_count": len(manifest["source"]["unresolved_links"]),
              "manifest_reference": {"path": str(path / "manifest.json"), "sha256": hashlib.sha256(data).hexdigest(),
                                     "size_bytes": len(data), "media_type": "application/json"}}
    if len(_canonical(result)) > 65536:
        raise SnapshotError("bundle receipt exceeds the public response budget")
    _finalize_authorization(authorize)
    return result


def _read(path):
    fd = os.open(_safe_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SnapshotError("bundle member must be a regular file")
        return stream.read()


def _closed_database(path):
    # Finished bundles never depend on a journal or on the source's WAL files.
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise SnapshotError("bundle database has a journal sidecar")


def _inspect(path, authorize=None):
    """Derive closure exclusively from an already completed database snapshot."""
    repository = SQLiteRepository.inspect(path)
    try:
        db = repository.db
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SnapshotError("snapshot database integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SnapshotError("snapshot has broken reference rows")
        status = repository.status()
        refs = {}
        unavailable = []
        projects = set()
        observed = []
        captured = []
        event_ids = set()
        unresolved = []
        producers = set()
        for row in db.execute("SELECT * FROM events ORDER BY sequence"):
            observation = json.loads(row["observation"])
            if authorize is not None and not authorize(observation):
                raise PermissionError("snapshot closure is not authorized")
            # The original serialized envelope, not a normalized projection, is
            # the immutable digest input used by the collector.
            if hashlib.sha256(canonical(observation).encode()).hexdigest() != row["digest"]:
                raise SnapshotError("snapshot observation digest differs")
            if (observation["event_id"] != row["event_id"] or
                    observation["event_type"] != row["event_type"] or
                    observation["context"]["project_id"] != row["project_id"] or
                    observation["context"]["dataset_id"] != status["dataset_id"] or
                    observation["context"]["collector_id"] != status["collector_id"]):
                raise SnapshotError("snapshot observation identity differs")
            event_ids.add(row["event_id"])
            projects.add(row["project_id"])
            producers.add(observation["context"]["producer_id"])
            captured.append(row["captured_at"])
            if observation.get("observed_at") is not None:
                observed.append(observation["observed_at"])
            declared = observation.get("artifacts", [])
            indexed = [json.loads(item[0]) for item in db.execute(
                "SELECT metadata FROM artifacts WHERE event_id=? ORDER BY rowid", (row["event_id"],))]
            if declared != indexed:
                raise SnapshotError("snapshot artifact index differs from observations")
            indexed_links = [list(item) for item in db.execute(
                "SELECT relation,target_id FROM links WHERE event_id=? ORDER BY rowid", (row["event_id"],))]
            if observation.get("links", []) != indexed_links:
                raise SnapshotError("snapshot link index differs from observations")
            for value in declared:
                ref = ArtifactRef(**value)
                if ref.availability != "captured":
                    unavailable.append({"event_id": row["event_id"], **value})
                    continue
                previous = refs.get(ref.digest)
                if previous is not None and asdict(previous) != value:
                    # A digest may be used with different media types, but its
                    # byte identity cannot have conflicting lengths.
                    if previous.size_bytes != ref.size_bytes:
                        raise SnapshotError("conflicting artifact lengths")
                refs[ref.digest] = ref
        for event_id, relation, target_id in db.execute("SELECT event_id,relation,target_id FROM links"):
            if target_id not in event_ids:
                unresolved.append({"event_id": event_id, "relation": relation, "target_id": target_id})
        source = {"cutoff": status["max_sequence"] or 0,
                  "event_count": status["event_count"],
                  "dataset_id": status["dataset_id"], "collector_id": status["collector_id"],
                  "schema_version": status["schema_version"], "cursors": status["cursors"],
                  "coverage": status["coverage"], "projects": sorted(projects),
                  "coverage_boundary": "retained collector observations through cutoff",
                  "sources": [{"producer_id": producer, "presence": "observed", "health": "unknown"}
                              for producer in sorted(producers)],
                  "observed_range": [min(observed), max(observed)] if observed else [None, None],
                  "captured_range": [min(captured), max(captured)] if captured else [None, None],
                  "unresolved_links": unresolved}
        return source, refs, unavailable
    finally:
        repository.close()


def create_snapshot(database_path, artifact_store, destination, *, authorize=None, backup=None,
                    backup_timeout=5.0, clock=None):
    """Create a verified bundle, refusing any unauthorized closure member.

    ``artifact_store`` may be None when artifact storage is disabled; a corpus
    that refers to captured artifacts then cannot be snapshotted.
    ``backup`` is an optional replaceable SQLite backup operation receiving the
    source and destination connections. Raising leaves an incomplete manifest.
    Destinations are new directories; existing bundles are never overwritten.
    """
    if (isinstance(backup_timeout, bool) or not isinstance(backup_timeout, (int, float))
            or not 0 < backup_timeout <= 3600):
        raise ValueError("invalid snapshot backup deadline")
    clock = clock or time.monotonic
    deadline = clock() + backup_timeout

    def progress(status, remaining, total):
        if clock() >= deadline:
            raise SnapshotError("snapshot backup deadline exceeded")

    destination = _safe_path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    _manifest(destination, {"format": FORMAT, "status": "incomplete"})
    try:
        # Staging is private process-owned storage, outside the export path.
        # A denied closure must never leave its database in the destination.
        with tempfile.TemporaryDirectory(prefix="xorchestrate-snapshot-") as staging:
            staged = Path(staging)
            database = staged / "telemetry.sqlite3"
            source = SQLiteRepository.inspect(database_path)
            try:
                _write(database, b"")
                target = sqlite3.connect(database)
                try:
                    if backup is None:
                        source.db.backup(target, pages=128, progress=progress, sleep=0.01)
                    else:
                        backup(source.db, target)
                    progress(None, 0, 0)
                    target.commit()
                    target.execute("PRAGMA journal_mode=DELETE")
                finally:
                    target.close()
            finally:
                source.close()
            _closed_database(database)
            source_info, refs, unavailable = _inspect(database, authorize)
            staged_store = FileArtifactStore(staged / "artifacts")
            if refs and artifact_store is None:
                # Artifact storage is disabled but the corpus refers to captured
                # objects: the closure cannot be completed.
                raise SnapshotError("snapshot closure needs artifacts but no artifact storage is selected")
            for ref in refs.values():
                installed = staged_store.put(artifact_store.read(ref), ref.media_type)
                if installed.digest.removeprefix("sha256:") != ref.digest.removeprefix("sha256:"):
                    raise SnapshotError("artifact installation changed content identity")
            # Recheck current policy after collecting the complete closure.
            _inspect(database, authorize)
            _write(destination / "telemetry.sqlite3", _read(database))
            bundled_store = FileArtifactStore(destination / "artifacts")
            for ref in refs.values():
                bundled_store.put(staged_store.read(ref), ref.media_type)
            (destination / "artifacts").mkdir(mode=0o700, exist_ok=True)
            _inspect(database, authorize)
            manifest = {"format": FORMAT, "status": "complete",
                        "completeness": "partial" if unavailable else "complete",
                        "database": {"path": "telemetry.sqlite3", "sha256": hashlib.sha256(_read(database)).hexdigest()},
                        "source": source_info, "artifacts": [asdict(refs[key]) for key in sorted(refs)],
                        "unavailable_artifacts": unavailable,
                        "artifact_algorithm": "sha256",
                        "software": {"xorchestrate": __version__, "agent_telemetry_format": "agent-telemetry-v1",
                                     "sqlite": sqlite3.sqlite_version}}
            manifest["bundle_id"] = hashlib.sha256(_canonical(manifest)).hexdigest()
            _manifest(destination, manifest)
            return verify_bundle(destination, authorize=authorize)
    except BaseException as error:
        for name in ("telemetry.sqlite3", "telemetry.sqlite3-wal", "telemetry.sqlite3-shm", "telemetry.sqlite3-journal"):
            (destination / name).unlink(missing_ok=True)
        shutil.rmtree(destination / "artifacts", ignore_errors=True)
        _manifest(destination, {"format": FORMAT, "status": "incomplete"})
        if isinstance(error, TelemetryError):
            raise SnapshotError("snapshot source or artifact verification failed") from error
        raise


def verify_bundle(path, *, authorize=None):
    """Verify bytes and current authority for the complete actual closure.

    Authorization is repeated after object verification so a policy change
    during that work cannot expose a stale manifest's private metadata.
    """
    path = _safe_path(path)
    try:
        manifest = json.loads(_read(path / "manifest.json"))
        if manifest.get("format") != FORMAT or manifest.get("status") != "complete":
            raise SnapshotError("bundle is incomplete or unsupported")
        identity = dict(manifest)
        bundle_id = identity.pop("bundle_id", None)
        # Provenance added when a bundle is archived into the companion is not part of its identity.
        identity.pop(ARCHIVE_FIELD, None)
        if hashlib.sha256(_canonical(identity)).hexdigest() != bundle_id:
            raise SnapshotError("bundle manifest identity differs")
        if manifest["database"]["path"] != "telemetry.sqlite3":
            raise SnapshotError("invalid bundle database path")
        database = path / "telemetry.sqlite3"
        _closed_database(database)
        if hashlib.sha256(_read(database)).hexdigest() != manifest["database"]["sha256"]:
            raise SnapshotError("bundle database digest differs")
        source, refs, unavailable = _inspect(database, authorize)
        if source != manifest["source"] or unavailable != manifest["unavailable_artifacts"]:
            raise SnapshotError("bundle source closure differs")
        if [asdict(refs[key]) for key in sorted(refs)] != manifest["artifacts"]:
            raise SnapshotError("bundle artifact closure differs")
        if manifest["completeness"] != ("partial" if unavailable else "complete"):
            raise SnapshotError("bundle completeness differs")
        store = FileArtifactStore(path / "artifacts")
        for ref in refs.values():
            if not store.verify(ref):
                raise SnapshotError("bundle artifact is missing or corrupt")
        _inspect(database, authorize)
        _finalize_authorization(authorize)
        return manifest
    except PermissionError:
        raise
    except (OSError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error, TelemetryError) as error:
        raise SnapshotError("bundle verification failed") from error


def restore_bundle(bundle, destination, *, authorize=None):
    """Restore a verified closed bundle into a new, independently read location."""
    bundle = _safe_path(bundle)
    manifest = verify_bundle(bundle, authorize=authorize)
    destination = _safe_path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    _manifest(destination, {"format": FORMAT, "status": "incomplete"})
    try:
        _write(destination / "telemetry.sqlite3", _read(bundle / "telemetry.sqlite3"))
        source_store = FileArtifactStore(bundle / "artifacts")
        restored_store = FileArtifactStore(destination / "artifacts")
        for item in manifest["artifacts"]:
            ref = ArtifactRef(**item)
            restored_store.put(source_store.read(ref), ref.media_type)
        (destination / "artifacts").mkdir(mode=0o700, exist_ok=True)
        _manifest(destination, manifest)
        fresh_manifest = verify_bundle(destination, authorize=authorize)
    except BaseException as error:
        for name in ("telemetry.sqlite3", "telemetry.sqlite3-wal", "telemetry.sqlite3-shm", "telemetry.sqlite3-journal"):
            (destination / name).unlink(missing_ok=True)
        shutil.rmtree(destination / "artifacts", ignore_errors=True)
        _manifest(destination, {"format": FORMAT, "status": "incomplete"})
        if isinstance(error, TelemetryError):
            raise SnapshotError("restore artifact verification failed") from error
        raise
    return {"database_path": str(destination / "telemetry.sqlite3"),
            "artifact_root": str(destination / "artifacts"), "manifest": fresh_manifest}


class BundlePublisher(Protocol):
    """Optional destination adapter; lookup resolves lost acknowledgments."""

    def lookup(self, bundle_id, *, expected_lineage): ...

    def publish(self, bundle, *, expected_lineage): ...


def publish_bundle(bundle, publisher: BundlePublisher, *, expected_lineage, attempt_id):
    """Publish only verified bundles and report independently verified receipts.

    A destination's ``lookup`` must verify byte identity and lineage. An absent
    or unverifiable receipt never becomes publication success. No publisher is
    created or contacted unless the caller explicitly provides one.
    """
    manifest = verify_bundle(bundle)
    lineage = {key: manifest["source"][key] for key in ("dataset_id", "collector_id")}
    if dict(expected_lineage) != lineage:
        raise SnapshotError("publication collector lineage differs")
    receipt = {"bundle_id": manifest["bundle_id"], "attempt_id": attempt_id,
               "lineage": lineage, "status": "unavailable"}
    try:
        remote = publisher.lookup(manifest["bundle_id"], expected_lineage=lineage)
        if not remote:
            publisher.publish(Path(bundle), expected_lineage=lineage)
            remote = publisher.lookup(manifest["bundle_id"], expected_lineage=lineage)
        if (isinstance(remote, dict) and remote.get("verified") is True and
                remote.get("bundle_id") == manifest["bundle_id"] and remote.get("lineage") == lineage):
            receipt.update(status="verified", destination=remote.get("destination"), remote_receipt=remote)
    except Exception:
        # The next attempt always looks up the same identity before retransmit.
        receipt["reason"] = "publication_unavailable"
    return receipt
