"""Concrete telemetry datasets and test sinks for preservation contracts."""

import hashlib
import importlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agent_telemetry.application import Collector
from agent_telemetry.artifacts import FileArtifactStore
from agent_telemetry.domain import ArtifactRef, CaptureContext, Event
from agent_telemetry.sqlite import SQLiteRepository


class Dataset:
    def __init__(self, root):
        self.root = Path(root)
        self.database = self.root / "live.sqlite3"
        self.artifacts = FileArtifactStore(self.root / "objects")
        self.repository = SQLiteRepository.initialize(
            self.database, dataset_id="fixture-dataset", collector_id="fixture-collector",
        )
        self.repository.db.execute("PRAGMA wal_autocheckpoint=0")
        self.collector = Collector(self.repository, self.artifacts)

    def event(self, identity, payload=None, *, content=None, work_id="work-public", refs=()):
        references = list(refs)
        if content is not None:
            references.append(self.artifacts.put(content, "text/plain"))
        return Event(
            identity, "fixture.observation", json.dumps({} if payload is None else payload),
            CaptureContext("fixture-project", "fixture-dataset", "fixture-collector", "fixture-producer",
                           session_id="fixture-session", session_generation="generation-1", task_id=work_id),
            artifacts=tuple(references),
        )

    def append(self, identity, payload=None, **options):
        event = self.event(identity, payload, **options)
        result = self.collector.record(event, cursor=("fixture-stream", identity))
        if result.status != "recorded":
            raise AssertionError(f"fixture event was not recorded: {result}")
        return event, result.receipt

    def append_from_another_connection(self, identity, *, content):
        repository = SQLiteRepository(self.database)
        try:
            collector = Collector(repository, self.artifacts)
            event = self.event(identity, {"writer": "concurrent"}, content=content)
            result = collector.record(event, cursor=("fixture-stream", identity))
            if result.status != "recorded":
                raise AssertionError(f"concurrent fixture event was not recorded: {result}")
        finally:
            repository.close()

    def backup_after_late_writer(self, source, target):
        failures = []

        def write_late():
            try:
                self.append_from_another_connection("late", content=b"late")
            except BaseException as error:
                failures.append(error)

        writer = threading.Thread(target=write_late)
        writer.start()
        writer.join(5)
        if writer.is_alive():
            raise AssertionError("snapshot prevented another WAL writer from completing")
        if failures:
            raise AssertionError(f"concurrent writer failed: {failures}")
        source.backup(target, pages=1)

    def object_path(self, reference):
        digest = reference.digest.removeprefix("sha256:")
        return self.artifacts.root / "sha256" / digest[:2] / digest

    def rows(self):
        return self.repository.query(limit=1000)

    def close(self):
        self.repository.close()


def module(name):
    return importlib.import_module("xorchestrate." + name)


def snapshot_api():
    return module("adapters.telemetry_snapshot")


def view_api():
    return module("telemetry_views")


def export_api():
    return module("adapters.telemetry_export")


def observation(identity, sequence, payload, *, event_type="usage.sample", **extra):
    return {"event_id": identity, "sequence": sequence, "event_type": event_type,
            "payload_json": json.dumps(payload), "context": {"project_id": "fixture-project",
            "session_id": "sensitive-session", "task_id": "work-public"}, **extra}


def unavailable_reference():
    return ArtifactRef("a" * 64, 0, availability="unavailable", reason="not_exposed")


def read_database(path):
    with sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True) as database:
        rows = database.execute("SELECT sequence, event_id, observation FROM events ORDER BY sequence").fetchall()
        return [{"sequence": row[0], "event_id": row[1], "observation": json.loads(row[2])} for row in rows]


def read_manifest(path):
    return json.loads((Path(path) / "manifest.json").read_text())


def write_manifest(path, manifest):
    (Path(path) / "manifest.json").write_text(json.dumps(manifest))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reference_dict(reference):
    return asdict(reference)


@contextmanager
def revoke_after_bundle_verification(bundle, allowed):
    original = FileArtifactStore.verify

    def verify(store, reference):
        result = original(store, reference)
        if store.root == Path(bundle) / "artifacts":
            allowed[0] = False
        return result

    with patch.object(FileArtifactStore, "verify", verify):
        yield


class Sink:
    def __init__(self, *, fails=False):
        self.events = []
        self.fails = fails

    def emit(self, event):
        if self.fails:
            raise OSError("private source exception text")
        self.events.append(deepcopy(event))


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now
