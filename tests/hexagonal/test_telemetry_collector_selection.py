"""Collector storage selection, inline option containment and composition defaults."""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.hexagonal.support.telemetry_collector import MODULE, MemoryArtifacts, MemoryCorpus
from xorchestrate.adapters.telemetry import LocalTelemetry, UnavailableTelemetry
from xorchestrate.adapters.telemetry_collector import sqlite as collector_sqlite
from xorchestrate.adapters.telemetry_collector.domain import TelemetryError
from xorchestrate.adapters.telemetry_collector.ports import CollectorScope
from xorchestrate.adapters.telemetry_collector.sqlite import SQLiteRepository
from xorchestrate.composition import TelemetryDefaults, build, factory_for
from xorchestrate.model import OrchestrationError

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


UNKNOWN, UNLOADABLE, INVALID, UNKNOWN_OPTION = "unknown_adapter", "adapter_unavailable", "invalid_option", "unknown_option"


@pytest.mark.parametrize(("options", "cause", "detail"), [
    ({"corpus": {"adapter": "unregistered"}}, UNKNOWN, "corpus: unregistered"),
    ({"artifacts": {"adapter": "unregistered"}}, UNKNOWN, "artifacts: unregistered"),
    ({"corpus": {"adapter": "missing_telemetry_module.nowhere:factory"}}, UNLOADABLE,
     "corpus: missing_telemetry_module.nowhere:factory"),
    ({"artifacts": {"adapter": MODULE + ":no_such_factory"}}, UNLOADABLE, "artifacts: " + MODULE + ":no_such_factory"),
    ({"artifacts": {"adapter": "no-such-adapter", "enabled": False}}, UNKNOWN, "artifacts: no-such-adapter"),
    ({"artifacts": {"adapter": "filesystem", "enabled": False, "options": {"root": "objects", "bogus": 1}}},
     INVALID, None),
    ({"artifacts": {"adapter": "filesystem", "enabled": False, "options": {"root": "/etc"}}}, INVALID, None),
    ({"corpus": {"adapter": "sqlite", "options": {"synchronous": "NORMAL"}}}, INVALID, None),
    ({"corpus": {"adapter": "sqlite", "options": {"unknown": True}}}, INVALID, None),
    ({"artifacts": {"adapter": "filesystem", "options": {"enabled": True}}}, INVALID, None),
    ({"corpus": {"adapter": "sqlite", "extra": True}}, INVALID, None),
    ({"corpus": {"adapter": "sqlite", "enabled": False}}, INVALID, None),
    ({"corpus": {"adapter": "sqlite"}, "database": "events.sqlite3"}, INVALID, None),
    ({"artifacts": {"adapter": "filesystem"}, "artifact_root": "objects"}, INVALID, None),
    ({"profile": "collector.toml"}, UNKNOWN_OPTION, "profile"),
    ({"capture_lock_timeout_ms": True}, INVALID, None),
    ({"capture_lock_timeout_ms": 60001}, INVALID, None),
    ({"database": "../outside.sqlite3"}, INVALID, None),
    ({"database": "/absolute.sqlite3"}, INVALID, None),
    ({"artifact_root": "."}, INVALID, None),
    ({"database": "shared/events.sqlite3", "artifact_root": "shared"}, INVALID, None),
    ({"database": "objects", "artifact_root": "objects"}, INVALID, None),
    ({"project_id": "selection\nproject"}, INVALID, None),
    ({"collector_id": "   "}, INVALID, None),
])
def test_invalid_or_unknown_storage_is_refused_without_creating_storage(tmp_path, runtimes, options, cause, detail):
    root = tmp_path / "corpus"
    runtime = build(local(root, **options), tmp_path)
    runtimes.append(runtime)
    sink = runtime.application.telemetry
    assert isinstance(sink, UnavailableTelemetry) and sink.status()["status"] == "unavailable"
    reported = runtime.application.call("telemetry_status", {})
    assert reported["status"] == "unavailable" and reported["cause"]["code"] == cause, \
        "STATUS_MUST_DISTINGUISH_THE_CONSTRUCTION_CAUSE"
    if detail is not None:
        assert reported["cause"]["detail"] == detail
    assert len(reported["cause"]["detail"] or "") <= 200
    assert runtime.application.call("create_work", {"goal": "unrelated work remains available"})["work"]["id"]
    assert not root.exists() or files(root) == []
    # The same refusal through the real composition resolver, not a stub.
    with pytest.raises(OrchestrationError) as refused:
        LocalTelemetry({"runtime_root": str(root), **IDENTITY, **options},
                       resolve=lambda component, name: factory_for("telemetry_" + component, name))
    assert refused.value.code == "telemetry_configuration"
    assert refused.value.details["cause"] == cause


