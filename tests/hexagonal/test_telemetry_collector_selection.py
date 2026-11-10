"""Collector storage selection, inline option containment and composition defaults."""

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.hexagonal.support.telemetry_collector import MODULE, MemoryArtifacts, MemoryCorpus
from xorchestrate.adapters.telemetry import LocalTelemetry, UnavailableTelemetry
from xorchestrate.adapters.telemetry_collector import artifacts as collector_artifacts
from xorchestrate.adapters.telemetry_collector import sqlite as collector_sqlite
from xorchestrate.adapters.telemetry_collector.domain import TelemetryError
from xorchestrate.adapters.telemetry_collector.ports import CollectorScope
from xorchestrate.composition import TelemetryDefaults, build
from xorchestrate.model import OrchestrationError

BUILT_IN = {"corpus": {"sqlite": collector_sqlite.corpus}, "artifacts": {"filesystem": collector_artifacts.filesystem}}
IDENTITY = {"project_id": "selection-project", "dataset_id": "selection-dataset", "collector_id": "selection-collector"}


def local(root, **options):
    return {"store": {"adapter": "memory"}, "telemetry": {"adapter": "local", "options": {
        "runtime_root": str(root), **IDENTITY, **options}}}


def construct(root, **options):
    """Build one runtime and return its telemetry sink with the runtime for cleanup."""
    runtime = build(local(root, **options), root.parent)
    return runtime, runtime.application.telemetry


def files(root):
    return sorted(str(path.relative_to(root)) for path in Path(root).rglob("*") if path.is_file())


@pytest.fixture
def runtimes():
    opened = []
    yield opened
    for runtime in opened:
        runtime.close()


def sink_for(runtimes, root, **options):
    runtime, sink = construct(root, **options)
    runtimes.append(runtime)
    return sink


