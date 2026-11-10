"""The in-tree collector reads a corpus and a bundle written by the standalone collector.

The fixtures under tests/fixtures/telemetry were produced by xormania/cce 84229bb
through Xorchestrate 246206a; their README records the exact command.
"""

import base64
import hashlib
import json
import sqlite3

import pytest

import xorchestrate
from tests.hexagonal.support.telemetry_collector import (
    FIXTURE_BUNDLE_ID,
    FIXTURE_IDENTITY,
    FIXTURES,
    independent_digest,
    private_copy,
    tree_digests,
)
from xorchestrate.adapters.telemetry_collector.sqlite import SQLiteRepository
from xorchestrate.adapters.telemetry_snapshot import restore_bundle, verify_bundle
from xorchestrate.composition import build


@pytest.fixture
def committed():
    before = tree_digests(FIXTURES)
    yield
    assert tree_digests(FIXTURES) == before, "COMPATIBILITY_FIXTURES_MUST_NOT_BE_MODIFIED_BY_TESTS"


@pytest.fixture
def runtime(tmp_path, committed):
    root = tmp_path / "runtime"
    private_copy("corpus", root)
    (root / "exports").mkdir(mode=0o700)
    private_copy("bundle", root / "exports" / "legacy-bundle")
    built = build({"store": {"adapter": "memory"}, "telemetry": {"adapter": "local", "options": {
        "runtime_root": str(root), "producer_id": "fixture-producer", **FIXTURE_IDENTITY}}}, tmp_path)
    try:
        yield built
    finally:
        built.close()


def test_standalone_corpus_opens_with_its_format_and_digests(tmp_path, committed):
    database = private_copy("corpus", tmp_path / "corpus") / "events.sqlite3"
    repository = SQLiteRepository.inspect(database)
    try:
        assert repository.db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert repository.metadata == {"format": "agent-telemetry-v1", "dataset_id": "fixture-dataset",
                                       "collector_id": "fixture-collector"}
        tables = {row[0] for row in repository.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"metadata", "events", "links", "artifacts", "cursors"} <= tables
        rows = repository.db.execute("SELECT event_id,digest,observation FROM events ORDER BY sequence").fetchall()
        assert len(rows) == 4
        for row in rows:
            assert independent_digest(json.loads(row["observation"])) == row["digest"]
        status = repository.status()
        assert (status["event_count"], status["max_sequence"]) == (4, 4)
        queried = repository.query(project_id="fixture-project")
        assert [row["event_type"] for row in queried] == ["application.requested", "application.completed"] * 2
    finally:
        repository.close()


def test_local_telemetry_reads_hydrates_and_extends_a_standalone_corpus(runtime):
    sink = runtime.application.telemetry
    page = sink.scan(limit=10)
    assert page["scanned"] == 4 and len(page["events"]) == 4
    stored = [row for row in page["events"] if row.get("payload_storage")]
    assert stored, "FIXTURE_MUST_EXERCISE_ARTIFACT_STORED_PAYLOADS"
    hydrated = [sink.get_event(row["event_id"])["payload"] for row in stored]
    assert all(isinstance(payload, dict) for payload in hydrated)
    assert any("compatibility fixture: artifact payload " + "x" * 600 in json.dumps(payload) for payload in hydrated)
    for row in stored:
        digest = row["payload_storage"]["digest"]
        chunk = sink.read_payload(row["event_id"], digest)
        assert chunk["next_offset"] is None
        assert hashlib.sha256(base64.b64decode(chunk["data_base64"])).hexdigest() == digest
    assert "fixture-not-a-secret" not in json.dumps(page["events"])
    runtime.application.call("create_work", {"goal": "appended by the in-tree collector"})
    assert sink.status()["event_count"] == 6


def test_standalone_bundle_verifies_and_restores(tmp_path, runtime):
    sink = runtime.application.telemetry
    original = json.loads((FIXTURES / "bundle" / "manifest.json").read_text())
    assert original["software"]["agent_telemetry"] == "0.1.0"
    manifest = sink.verify("legacy-bundle")
    assert manifest == original and manifest["bundle_id"] == FIXTURE_BUNDLE_ID
    restored = sink.restore("legacy-bundle", "restored")
    assert restored["manifest"] == original
    with sqlite3.connect(f"file:{restored['database_path']}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 4
    copied = private_copy("bundle", tmp_path / "direct")
    assert verify_bundle(copied)["bundle_id"] == FIXTURE_BUNDLE_ID
    assert restore_bundle(copied, tmp_path / "direct-restored")["manifest"]["bundle_id"] == FIXTURE_BUNDLE_ID


def test_new_bundles_record_the_xorchestrate_version_and_keep_the_corpus_format(runtime):
    sink = runtime.application.telemetry
    manifest = sink.snapshot("fresh")
    assert manifest["software"].keys() == {"xorchestrate", "agent_telemetry_format", "sqlite"}
    assert manifest["software"]["xorchestrate"] == xorchestrate.__version__
    assert manifest["software"]["agent_telemetry_format"] == "agent-telemetry-v1"
    assert manifest["source"]["event_count"] == 4
    assert sink.restore("fresh", "fresh-restored")["manifest"] == manifest
