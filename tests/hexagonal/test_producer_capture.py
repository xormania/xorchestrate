"""Independent capture assertions across real HTTP and subprocess boundaries."""

import http.client
import json
import os
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

from tests.hexagonal.contracts.producer_capture import (
    assert_attempt_links,
    assert_validation,
    events_of,
    require_event,
)
from tests.hexagonal.support.producer_capture import (
    HttpFixture,
    Recorder,
    assert_protected,
    bind_if_supported,
    evaluator,
    failing_observer,
    one_shot,
    process_options,
    response_body,
    retained_bytes,
    rpc,
)
from xorchestrate.adapters.capabilities import CLIProvider
from xorchestrate.adapters.evaluation import ToolEvaluator
from xorchestrate.model import OrchestrationError


class EvaluatorProducerCaptureTests(unittest.TestCase):
    def exercise_configured_tool_secret(self, mode):
        secret = "configured-provider-private-material"
        recorder = Recorder()
        provider = CLIProvider({**process_options(mode, env={"API_KEY": secret}),
            "capabilities": [{"name": "assess", "input_schema": {"type": "object"}}]})
        bind_if_supported(provider, recorder)
        adapter = bind_if_supported(ToolEvaluator({"provider": "source", "capability": "assess"}, {"source": provider}), recorder)
        try:
            if mode == "assessment_secret":
                answer = adapter.assess("Inspect", {})
                self.assertEqual(answer.details["private"], secret, "capture must preserve the original assessment")
                self.assertEqual(answer.details["tokens"], {"input_tokens": 0})
            else:
                with self.assertRaises(OrchestrationError) as caught:
                    adapter.assess("Inspect", {})
                self.assertEqual(caught.exception.code, "evaluation_invalid")
        finally:
            adapter.close()
            provider.close()
        require_event(recorder.snapshot(), "evaluator.result")
        assert_protected(recorder.snapshot(), secret)
        semantic = require_event(recorder.snapshot(), "evaluator.result")["payload"]["result"]
        tokens = semantic.get("details", semantic)["tokens"]
        self.assertEqual(tokens, {"input_tokens": 0})

    def test_tool_evaluator_inherits_configured_provider_secret_protection(self):
        self.exercise_configured_tool_secret("assessment_secret")

    def test_malformed_tool_assessment_keeps_provider_secret_protected(self):
        self.exercise_configured_tool_secret("secret")

    def test_safe_wire_bytes_and_zero_usage_survive_success(self):
        body = response_body()
        recorder = Recorder()
        with HttpFixture(body) as fixture:
            answer = evaluator(fixture.options(), recorder).assess("Inspect λ", {"work_id": "work-one"})
        self.assertEqual(answer.outcome, "ready")
        self.assertEqual(answer.details["usage"], {"input_tokens": 0, "output_tokens": 2})
        events = recorder.snapshot()
        self.assertEqual(retained_bytes(require_event(events, "evaluator.request")), fixture.requests[0][1])
        self.assertEqual(retained_bytes(require_event(events, "evaluator.response")), body)
        self.assertEqual(require_event(events, "evaluator.response")["completeness"], "complete")
        assert_validation(events, "valid")
        assert_attempt_links(events, ("evaluator.request", "evaluator.response", "evaluator.validation"))

    def test_absent_usage_stays_absent_in_source_evidence(self):
        recorder = Recorder()
        with HttpFixture(response_body(usage=False)) as fixture:
            answer = evaluator(fixture.options(), recorder).assess("Inspect", {})
        self.assertNotIn("usage", answer.details)
        retained = json.loads(retained_bytes(require_event(recorder.snapshot(), "evaluator.response")))
        self.assertNotIn("usage", retained)

    def test_invalid_json_retains_original_bounded_body(self):
        recorder = Recorder()
        with HttpFixture(b"{malformed-json\n") as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")
        self.assertEqual(retained_bytes(require_event(recorder.snapshot(), "evaluator.response")), b"{malformed-json\n")
        assert_validation(recorder.snapshot(), "invalid", "evaluation_invalid")
        self.assertEqual(len(fixture.requests), 1)

    def test_invalid_schema_retains_payload_before_rejection(self):
        body = b'{"model":"fixture-model","answers":{"unexpected":{}}}'
        recorder = Recorder()
        with HttpFixture(body) as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")
        self.assertEqual(retained_bytes(require_event(recorder.snapshot(), "evaluator.response")), body)
        assert_validation(recorder.snapshot(), "invalid", "evaluation_invalid")

    def test_http_error_retains_safe_body_and_original_status(self):
        recorder = Recorder()
        with HttpFixture(b'{"error":"fixture-rate-limit"}', status=429) as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        self.assertEqual(caught.exception.details["status"], 429)
        event = require_event(recorder.snapshot(), "evaluator.response")
        self.assertEqual(retained_bytes(event), b'{"error":"fixture-rate-limit"}')
        self.assertEqual(event["payload"]["http_status"], 429)
        assert_validation(recorder.snapshot(), "unavailable", "evaluation_unavailable")

    def test_disabled_capture_does_not_read_known_http_failure_body(self):
        class Body:
            reads = 0

            def read(self, count=-1):
                self.reads += 1
                return b"diagnostic" if self.reads == 1 else b""

            read1 = read

            def close(self):
                pass

        body = Body()
        failure = urllib.error.HTTPError("http://127.0.0.1/evaluate", 429, "fixture rate limit",
                                         {"Content-Length": "10"}, body)

        class Opener:
            def open(self, *args, **kwargs):
                raise failure

        with HttpFixture(b"") as fixture, patch("urllib.request.build_opener", return_value=Opener()):
            adapter = evaluator(fixture.options(), None)
            try:
                with self.assertRaises(OrchestrationError) as caught:
                    adapter.assess("Inspect", {})
            finally:
                adapter.close()
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        self.assertEqual(caught.exception.details["status"], 429)
        self.assertEqual(body.reads, 0, "disabled capture must return the known HTTP failure without diagnostic-body reads")

    def test_http_error_trickle_diagnostics_cannot_extend_failure_deadline(self):
        recorder = Recorder()
        body = b"failure-detail " * 3
        with HttpFixture(body, status=429, chunk_delay=.025) as fixture:
            adapter = evaluator(fixture.options(timeout=.08), recorder)
            try:
                started = time.monotonic()
                with self.assertRaises(OrchestrationError) as caught:
                    adapter.assess("Inspect", {})
                elapsed = time.monotonic() - started
            finally:
                adapter.close()
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        self.assertEqual(caught.exception.details["status"], 429)
        self.assertLess(elapsed, .5,
                        "a trickle faster than each socket timeout must not keep a known HTTP failure alive for its entire body")
        self.assertEqual(len(fixture.requests), 1, "diagnostic capture must not retry the failed evaluation")
        event = require_event(recorder.snapshot(), "evaluator.response")
        retained = retained_bytes(event)
        self.assertTrue(retained, "available diagnostic bytes should be retained within the original deadline")
        self.assertTrue(body.startswith(retained))
        self.assertLess(len(retained), len(body))
        self.assertEqual(event["completeness"], "partial")
        self.assertEqual(event["payload"]["total_bytes"], len(body))
        self.assertEqual(event["payload"]["http_status"], 429)

    def test_oversize_response_is_partial_with_exact_observed_prefix(self):
        recorder = Recorder()
        body = b"0123456789" * 10
        with HttpFixture(body) as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(max_response_bytes=12), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")
        event = require_event(recorder.snapshot(), "evaluator.response")
        self.assertEqual(event["completeness"], "partial")
        self.assertEqual(retained_bytes(event), body[:13])
        self.assertEqual(event["payload"]["range_end"], 13)
        self.assertEqual(event["payload"]["total_bytes"], len(body))

    def test_timeout_retains_request_and_visible_response_gap_without_retry(self):
        recorder = Recorder()
        with HttpFixture(response_body(), delay=.2) as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(timeout=.05), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        require_event(recorder.snapshot(), "evaluator.request")
        event = require_event(recorder.snapshot(), "evaluator.response")
        self.assertIn(event["completeness"], {"partial", "unavailable"})
        self.assertTrue(event.get("reason"))
        self.assertEqual(len(fixture.requests), 1)

    def test_incomplete_read_keeps_observed_partial_bytes(self):
        recorder = Recorder()
        partial = b'{"unfinished":'

        class BrokenResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                pass

            def read1(self, count):
                raise http.client.IncompleteRead(partial, 100)

        class Opener:
            def open(self, *args, **kwargs):
                return BrokenResponse()

        with HttpFixture(b"") as fixture, patch("urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        event = require_event(recorder.snapshot(), "evaluator.response")
        self.assertEqual(event["completeness"], "partial")
        self.assertEqual(retained_bytes(event), partial)
        self.assertIsNone(event["payload"]["total_bytes"])

    def test_repeated_provider_calls_have_distinct_attempts(self):
        recorder = Recorder()
        with HttpFixture(response_body()) as fixture:
            adapter = evaluator(fixture.options(), recorder)
            adapter.assess("Inspect", {})
            adapter.assess("Inspect", {})
        requests = events_of(recorder.snapshot(), "evaluator.request")
        self.assertEqual(len(requests), 2)
        self.assertEqual(len({event["context"].get("attempt_id") for event in requests}), 2)
        self.assertEqual(len(fixture.requests), 2)

    def test_credential_in_body_is_removed_before_any_callback(self):
        secret = "fixture-credential-never-retain"
        body = json.dumps({"model": secret, "usage": {"input_tokens": 0, "output_tokens": 2}}).encode()
        recorder = Recorder()
        with patch.dict(os.environ, {"XOR_CAPTURE_SECRET": secret}), HttpFixture(body) as fixture:
            with self.assertRaises(OrchestrationError):
                evaluator(fixture.options(credential_env="XOR_CAPTURE_SECRET"), recorder).assess("Inspect", {})
        assert_protected(recorder.snapshot(), secret)
        event = require_event(recorder.snapshot(), "evaluator.response")
        self.assertNotIn(secret.encode(), retained_bytes(event))
        self.assertEqual(json.loads(retained_bytes(event))["usage"]["input_tokens"], 0)

    def test_failing_telemetry_does_not_repeat_or_change_provider_result(self):
        with failing_observer() as (observer, calls), HttpFixture(response_body()) as fixture:
            answer = evaluator(fixture.options(), observer).assess("Inspect", {})
        self.assertEqual(answer.outcome, "ready")
        self.assertEqual(len(fixture.requests), 1)
        self.assertTrue(calls, "producer must attempt telemetry even when it fails")

    def test_known_credential_cut_by_response_budget_is_protected(self):
        secret = "prefix-private-credential-material"
        prefix = b'{"value":"'
        visible_secret = secret[:12].encode()
        body = prefix + secret.encode() + b'"}'
        recorder = Recorder()
        with patch.dict(os.environ, {"XOR_CAPTURE_SECRET": secret}), HttpFixture(body) as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(credential_env="XOR_CAPTURE_SECRET", max_response_bytes=len(prefix) + 11), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")
        response = require_event(recorder.snapshot(), "evaluator.response")
        self.assertIn(response["completeness"], {"partial", "suppressed"})
        payload = response["payload"]
        content = retained_bytes(response) if payload.get("encoding") == "base64" else json.dumps(payload).encode()
        self.assertNotIn(visible_secret, content, "bounded capture exposed a known credential prefix")
        assert_protected(recorder.snapshot(), secret)

    def test_unclosed_credential_key_is_protected_before_parser_failure(self):
        secret = "unclosed-password-value"
        recorder = Recorder()
        with HttpFixture(b'{"password":"' + secret.encode()) as fixture:
            with self.assertRaises(OrchestrationError) as caught:
                evaluator(fixture.options(), recorder).assess("Inspect", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")
        response = require_event(recorder.snapshot(), "evaluator.response")
        self.assertIn(response["completeness"], {"complete", "suppressed"})
        assert_protected(recorder.snapshot(), secret)


class ProcessProducerCaptureTests(unittest.TestCase):
    def test_rejected_rpc_frame_records_bounded_attempt_without_sending_it(self):
        recorder = Recorder()
        peer = rpc(process_options("rpc", max_frame_bytes=128), recorder, {"session_id": "connection-session"})
        origin = {"work_id": "rejected-work", "attempt_id": "application-attempt"}
        try:
            with self.assertRaises(OrchestrationError) as caught:
                peer.request_async("fixture/too-large", {"blob": "x" * 256}, telemetry_context=origin)
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertEqual(caught.exception.details["outcome"], "not_started")
        finally:
            peer.close()
        request = require_event(recorder.snapshot(), "process.request")
        self.assertEqual(request["context"]["work_id"], "rejected-work")
        self.assertEqual(request["context"]["parent_attempt_id"], "application-attempt")
        self.assertEqual(request["completeness"], "partial")
        self.assertLessEqual(len(retained_bytes(request)), 129)
        self.assertGreater(request["payload"]["total_bytes"], 128)
        error = require_event(recorder.snapshot(), "process.error")
        self.assertEqual(error["payload"]["code"], "invalid_request")
        self.assertEqual(error["payload"]["outcome"], "not_started")
        self.assertEqual(error["context"], request["context"])

    def test_rpc_timeout_keeps_the_pending_request_work_and_attempt_binding(self):
        recorder = Recorder()
        peer = rpc(process_options("rpc_hold", timeout=.15), recorder,
                   {"session_id": "connection-session", "work_id": "connection-default"})
        try:
            with self.assertRaises(OrchestrationError) as caught:
                peer.request("fixture/held", {}, telemetry_context={"work_id": "request-work", "attempt_id": "application-attempt"})
            self.assertEqual(caught.exception.code, "provider_timeout")
        finally:
            peer.close()
        request = require_event(recorder.snapshot(), "process.request")
        error = require_event(recorder.snapshot(), "process.error")
        self.assertEqual(request["context"]["work_id"], "request-work")
        self.assertEqual(error["context"]["work_id"], "request-work")
        self.assertEqual(error["context"], request["context"], "terminal failure must retain the originating attempt")

    def test_slow_optional_observer_does_not_hold_the_action_or_spend_its_deadline(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        results, failures = [], []
        calls = []

        def observer(event):
            calls.append(event)
            if len(calls) == 1:
                entered.set()
                release.wait(2)

        def invoke():
            try:
                results.append(one_shot(process_options("success", timeout=.15), {"work": "useful"}, observer))
            except BaseException as error:
                failures.append(error)
            finally:
                completed.set()

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1), "test must observe the optional recording hold")
            self.assertTrue(completed.wait(.5), "slow optional recording held the action past its transport deadline")
            self.assertEqual(failures, [], "optional recording delay changed the source outcome")
            self.assertEqual(results, [{"echo": {"work": "useful"}, "unicode": "λ"}])
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_stderr_saturation_drains_without_corrupting_json_result(self):
        recorder = Recorder()
        started = time.monotonic()
        result = one_shot(process_options("success"), {"value": "λ"}, recorder, {"work_id": "work-one"})
        self.assertEqual(result, {"echo": {"value": "λ"}, "unicode": "λ"})
        self.assertLess(time.monotonic() - started, 3)
        event = require_event(recorder.snapshot(), "process.stderr")
        diagnostic = retained_bytes(event)
        self.assertTrue(diagnostic.startswith(b"diagnostic-start\n"))
        self.assertTrue(diagnostic.endswith(b"\ndiagnostic-end\n"))
        self.assertEqual(len(diagnostic), len(b"diagnostic-start\n") + 131072 + len(b"\ndiagnostic-end\n"))
        assert_attempt_links(recorder.snapshot(), ("process.request", "process.response", "process.exit"))

    def test_binary_stderr_retains_exact_bytes(self):
        recorder = Recorder()
        self.assertEqual(one_shot(process_options("binary"), {}, recorder)["echo"], {})
        event = require_event(recorder.snapshot(), "process.stderr")
        self.assertEqual(retained_bytes(event), b"binary-\xff\x00-diagnostic\n")

    def test_failed_process_keeps_diagnostics_and_exit_code(self):
        recorder = Recorder()
        with self.assertRaises(OrchestrationError) as caught:
            one_shot(process_options("failure"), {}, recorder)
        self.assertEqual(caught.exception.code, "capability_failed")
        self.assertEqual(caught.exception.details["exit_code"], 7)
        require_event(recorder.snapshot(), "process.stderr")
        self.assertEqual(require_event(recorder.snapshot(), "process.exit")["payload"]["exit_code"], 7)

    def test_invalid_stdout_is_retained_before_parser_rejects(self):
        recorder = Recorder()
        with self.assertRaises(OrchestrationError) as caught:
            one_shot(process_options("invalid"), {}, recorder)
        self.assertEqual(caught.exception.code, "provider_protocol")
        self.assertEqual(retained_bytes(require_event(recorder.snapshot(), "process.response")), b"{invalid\n")
        require_event(recorder.snapshot(), "process.stderr")

    def test_process_timeout_keeps_diagnostics_and_returns_on_deadline(self):
        recorder = Recorder()
        started = time.monotonic()
        with self.assertRaises(OrchestrationError) as caught:
            one_shot(process_options("timeout", timeout=.15), {}, recorder)
        self.assertEqual(caught.exception.code, "provider_timeout")
        self.assertLess(time.monotonic() - started, 2)
        require_event(recorder.snapshot(), "process.stderr")
        require_event(recorder.snapshot(), "process.error")

    def test_stderr_bound_reports_partial_and_keeps_transport_result(self):
        recorder = Recorder()
        options = process_options("success", max_stderr_bytes=64)
        self.assertEqual(one_shot(options, {}, recorder)["echo"], {})
        event = require_event(recorder.snapshot(), "process.stderr")
        self.assertEqual(event["completeness"], "partial")
        self.assertEqual(len(retained_bytes(event)), 64)
        self.assertGreater(event["payload"]["total_bytes"], 64)

    def test_persistent_rpc_stderr_drain_preserves_response_correlation(self):
        recorder = Recorder()
        peer = rpc(process_options("rpc"), recorder, {"session_id": "session-one"})
        try:
            self.assertEqual(peer.request("fixture/first", {"ordinal": 1}), {"echo": {"ordinal": 1}})
            self.assertEqual(peer.request("fixture/second", {"ordinal": 2}), {"echo": {"ordinal": 2}})
        finally:
            peer.close()
        event = require_event(recorder.snapshot(), "process.stderr")
        self.assertTrue(retained_bytes(event).endswith(b"diagnostic-end\n"))
        self.assertEqual(len(events_of(recorder.snapshot(), "process.response")), 2)
        require_event(recorder.snapshot(), "process.exit")

    def test_persistent_timeout_retains_gap_and_closes_owned_process(self):
        recorder = Recorder()
        peer = rpc(process_options("rpc_hold", timeout=.15), recorder)
        try:
            with self.assertRaises(OrchestrationError) as caught:
                peer.request("fixture/held", {})
            self.assertEqual(caught.exception.code, "provider_timeout")
        finally:
            peer.close()
        self.assertIsNotNone(peer.process.poll())
        require_event(recorder.snapshot(), "process.stderr")
        require_event(recorder.snapshot(), "process.error")

    def test_observer_failure_preserves_one_shot_result_and_rpc_progress(self):
        with failing_observer() as (observer, calls):
            self.assertEqual(one_shot(process_options("success"), {}, observer)["echo"], {})
            peer = rpc(process_options("rpc"), observer)
            try:
                self.assertEqual(peer.request("fixture/ping", {}), {"echo": {}})
            finally:
                peer.close()
        self.assertTrue(calls, "producer must attempt telemetry even when collector fails")

    def test_known_process_credential_and_usage_are_treated_independently(self):
        secret = "process-private-credential-material"
        recorder = Recorder()
        options = process_options("secret", env={"API_KEY": secret})
        result = one_shot(options, {}, recorder)
        self.assertEqual(result, {"private": secret, "tokens": {"input_tokens": 0}})
        assert_protected(recorder.snapshot(), secret)
        response = json.loads(retained_bytes(require_event(recorder.snapshot(), "process.response")))
        self.assertEqual(response["tokens"], {"input_tokens": 0})
        self.assertNotEqual(response["private"], secret)

    def test_known_credential_cut_by_stderr_budget_is_protected(self):
        secret = "process-private-credential-material"
        prefix = b"diagnostic: "
        recorder = Recorder()
        options = process_options("secret", env={"API_KEY": secret}, max_stderr_bytes=len(prefix) + 12)
        result = one_shot(options, {}, recorder)
        self.assertEqual(result["private"], secret)
        event = require_event(recorder.snapshot(), "process.stderr")
        self.assertIn(event["completeness"], {"partial", "suppressed"})
        payload = event["payload"]
        content = retained_bytes(event) if payload.get("encoding") == "base64" else json.dumps(payload).encode()
        self.assertNotIn(secret[:12].encode(), content, "bounded stderr exposed a known credential prefix")
        assert_protected(recorder.snapshot(), secret)


if __name__ == "__main__":
    unittest.main()