def test_cause_never_carries_option_values(tmp_path, runtimes):
    runtime = build(local(tmp_path / "corpus", known_secrets=["configured-secret"],
                          corpus={"adapter": "sqlite", "options": {"database": "configured-secret/../../x"}}), tmp_path)
    runtimes.append(runtime)
    status = runtime.application.call("telemetry_status", {})
    assert status["cause"]["code"] == INVALID and "configured-secret" not in str(status)


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
    assert runtime.application.call("telemetry_status", {})["cause"] == {"code": "sqlite_version", "detail": None}
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


def test_null_section_is_absent_and_takes_project_defaults(tmp_path, runtimes):
    defaults = TelemetryDefaults(tmp_path / "defaults", "chosen-project", "instance-1")
    runtime = build({"store": {"adapter": "memory"}, "telemetry": None}, tmp_path, telemetry_defaults=defaults)
    runtimes.append(runtime)
    assert isinstance(runtime.application.telemetry, LocalTelemetry)
    assert runtime.application.telemetry.path == tmp_path / "defaults" / "events.sqlite3"


def test_snapshot_refuses_disabled_artifacts_when_the_corpus_refers_to_them(tmp_path, runtimes):
    root = tmp_path / "corpus"
    first = build(local(root, artifact_threshold_bytes=64), tmp_path)
    first.application.call("create_work", {"goal": "stored as an artifact " + "a" * 200})
    first.close()
    runtime = build(local(root, artifact_threshold_bytes=64,
                          artifacts={"adapter": "filesystem", "enabled": False}), tmp_path)
    runtimes.append(runtime)
    sink = runtime.application.telemetry
    with pytest.raises(OrchestrationError) as refused:
        sink.snapshot("bundle")
    assert refused.value.code == "telemetry_integrity"
    bundle = root / "exports" / "bundle"
    assert sorted(path.name for path in bundle.iterdir()) == ["manifest.json"]
    assert json.loads((bundle / "manifest.json").read_text())["status"] == "incomplete"
    with pytest.raises(OrchestrationError) as unusable:
        sink.restore("bundle", "restored")
    assert unusable.value.code == "telemetry_integrity"


def test_snapshot_without_artifacts_succeeds_when_none_are_referenced(tmp_path, runtimes):
    sink = sink_for(runtimes, tmp_path / "corpus", artifacts={"adapter": "filesystem", "enabled": False})
    runtimes[0].application.call("create_work", {"goal": "small payload"})
    manifest = sink.snapshot("bundle")
    assert manifest["status"] == "complete" and manifest["artifacts"] == []
    assert sink.restore("bundle", "restored")["manifest"] == manifest