def test_default_selection_is_the_sqlite_corpus_with_filesystem_artifacts(tmp_path, runtimes):
    sink = sink_for(runtimes, tmp_path / "corpus", artifact_threshold_bytes=64)
    runtimes[0].application.call("create_work", {"goal": "default storage " + "x" * 200})
    assert isinstance(sink.corpus, collector_sqlite.SQLiteCorpus)
    assert sink.path == tmp_path / "corpus" / "events.sqlite3" and sink.path.is_file()
    assert any(name.startswith("artifacts/sha256/") for name in files(tmp_path / "corpus"))
    with sqlite3.connect(sink.path.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert dict(connection.execute("SELECT key,value FROM metadata"))["format"] == "agent-telemetry-v1"
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_nested_selection_and_shorthand_name_the_same_storage(tmp_path, runtimes):
    nested = sink_for(runtimes, tmp_path / "nested", corpus={"adapter": "sqlite", "options": {
        "database": "data/live.sqlite3", "capture_lock_timeout_ms": 10}},
        artifacts={"adapter": "filesystem", "options": {"root": "objects"}})
    shorthand = sink_for(runtimes, tmp_path / "shorthand", database="data/live.sqlite3",
                         capture_lock_timeout_ms=10, artifact_root="objects")
    for root, sink in ((tmp_path / "nested", nested), (tmp_path / "shorthand", shorthand)):
        assert sink.path == root / "data" / "live.sqlite3" and sink.path.is_file()
        assert sink.artifact_root == root / "objects"
        assert sink.corpus.timeout_ms == 10


def test_selected_replacement_adapters_receive_capture_without_sqlite_or_files(tmp_path, runtimes):
    MemoryCorpus.instances.clear()
    MemoryArtifacts.instances.clear()
    sink = sink_for(runtimes, tmp_path / "corpus", artifact_threshold_bytes=64,
                    corpus={"adapter": MODULE + ":memory_corpus", "options": {"label": "chosen"}},
                    artifacts={"adapter": MODULE + ":memory_artifacts"})
    app = runtimes[0].application
    work = app.call("create_work", {"goal": "replacement storage " + "y" * 200})["work"]
    corpus, = MemoryCorpus.instances
    artifacts, = MemoryArtifacts.instances
    assert corpus.options == {"label": "chosen"}
    assert corpus.scope == CollectorScope((tmp_path / "corpus").resolve(), IDENTITY["dataset_id"],
                                          IDENTITY["collector_id"])
    assert [row["event_type"] for row in corpus.rows] == ["application.requested", "application.completed"]
    assert artifacts.objects, "PAYLOADS_ABOVE_THRESHOLD_MUST_USE_THE_SELECTED_ARTIFACT_ADAPTER"
    page = sink.scan()
    assert [row["event_type"] for row in page["events"]] == ["application.requested", "application.completed"]
    assert sink.resolve_scope(page["events"][0]) == {"work_id": work["id"]}
    assert [row["event_id"] for row in sink.scan(work_id=work["id"])["events"]] == [page["events"][1]["event_id"]]
    hydrated = sink.get_event(page["events"][0]["event_id"])
    assert "y" * 200 in str(hydrated["payload"])
    assert sink.status()["event_count"] == 2
    assert files(tmp_path) == [], "REPLACEMENT_ADAPTERS_MUST_NOT_CREATE_SQLITE_OR_ARTIFACT_FILES"
    with pytest.raises(OrchestrationError) as refused:
        app.call("telemetry_snapshot", {"destination": "bundle"})
    assert refused.value.code == "telemetry_unsupported"


def test_disabled_artifact_selection_keeps_payloads_inline(tmp_path, runtimes):
    sink = sink_for(runtimes, tmp_path / "corpus", artifact_threshold_bytes=64,
                    artifacts={"adapter": "filesystem", "enabled": False})
    work = runtimes[0].application.call("create_work", {"goal": "inline " + "z" * 200})["work"]
    assert sink.artifacts is None and not (tmp_path / "corpus" / "artifacts").exists()
    rows = sink.scan(work_id=work["id"])["events"]
    assert rows and all(not row["artifacts"] for row in rows)
    assert "z" * 200 in str(rows[0]["payload"])


@pytest.mark.parametrize("options", [
    {"corpus": {"adapter": "unregistered"}},
    {"artifacts": {"adapter": "unregistered"}},
    {"corpus": {"adapter": "sqlite", "options": {"synchronous": "NORMAL"}}},
    {"corpus": {"adapter": "sqlite", "options": {"unknown": True}}},
    {"artifacts": {"adapter": "filesystem", "options": {"enabled": True}}},
    {"corpus": {"adapter": "sqlite", "extra": True}},
    {"corpus": {"adapter": "sqlite", "enabled": False}},
    {"corpus": {"adapter": "sqlite"}, "database": "events.sqlite3"},
    {"artifacts": {"adapter": "filesystem"}, "artifact_root": "objects"},
    {"profile": "collector.toml"},
    {"capture_lock_timeout_ms": True},
    {"capture_lock_timeout_ms": 60001},
    {"database": "../outside.sqlite3"},
    {"database": "/absolute.sqlite3"},
    {"artifact_root": "."},
    {"database": "shared/events.sqlite3", "artifact_root": "shared"},
    {"database": "objects", "artifact_root": "objects"},
])
def test_invalid_or_unknown_storage_is_refused_without_creating_storage(tmp_path, runtimes, options):
    root = tmp_path / "corpus"
    runtime = build(local(root, **options), tmp_path)
    runtimes.append(runtime)
    sink = runtime.application.telemetry
    assert isinstance(sink, UnavailableTelemetry) and sink.status()["status"] == "unavailable"
    assert runtime.application.call("create_work", {"goal": "unrelated work remains available"})["work"]["id"]
    assert not root.exists() or files(root) == []
    with pytest.raises(OrchestrationError) as refused:
        LocalTelemetry({"runtime_root": str(root), **IDENTITY, **options},
                       resolve=lambda component, name: BUILT_IN[component].get(name, _unknown))
    assert refused.value.code == "telemetry_configuration"


def _unknown(options, scope):
    raise OrchestrationError("Unknown adapter.", "telemetry_configuration")


def test_storage_kinds_are_not_top_level_sections(tmp_path):
    with pytest.raises(OrchestrationError) as refused:
        build({"store": {"adapter": "memory"}, "telemetry_corpus": {"adapter": "sqlite"}}, tmp_path)
    assert refused.value.code == "configuration_error"
    assert refused.value.details["sections"] == ["telemetry_corpus"]


def test_sqlite_corpus_synchronization_is_fixed_full(tmp_path):
    scope = CollectorScope(tmp_path, "dataset", "collector")
    with pytest.raises(TelemetryError) as refused:
        collector_sqlite.corpus({"synchronous": "FULL"}, scope)
    assert refused.value.code == "invalid_config"
    corpus = collector_sqlite.corpus({}, scope)
    corpus.initialize()
    connection = corpus.open()
    try:
        assert connection.db.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()


def test_reads_never_initialize_absent_corpus(tmp_path):
    corpus = collector_sqlite.corpus({}, CollectorScope(tmp_path, "dataset", "collector"))
    with pytest.raises(TelemetryError) as absent:
        corpus.inspect()
    assert absent.value.code == "absent"
    assert list(tmp_path.iterdir()) == []


def test_relative_runtime_root_follows_configuration_directory_not_caller_cwd(tmp_path, monkeypatch, runtimes):
    configuration_directory, unrelated = tmp_path / "configuration", tmp_path / "unrelated"
    configuration_directory.mkdir()
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    runtime = build(local("relative-corpus"), configuration_directory)
    runtimes.append(runtime)
    assert runtime.application.telemetry.path == configuration_directory / "relative-corpus" / "events.sqlite3"
    assert list(unrelated.iterdir()) == []


def test_configuration_identity_changes_with_selected_storage(tmp_path, runtimes):
    first = sink_for(runtimes, tmp_path / "first", database="one.sqlite3")
    same = sink_for(runtimes, tmp_path / "first", database="one.sqlite3")
    other = sink_for(runtimes, tmp_path / "first", database="two.sqlite3")
    assert first.configuration_identity == same.configuration_identity != other.configuration_identity


def test_symlinked_storage_is_refused(tmp_path, runtimes):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    for configuration in (local(root, database="linked/events.sqlite3"), local(root, artifact_root="linked"),
                          local(alias)):
        runtime = build(configuration, tmp_path)
        runtimes.append(runtime)
        assert isinstance(runtime.application.telemetry, UnavailableTelemetry)
    assert list(outside.iterdir()) == []


def test_sqlite_without_the_wal_fix_is_a_visible_construction_failure(tmp_path, runtimes):
    with patch.object(collector_sqlite.sqlite3, "sqlite_version_info", (3, 45, 1)):
        runtime = build(local(tmp_path / "corpus"), tmp_path)
    runtimes.append(runtime)
    assert isinstance(runtime.application.telemetry, UnavailableTelemetry)
    assert not (tmp_path / "corpus" / "events.sqlite3").exists()
    with pytest.raises(TelemetryError) as refused, \
            patch.object(collector_sqlite.sqlite3, "sqlite_version_info", (3, 51, 2)):
        collector_sqlite.require_sqlite()
    assert refused.value.code == "sqlite_version"
    for fixed in ((3, 51, 3), (3, 50, 7), (3, 44, 6)):
        with patch.object(collector_sqlite.sqlite3, "sqlite_version_info", fixed):
            collector_sqlite.require_sqlite()


def test_project_defaults_construct_the_local_collector_without_a_telemetry_section(tmp_path, runtimes):
    defaults = TelemetryDefaults(tmp_path / "home" / "telemetry", "chosen-project", "instance-1")
    runtime = build({"store": {"adapter": "memory"}}, tmp_path, telemetry_defaults=defaults)
    runtimes.append(runtime)
    sink = runtime.application.telemetry
    assert isinstance(sink, LocalTelemetry)
    assert (sink.project_id, sink.collector_id, sink.dataset_id) == ("chosen-project", "instance-1", "default")
    runtime.application.call("create_work", {"goal": "captured by default"})
    assert sink.path == tmp_path / "home" / "telemetry" / "events.sqlite3"
    assert sink.status()["event_count"] == 2
    overridden = build({"store": {"adapter": "memory"}}, tmp_path, telemetry_defaults=TelemetryDefaults(
        tmp_path / "trial", "chosen-project", "instance-1", dataset_id="experiment-routing"))
    runtimes.append(overridden)
    assert overridden.application.telemetry.dataset_id == "experiment-routing"


def test_project_defaults_respect_disabled_and_explicit_sections(tmp_path, runtimes):
    defaults = TelemetryDefaults(tmp_path / "defaults", "chosen-project", "instance-1")
    disabled = build({"store": {"adapter": "memory"}, "telemetry": {"enabled": False}}, tmp_path,
                     telemetry_defaults=defaults)
    runtimes.append(disabled)
    assert disabled.application.telemetry is None
    explicit = build(local(tmp_path / "explicit"), tmp_path, telemetry_defaults=defaults)
    runtimes.append(explicit)
    assert explicit.application.telemetry.project_id == IDENTITY["project_id"]
    assert not (tmp_path / "defaults").exists()


def test_without_project_defaults_an_absent_section_constructs_no_collector(tmp_path, runtimes):
    runtime = build({"store": {"adapter": "memory"}}, tmp_path)
    runtimes.append(runtime)
    assert runtime.application.telemetry is None
    assert runtime.application.call("telemetry_status", {})["status"] == "disabled"


@pytest.mark.parametrize("defaults", [
    {"runtime_root": "root", "project_id": "p", "collector_id": "c"},
    TelemetryDefaults("root", "", "c"),
    TelemetryDefaults(None, "p", "c"),
])
def test_invalid_project_defaults_are_refused(tmp_path, defaults):
    with pytest.raises(OrchestrationError) as refused:
        build({"store": {"adapter": "memory"}}, tmp_path, telemetry_defaults=defaults)
    assert refused.value.code == "configuration_error"
    assert os.listdir(tmp_path) == []
