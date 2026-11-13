"""Operational sink selection through trusted telemetry composition."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from tests.hexagonal.support.native import wait_for
from tests.hexagonal.support.producer_capture import HttpFixture
from tests.hexagonal.support.telemetry import Fixture
from tests.hexagonal.support.telemetry_collector import MODULE, SelectedOperationalSink
from xorchestrate.adapters.telemetry_collector.ports import CollectorScope
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError


@pytest.fixture
def telemetry(tmp_path):
    SelectedOperationalSink.instances.clear()
    subject = Fixture(tmp_path)
    yield subject
    for sink in SelectedOperationalSink.instances:
        sink.release.set()
    subject.close()


def configure(subject, operational):
    subject.configuration["telemetry"]["options"]["operational"] = operational
    return subject.app


def operational_status(app):
    return app.call("telemetry_status", {})["operational"]


def test_selected_factory_receives_scope_and_exports_protected_signals(telemetry):
    options = {"sink": {"adapter": MODULE + ":operational_sink", "options": {"label": "chosen"}}}
    original = deepcopy(options)
    app = configure(telemetry, options)
    assert operational_status(app)["status"] == "ready", "SELECTED_OPERATIONAL_FACTORY_MUST_BE_CONSTRUCTED"
    sink, = SelectedOperationalSink.instances
    assert sink.options == {"label": "chosen"}
    assert sink.scope == CollectorScope((telemetry.root / "corpus").resolve(), "dataset-tests", "collector-tests")
    app.call("create_work", {"goal": "private-source-payload configured-secret"})
    wait_for(lambda: operational_status(app)["dispatch"]["pending"] == 0,
             message="selected sink did not finish the admitted signals")
    assert sink.events, "SELECTED_OPERATIONAL_SINK_MUST_RECEIVE_APPLICATION_SIGNALS"
    assert {event["kind"] for event in sink.events} == {"log", "metric", "span"}
    encoded = json.dumps(sink.events)
    assert "private-source-payload" not in encoded and "configured-secret" not in encoded
    assert app.call("telemetry_status", {})["event_count"] == 2
    assert options == original, "SINK_SELECTION_MUST_NOT_MUTATE_TRUSTED_CONFIGURATION"


@pytest.mark.parametrize("operational", [
    {"adapter": "local", "capacity": 2},
    {"sink": {"adapter": "local", "options": {"capacity": 2}}},
])
def test_legacy_and_nested_local_sink_selections_keep_working(telemetry, operational):
    app = configure(telemetry, operational)
    assert operational_status(app)["status"] == "ready", "LOCAL_OPERATIONAL_SELECTION_MUST_BE_READY"
    app.call("create_work", {"goal": "local signal compatibility"})
    wait_for(lambda: operational_status(app)["dispatch"]["pending"] == 0)
    assert app.telemetry.status()["operational"]["exported"] > 0
    assert app.telemetry.status()["operational"]["sink"] == "LocalOperationalSink"
    sink = app.telemetry._operational.exporter.sink
    assert sink.capacity == 2 and len(sink.events) == 2, "LOCAL_SINK_SELECTION_MUST_FORWARD_CAPACITY"


@pytest.mark.parametrize("nested", [False, True])
def test_legacy_and_nested_otlp_select_the_configured_endpoint_and_timeout(telemetry, nested):
    with HttpFixture(b"{}") as server:
        options = {"endpoint": f"http://127.0.0.1:{server.server.server_port}", "timeout": 0.3}
        operational = {"sink": {"adapter": "otlp", "options": options}} if nested else {"adapter": "otlp", **options}
        app = configure(telemetry, operational)
        assert operational_status(app)["status"] == "ready", "OTLP_SELECTION_MUST_BE_READY"
        sink = app.telemetry._operational.exporter.sink
        assert (sink.endpoint, sink.timeout) == (options["endpoint"], 0.3), "OTLP_SELECTION_MUST_FORWARD_OPTIONS"
        assert server.requests == [], "CONSTRUCTING_AN_OPTIONAL_SINK_MUST_NOT_CONTACT_ITS_BACKEND"
        app.call("create_work", {"goal": "private-otlp-payload"})
        wait_for(lambda: operational_status(app)["dispatch"]["pending"] == 0)
        assert server.requests, "SELECTED_OTLP_SINK_MUST_EXPORT_TO_THE_CONFIGURED_BACKEND"
        assert app.telemetry.status()["operational"]["failures"] == 0
        assert "private-otlp-payload" not in str(server.requests)
        assert app.call("telemetry_status", {})["event_count"] == 2


def test_sampling_selected_sink_leaves_complete_corpus(telemetry):
    app = configure(telemetry, {"sink": {"adapter": MODULE + ":operational_sink"}, "trace_sample_rate": 0})
    assert operational_status(app)["status"] == "ready", "SELECTED_SINK_MUST_RETAIN_SHARED_SAMPLING"
    app.call("create_work", {"goal": "sample only optional spans"})
    wait_for(lambda: operational_status(app)["dispatch"]["pending"] == 0)
    sink, = SelectedOperationalSink.instances
    assert sink.events and all(event["kind"] != "span" for event in sink.events)
    assert app.telemetry.status()["operational"]["sampled_out"] > 0
    assert app.call("telemetry_status", {})["event_count"] == 2


@pytest.mark.parametrize("selection", [
    None,
    "not-a-selection",
    {},
    {"adapter": "unregistered"},
    {"adapter": "missing_operational_module:factory"},
    {"adapter": MODULE + ":invalid_operational_sink"},
    {"adapter": MODULE + ":raising_operational_sink"},
    {"adapter": "local", "options": {"capacity": 0}},
    {"adapter": "local", "enabled": False},
    {"adapter": "local", "extra": "private-option-value"},
])
def test_invalid_sink_selection_is_safe_and_corpus_remains_available(telemetry, selection):
    app = configure(telemetry, {"sink": selection})
    app.call("create_work", {"goal": "optional sink failure cannot remove this work"})
    status = app.call("telemetry_status", {})
    assert status["status"] == "ready" and status["event_count"] == 2
    assert status["operational"]["status"] == "unavailable"
    assert status["operational"]["corpus_coverage"] == "independent"
    assert app.telemetry.status()["operational"]["reason"] == "configuration_unavailable"
    assert "private" not in json.dumps(status["operational"])


@pytest.mark.parametrize("legacy", [{"adapter": "local"}, {"capacity": 2}, {"endpoint": "private-endpoint"},
                                    {"timeout": 1}])
def test_nested_selection_refuses_ambiguous_legacy_sink_options(telemetry, legacy):
    app = configure(telemetry, {"sink": {"adapter": MODULE + ":operational_sink"}, **legacy})
    assert operational_status(app)["status"] == "unavailable"
    assert SelectedOperationalSink.instances == [], "CONFLICTING_SELECTION_MUST_NOT_CONSTRUCT_A_SINK"
    app.call("create_work", {"goal": "configuration refusal remains optional"})
    assert app.call("telemetry_status", {})["event_count"] == 2


def test_failed_custom_sink_cannot_change_action_or_corpus_result(telemetry):
    app = configure(telemetry, {"sink": {"adapter": MODULE + ":operational_sink", "options": {"fails": True}}})
    assert operational_status(app)["status"] == "ready", "CUSTOM_SINK_FAILURE_MUST_BE_TESTED_AFTER_CONSTRUCTION"
    work = app.call("create_work", {"goal": "work with failed optional export"})["work"]
    wait_for(lambda: operational_status(app)["dispatch"]["pending"] == 0)
    assert app.call("get_work", {"work_id": work["id"]})["work"]["id"] == work["id"]
    assert operational_status(app)["export_failures"] > 0
    assert app.call("telemetry_status", {})["event_count"] >= 2


def test_blocked_custom_sink_keeps_bounded_admission_and_action_progress(telemetry):
    app = configure(telemetry, {"sink": {"adapter": MODULE + ":operational_sink", "options": {"blocked": True}},
                                "max_pending": 1, "max_bytes": 4096})
    assert operational_status(app)["status"] == "ready", "CUSTOM_SINK_PRESSURE_MUST_BE_TESTED_AFTER_CONSTRUCTION"
    sink, = SelectedOperationalSink.instances
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            future = pool.submit(app.call, "create_work", {"goal": "action must outlive optional exporter pressure"})
            wait_for(sink.entered.is_set, message="configured blocking sink never received a signal")
            wait_for(future.done, message="blocked optional sink held the application action")
            assert future.result()["work"]["id"]
            status = app.call("telemetry_status", {})
            assert status["event_count"] == 2
            dispatch = status["operational"]["dispatch"]
            assert dispatch["pending"] == dispatch["capacity"] == 1
            assert dispatch["max_bytes"] == 4096 and dispatch["pending_bytes"] <= 4096
            assert dispatch["rejected"] >= 1
        finally:
            sink.release.set()


def test_operational_sink_kind_is_nested_and_cannot_be_a_project_section(tmp_path):
    with pytest.raises(OrchestrationError) as refused:
        build({"store": {"adapter": "memory"}, "telemetry_operational_sink": {"adapter": "local"}}, tmp_path)
    assert refused.value.code == "configuration_error"
    assert refused.value.details["sections"] == ["telemetry_operational_sink"]
