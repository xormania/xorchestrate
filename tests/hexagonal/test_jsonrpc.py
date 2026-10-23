"""Real subprocess regressions for incomplete exchanges and callback failure."""

import json
import sys
import time
from pathlib import Path

import pytest

from xorchestrate.adapters.jsonrpc import JsonLineProcess, run_json_process
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError

FIXTURE = Path(__file__).parent / "fixtures" / "transport_failure_server.py"
PAYLOAD = {"value": "x" * 2_000_000}


def options(mode, marker, **overrides):
    return {
        "command": [sys.executable, str(FIXTURE), mode, "--marker", str(marker)],
        "timeout": 3,
        "shutdown_timeout": 0.01,
        **overrides,
    }


def records(marker):
    return [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]


def test_cli_result_requires_complete_request_delivery(tmp_path):
    marker = tmp_path / "peer.jsonl"
    with pytest.raises(OrchestrationError) as caught:
        run_json_process(options("unread_input", marker), PAYLOAD)
    assert caught.value.code == "provider_unavailable"
    assert caught.value.details["outcome"] == "unknown"
    assert records(marker) == [{"event": "started"}, {"event": "stdout_closed"}]


def test_early_stdout_close_can_precede_successful_input_delivery(tmp_path):
    marker = tmp_path / "peer.jsonl"
    assert run_json_process(options("late_input", marker), PAYLOAD) == {"ok": True}
    assert records(marker)[-1] == {"event": "input_received", "value_length": len(PAYLOAD["value"])}


def test_blocked_input_uses_the_remaining_execution_deadline(tmp_path):
    marker = tmp_path / "peer.jsonl"
    with pytest.raises(OrchestrationError) as caught:
        run_json_process(options("input_deadline", marker, timeout=2), PAYLOAD)
    assert caught.value.code == "provider_timeout"
    assert caught.value.details["outcome"] == "unknown"
    # Restarting the timeout after stdout EOF lets the peer reach this marker.
    assert records(marker) == [{"event": "started"}, {"event": "stdout_closed"}]


def test_output_failure_is_not_replaced_by_a_blocked_writer_timeout(tmp_path):
    marker = tmp_path / "peer.jsonl"
    with pytest.raises(OrchestrationError) as caught:
        run_json_process(options("output_overflow", marker, max_output_bytes=128), PAYLOAD)
    assert caught.value.code == "provider_protocol"
    assert caught.value.details["outcome"] == "unknown"


def test_incomplete_cli_delivery_stays_unknown_and_is_never_reexecuted(tmp_path):
    marker = tmp_path / "peer.jsonl"
    config = {
        "store": {"adapter": "sqlite", "options": {"path": str(tmp_path / "state.sqlite")}},
        "providers": {"peer": {"adapter": "cli", "options": {
            **options("unread_input", marker),
            "capabilities": [{"name": "effect", "input_schema": {"type": "object"}}],
        }}},
    }
    arguments = {"capability": "peer/effect", "arguments": PAYLOAD, "idempotency_key": "one-effect"}
    with build(config) as runtime:
        with pytest.raises(OrchestrationError) as caught:
            runtime.application.call("invoke", arguments)
        assert caught.value.code == "operation_unknown"
        operation_id = caught.value.details["operation_id"]
    with build(config) as runtime:
        operation = runtime.application.call("get_operation", {"operation_id": operation_id})["operation"]
        assert operation["status"] == "unknown"
        with pytest.raises(OrchestrationError) as replay:
            runtime.application.call("invoke", arguments)
        assert replay.value.code == "operation_unresolved"
    assert records(marker) == [{"event": "started"}, {"event": "stdout_closed"}]


@pytest.mark.parametrize("mode", ["request_callback", "poll_callback"])
@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_callback_failure_closes_and_reaps_the_owned_connection(tmp_path, mode, exception_type):
    failure = exception_type("private callback detail")
    calls = []

    def handler(method, params):
        calls.append((method, params))
        raise failure

    transport = JsonLineProcess(options(mode, tmp_path / "peer.jsonl"), request_handler=handler)
    expected = OrchestrationError if exception_type is RuntimeError else exception_type
    try:
        with pytest.raises(expected) as caught:
            if mode == "request_callback":
                transport.request("fixture/outer")
            else:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    transport.poll_notifications()
                    time.sleep(0.01)
                pytest.fail("The peer callback was not received before the deadline.")
        if exception_type is RuntimeError:
            assert caught.value.code == "provider_unavailable"
            assert caught.value.details == {"outcome": "unknown"}
            assert "private callback detail" not in str(caught.value)
        else:
            assert caught.value is failure
        assert calls == [("fixture/callback", {})]
        assert transport._closed
        assert transport.process.poll() is not None
        assert not transport._reader.is_alive()
        with pytest.raises(OrchestrationError) as subsequent:
            transport.request("ping")
        assert subsequent.value.code == "provider_unavailable"
        assert subsequent.value.details["outcome"] == "not_started"
    finally:
        transport.close()
