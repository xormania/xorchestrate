"""Collector storage subjects: committed compatibility fixtures and replacement adapters.

The replacement adapters are deliberately small in-memory implementations of the
collector's storage ports. Selecting them through configuration demonstrates that
the local telemetry adapter depends on the ports, not on SQLite or files.
"""

import hashlib
import json
import os
import shutil
import threading
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from xorchestrate.adapters.telemetry_collector.domain import (
    ArtifactRef,
    CaptureResult,
    Receipt,
    TelemetryError,
    observation_digest,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "telemetry"
FIXTURE_IDENTITY = {"project_id": "fixture-project", "dataset_id": "fixture-dataset",
                    "collector_id": "fixture-collector"}
FIXTURE_BUNDLE_ID = "fc12e9bbb5f099641bd64e724b2dd3d81118d50b14ea0b2544568edc71dad901"
MODULE = "tests.hexagonal.support.telemetry_collector"
_COLLECTION_FIELDS = {"sequence", "digest", "captured_at"}


def private_copy(name, destination):
    """Copy one committed fixture tree with the private modes storage requires.

    A checkout stores files as 0644; the collector refuses group/world-readable
    database files, so tests never open the committed bytes in place.
    """
    destination = Path(destination)
    shutil.copytree(FIXTURES / name, destination)
    for directory, _, files in os.walk(destination):
        os.chmod(directory, 0o700)
        for file in files:
            os.chmod(Path(directory) / file, 0o600)
    return destination


def independent_digest(observation):
    """Recompute an observation digest without the collector's helpers."""
    text = json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def tree_digests(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob("*")) if path.is_file()}


class MemoryCorpus:
    """A shared in-memory corpus; ``open``/``inspect`` return connection views."""

    instances = []

    def __init__(self, scope, options):
        self.scope, self.options = scope, options
        self.rows, self.cursors, self.lock = [], {}, threading.Lock()
        self.initialized = False
        self.locations = ()
        self.database_path = None
        MemoryCorpus.instances.append(self)

    def initialize(self):
        self.initialized = True

    def open(self):
        return _MemoryConnection(self, readonly=False)

    def inspect(self, location=None):
        if location is not None:
            raise TelemetryError("unsupported", "memory corpus has no preserved locations")
        if not self.initialized:
            raise TelemetryError("absent", "corpus does not exist")
        return _MemoryConnection(self, readonly=True)


class _MemoryConnection:
    def __init__(self, corpus, *, readonly):
        self.corpus, self.readonly = corpus, readonly

    def append(self, event, captured_at, cursor=None):
        if self.readonly:
            return CaptureResult("unavailable", reason="readonly")
        if (event.context.dataset_id, event.context.collector_id) != (
                self.corpus.scope.dataset_id, self.corpus.scope.collector_id):
            return CaptureResult("rejected", reason="identity_mismatch")
        digest = observation_digest(event)
        with self.corpus.lock:
            for row in self.corpus.rows:
                if row["event_id"] == event.event_id:
                    if row["digest"] != digest:
                        return CaptureResult("rejected", reason="conflict")
                    return CaptureResult("duplicate", Receipt(event.event_id, row["sequence"], digest, row["captured_at"]))
            sequence = len(self.corpus.rows) + 1
            # Rows keep the stored observation separate from collection metadata.
            self.corpus.rows.append({**json.loads(json.dumps(asdict(event))), "sequence": sequence,
                                     "digest": digest, "captured_at": captured_at})
            if cursor is not None:
                self.corpus.cursors[cursor[0]] = cursor[1]
        return CaptureResult("recorded", Receipt(event.event_id, sequence, digest, captured_at))

    def _row(self, row):
        known = {item["event_id"] for item in self.corpus.rows}
        return {**row, "context_json": json.dumps(row["context"], sort_keys=True, separators=(",", ":")),
                "unresolved_links": [{"relation": relation, "target_id": target}
                                     for relation, target in row["links"] if target not in known]}

    def query(self, *, project_id=None, event_type=None, after=0, limit=100):
        rows = [row for row in self.corpus.rows if row["sequence"] > after
                and project_id in (None, row["context"]["project_id"]) and event_type in (None, row["event_type"])]
        return [self._row(row) for row in rows[:limit]]

    def get(self, event_id, *, project_id):
        return next((self._row(row) for row in self.corpus.rows
                     if row["event_id"] == event_id and row["context"]["project_id"] == project_id), None)

    def find_by_source(self, *, project_id, event_type, path, value):
        """Return stored observations only, as the SQLite corpus does."""
        found = []
        for row in self.corpus.rows:
            if row["context"]["project_id"] != project_id or row["event_type"] != event_type:
                continue
            item = json.loads(row["source_json"])
            for part in path:
                item = item.get(part) if isinstance(item, dict) else None
            if item == value:
                found.append({key: deepcopy(item) for key, item in row.items() if key not in _COLLECTION_FIELDS})
        return found

    def status(self):
        rows = self.corpus.rows
        return {"event_count": len(rows), "max_sequence": rows[-1]["sequence"] if rows else None,
                "last_capture": rows[-1]["captured_at"] if rows else None,
                "coverage": "observed" if rows else "unknown", "publication": "unknown", "schema_version": 1,
                "dataset_id": self.corpus.scope.dataset_id, "collector_id": self.corpus.scope.collector_id,
                "cursors": dict(self.corpus.cursors)}

    def close(self):
        pass


class MemoryArtifacts:
    instances = []

    def __init__(self, options):
        self.options, self.objects = options, {}
        self.locations = ()
        MemoryArtifacts.instances.append(self)

    def put(self, data, media_type="application/octet-stream"):
        digest = hashlib.sha256(data).hexdigest()
        self.objects[digest] = bytes(data)
        return ArtifactRef(digest, len(data), media_type)

    def read(self, ref):
        data = self.objects.get(ref.digest)
        if data is None or len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.digest:
            raise TelemetryError("artifact_integrity", "artifact unavailable")
        return data

    def verify(self, ref):
        try:
            self.read(ref)
            return True
        except TelemetryError:
            return False


def memory_corpus(options, scope):
    return MemoryCorpus(scope, options)


def memory_artifacts(options, scope):
    return MemoryArtifacts(options)


class SelectedOperationalSink:
    """A configured signal sink with controlled failure and pressure variants."""

    instances = []

    def __init__(self, options, scope):
        self.options, self.scope = deepcopy(options), scope
        self.events = []
        self.entered, self.release = threading.Event(), threading.Event()
        if not options.get("blocked"):
            self.release.set()
        SelectedOperationalSink.instances.append(self)

    def emit(self, signal):
        self.entered.set()
        self.release.wait()
        if self.options.get("fails"):
            raise RuntimeError("private sink exception must not escape")
        self.events.append(deepcopy(signal))


def operational_sink(options, scope):
    return SelectedOperationalSink(options, scope)


def invalid_operational_sink(options, scope):
    return object()


def raising_operational_sink(options, scope):
    raise RuntimeError("private configured sink credential")
