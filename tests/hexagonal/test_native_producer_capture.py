"""Capture exposed native evidence without replacing the required observer."""

import copy
import tempfile
import unittest
from pathlib import Path

from tests.hexagonal.contracts.producer_capture import require_event
from tests.hexagonal.support.native import application_message, native_driver, wait_for
from tests.hexagonal.support.producer_capture import Recorder, assert_protected, bind_if_supported, failing_observer
from xorchestrate.model import Capability


class NativeProducerCaptureTests(unittest.TestCase):
    def exercise_native(self, protocol, observer=None):
        recorder = Recorder()
        required, calls = [], []
        with tempfile.TemporaryDirectory(prefix="xor-native-capture-") as directory:
            adapter = native_driver(protocol, state=Path(directory), callback_timeout=5, turn_timeout=5)
            bind_if_supported(adapter, observer or recorder)
            adapter.bind_observer(lambda context, event: required.append((copy.deepcopy(context), copy.deepcopy(event))))
            adapter.bind_application_tools([Capability("echo", "Return supplied data", {"type": "object"})],
                lambda action, args, context, key: calls.append((action, copy.deepcopy(args), copy.deepcopy(context))) or {"ok": True})
            context = {"actor": "worker", "session_id": "app-session", "work_id": "work-original"}
            try:
                session = adapter.start({"context": context})
                context["work_id"] = "work-later"
                args = {"tokens": {"input_tokens": 0, "output_tokens": 2}, "password": "synthetic-private-value"}
                adapter.send(session, application_message(protocol, "echo", args))
                wait_for(lambda: calls, timeout=4)
                done_kind = "turn_completed" if protocol == "claude" else "native_completion"
                wait_for(lambda: any(event["kind"] == done_kind for _, event in required), timeout=4)
            finally:
                adapter.close()
        return recorder.snapshot(), required, calls, session

    def assert_native_capture(self, protocol):
        events, required, calls, session = self.exercise_native(protocol)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["tokens"]["input_tokens"], 0)
        event_type = "native.native_tool" if protocol == "claude" else "native.native_tool_call"
        observed = require_event(events, event_type)
        self.assertEqual(observed["context"]["work_id"], "work-original")
        self.assertEqual(observed["context"]["session_id"], "app-session")
        self.assertEqual(observed["payload"]["native_id"], session["native_id"])
        self.assertEqual(observed["payload"]["incarnation"], session["incarnation"])
        self.assertEqual(observed["payload"]["arguments"]["tokens"], {"input_tokens": 0, "output_tokens": 2})
        assert_protected(events, "synthetic-private-value")
        original = next(event for _, event in required if event["kind"] == event_type.removeprefix("native."))
        self.assertEqual(original["arguments"]["password"], "synthetic-private-value", "optional capture must not rewrite required observer data")

    def test_codex_semantic_observations_keep_identity_context_and_usage(self):
        self.assert_native_capture("codex")

    def test_grok_semantic_observations_keep_identity_context_and_usage(self):
        self.assert_native_capture("grok")

    def test_claude_hook_observations_keep_identity_context_and_usage(self):
        self.assert_native_capture("claude")

    def test_optional_native_observer_failure_leaves_required_evidence_and_calls_intact(self):
        with failing_observer() as (observer, attempts):
            _, required, calls, _ = self.exercise_native("codex", observer)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(event["kind"] == "native_completion" for _, event in required))
        self.assertTrue(attempts, "optional native recording must be attempted")

    def test_required_native_observer_exception_is_not_treated_as_optional(self):
        adapter = native_driver("codex")
        expected = RuntimeError("required evidence failure")

        def required(context, event):
            raise expected

        adapter.bind_observer(required)
        bind_if_supported(adapter, lambda event: None)
        try:
            with self.assertRaises(RuntimeError) as caught:
                adapter.start({})
            self.assertIs(caught.exception, expected)
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