def test_replacement_and_sqlite_corpora_return_the_same_source_match_shape(tmp_path, runtimes):
    shapes = []
    for name, options in (("sqlite", {}), ("memory", {"corpus": {"adapter": MODULE + ":memory_corpus"}})):
        sink = sink_for(runtimes, tmp_path / name, **options)
        runtimes[-1].application.call("create_work", {"goal": "shape " + name})
        requested = sink.scan()["events"][0]
        connection = sink.corpus.inspect()
        try:
            found = connection.find_by_source(project_id=IDENTITY["project_id"], event_type="application.completed",
                                              path=("xorchestrate", "attempt_id"),
                                              value=requested["context"]["attempt_id"])
        finally:
            connection.close()
        assert len(found) == 1
        shapes.append(set(found[0]))
    assert shapes[0] == shapes[1]
    assert {"sequence", "digest", "captured_at"}.isdisjoint(shapes[0])


def test_adapters_must_declare_locations_and_database_path(tmp_path):
    def resolve(component, name):
        if component == "corpus":
            return lambda options, scope: _Undeclared()
        return factory_for("telemetry_" + component, name)

    with pytest.raises(OrchestrationError) as refused:
        LocalTelemetry({"runtime_root": str(tmp_path / "corpus"), **IDENTITY}, resolve=resolve)
    assert refused.value.details == {"cause": "invalid_adapter", "detail": "corpus: locations"}


class _Undeclared:
    def initialize(self):
        raise AssertionError("an adapter without declared locations must be refused before initialization")


def test_sqlite_get_and_source_lookup(tmp_path, runtimes):
    sink = sink_for(runtimes, tmp_path / "corpus")
    runtimes[0].application.call("create_work", {"goal": "lookups"})
    requested, completed = sink.scan()["events"]
    repository = SQLiteRepository.inspect(sink.path)
    try:
        assert repository.get(completed["event_id"], project_id=IDENTITY["project_id"])["sequence"] == 2
        assert repository.get(completed["event_id"], project_id="other-project") is None
        assert repository.get("missing", project_id=IDENTITY["project_id"]) is None
        attempt = requested["context"]["attempt_id"]
        found = repository.find_by_source(project_id=IDENTITY["project_id"], event_type="application.completed",
                                          path=("xorchestrate", "attempt_id"), value=attempt)
        assert [item["event_id"] for item in found] == [completed["event_id"]]
        assert repository.find_by_source(project_id=IDENTITY["project_id"], event_type="application.completed",
                                         path=("xorchestrate", "attempt_id"), value="other") == []
        for path in ((), ("a b",), ("xorchestrate", "attempt_id')||'"), ("1x",), ("x", 3)):
            with pytest.raises(TelemetryError) as refused:
                repository.find_by_source(project_id=IDENTITY["project_id"], event_type="application.completed",
                                          path=path, value=attempt)
            assert refused.value.code == "invalid_query"
    finally:
        repository.close()


def test_without_project_defaults_an_absent_section_constructs_no_collector(tmp_path, runtimes):
    runtime = build({"store": {"adapter": "memory"}}, tmp_path)
    runtimes.append(runtime)
    assert runtime.application.telemetry is None
    assert runtime.application.call("telemetry_status", {})["status"] == "disabled"


@pytest.mark.parametrize("defaults", [
    {"runtime_root": "/absolute/root", "project_id": "p", "collector_id": "c"},
    TelemetryDefaults("", "p", "c"),
    TelemetryDefaults("relative/root", "p", "c"),
    TelemetryDefaults("~/telemetry", "p", "c"),
    TelemetryDefaults(None, "p", "c"),
    TelemetryDefaults(b"/absolute/root", "p", "c"),
    TelemetryDefaults("/absolute/root", "", "c"),
    TelemetryDefaults("/absolute/root", "   ", "c"),
    TelemetryDefaults("/absolute/root", "p\n", "c"),
    TelemetryDefaults("/absolute/root", "p", "c\x00"),
    TelemetryDefaults("/absolute/root", "p", "c", dataset_id=""),
])
def test_invalid_project_defaults_are_refused(tmp_path, defaults):
    with pytest.raises(OrchestrationError) as refused:
        build({"store": {"adapter": "memory"}}, tmp_path, telemetry_defaults=defaults)
    assert refused.value.code == "configuration_error"
    assert os.listdir(tmp_path) == []
