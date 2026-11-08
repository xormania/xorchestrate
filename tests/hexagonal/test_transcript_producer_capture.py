"""Exact-session transcript ingestion through an active native hook boundary."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.hexagonal.contracts.producer_capture import events_of, require_event
from tests.hexagonal.support.native import native_driver
from tests.hexagonal.support.producer_capture import Recorder, assert_protected, bind_if_supported, retained_bytes
from xorchestrate.model import Capability


class TranscriptProducerCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xor-transcript-capture-")
        self.root = Path(self.temporary.name)
        self.recorder = Recorder()
        self.fail_next_transcript = False

        def observer(event):
            if event["event_type"] == "native.transcript" and self.fail_next_transcript:
                self.fail_next_transcript = False
                raise RuntimeError("synthetic collector rejection")
            self.recorder(event)

        self.driver = native_driver("claude", state=self.root / "state")
        bind_if_supported(self.driver, observer)
        self.session = self.driver.start({"context": {"session_id": "app-session", "work_id": "work-one"}})
        self.live = self.driver._get(self.session)
        self.source = self.root / (self.session["native_id"] + ".jsonl")

    def tearDown(self):
        self.driver.close()
        self.temporary.cleanup()

    def line(self, marker="first", **fields):
        entry = {"sessionId": self.session["native_id"], "type": "assistant", "marker": marker, **fields}
        return (json.dumps(entry, ensure_ascii=False) + "\n").encode()

    def hook(self, source=None):
        result = self.driver._hook(self.live, {"hook_event_name": "SessionStart", "session_id": self.session["native_id"],
            "model": "fixture-model", "transcript_path": str(source or self.source)})
        self.drain()
        return result

    def drain(self):
        drain = getattr(self.driver, "drain_telemetry", None)
        if callable(drain):
            self.assertTrue(drain(timeout=1), "fast test observer must finish its queued source work")

    def poll(self):
        result = self.driver.poll(self.session)
        self.drain()
        return result

    def source_event(self):
        return require_event(self.recorder.snapshot(), "native.transcript_source")

    def transcript_events(self):
        return events_of(self.recorder.snapshot(), "native.transcript")

    def test_hook_selected_exact_session_retains_bytes_and_source_position(self):
        content = self.line("λ", usage={"input_tokens": 0, "output_tokens": 2})
        self.source.write_bytes(content)
        self.hook()
        event = require_event(self.recorder.snapshot(), "native.transcript")
        self.assertEqual(retained_bytes(event), content)
        self.assertEqual(event["payload"]["entry"]["usage"], {"input_tokens": 0, "output_tokens": 2})
        self.assertEqual(event["payload"]["range_start"], 0)
        self.assertEqual(event["payload"]["range_end"], len(content))
        self.assertTrue(event["payload"]["source_id"])
        self.assertTrue(event["payload"]["source_version"])
        self.assertEqual(event["context"]["session_id"], "app-session")
        self.assertEqual(self.source_event()["payload"]["cursor"], len(content))
        self.poll()
        self.assertEqual(len(self.transcript_events()), 1, "poll must not duplicate accepted source records")

    def test_partial_tail_waits_without_advancing_and_is_resumed_once(self):
        first, second = self.line("first"), self.line("second")
        self.source.write_bytes(first + second[:len(second) // 2])
        self.hook()
        self.assertEqual(len(self.transcript_events()), 1)
        status = self.source_event()["payload"]
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["cursor"], len(first))
        self.source.write_bytes(first + second)
        self.poll()
        events = self.transcript_events()
        self.assertEqual([event["payload"]["entry"]["marker"] for event in events], ["first", "second"])
        self.assertEqual(events[1]["payload"]["range_start"], len(first))
        self.assertEqual(self.source_event()["payload"]["cursor"], len(first + second))

    def test_wrong_session_filename_is_not_read(self):
        other = self.root / "another-session.jsonl"
        other.write_bytes(self.line("must-not-capture"))
        self.hook(other)
        self.assertEqual(self.transcript_events(), [])
        self.assertEqual(self.source_event()["payload"]["status"], "rejected")
        self.assertEqual(self.source_event()["payload"]["cursor"], 0)

    def test_wrong_session_record_does_not_advance_cursor(self):
        self.source.write_text(json.dumps({"sessionId": "another-conversation", "marker": "must-not-capture"}) + "\n")
        self.hook()
        self.assertEqual(self.transcript_events(), [])
        self.assertEqual(self.source_event()["payload"]["status"], "rejected")
        self.assertEqual(self.source_event()["payload"]["cursor"], 0)

    def test_symlink_selected_by_hook_is_not_followed(self):
        target = self.root / "private-target.jsonl"
        target.write_bytes(self.line("must-not-capture"))
        self.source.symlink_to(target)
        self.hook()
        self.assertEqual(self.transcript_events(), [])
        self.assertIn(self.source_event()["payload"]["status"], {"rejected", "unavailable"})
        self.assertEqual(self.source_event()["payload"]["cursor"], 0)

    def test_missing_selected_source_is_visible_and_later_recoverable(self):
        self.hook()
        self.assertEqual(self.transcript_events(), [])
        self.assertEqual(self.source_event()["payload"]["status"], "unavailable")
        self.assertEqual(self.source_event()["payload"]["cursor"], 0)
        self.source.write_bytes(self.line("recovered"))
        self.poll()
        self.assertEqual(require_event(self.recorder.snapshot(), "native.transcript")["payload"]["entry"]["marker"], "recovered")

    def test_capture_rejection_retries_record_without_advancing_cursor(self):
        content = self.line("retry")
        self.source.write_bytes(content)
        self.fail_next_transcript = True
        self.hook()
        self.assertEqual(self.transcript_events(), [])
        self.assertEqual(self.source_event()["payload"]["cursor"], 0)
        self.poll()
        self.assertEqual(len(self.transcript_events()), 1)
        self.assertEqual(self.source_event()["payload"]["cursor"], len(content))

    def test_source_rotation_has_new_identity_and_explicit_gap(self):
        self.source.write_bytes(self.line("old-source"))
        self.hook()
        first = require_event(self.recorder.snapshot(), "native.transcript")
        self.source.rename(self.root / "rotated.jsonl")
        self.source.write_bytes(self.line("new-source"))
        self.poll()
        events = self.transcript_events()
        self.assertEqual(len(events), 2)
        self.assertNotEqual(first["payload"]["source_id"], events[1]["payload"]["source_id"])
        self.assertEqual(events[1]["payload"]["range_start"], 0)
        statuses = events_of(self.recorder.snapshot(), "native.transcript_source")
        self.assertTrue(any(event["payload"]["status"] == "gap" for event in statuses), "rotation must not silently reuse an unrelated source cursor")

    def test_same_inode_equal_size_rewrite_is_not_silently_treated_as_current(self):
        self.assertEqual(len(self.line("old-marker")), len(self.line("new-marker")))
        self.assert_source_rewrite_is_detected("new-marker")

    def test_same_inode_longer_rewrite_does_not_reuse_an_unrelated_offset(self):
        marker = "new-marker-with-longer-content"
        self.assertGreater(len(self.line(marker)), len(self.line("old-marker")))
        self.assert_source_rewrite_is_detected(marker)

    def test_valid_append_verifies_consumed_prefix_across_bounded_passes(self):
        lines = [self.line(marker) for marker in ("one", "two", "three")]
        self.driver.max_transcript_bytes = max(map(len, lines)) + 8
        initial = b"".join(lines)
        self.source.write_bytes(initial)
        self.hook()
        for _ in range(10):
            if self.source_event()["payload"]["cursor"] == len(initial):
                break
            self.poll()
        self.assertEqual(self.source_event()["payload"]["cursor"], len(initial))
        first_source = self.transcript_events()[0]["payload"]["source_id"]
        appended = self.line("four")
        with self.source.open("ab") as stream:
            stream.write(appended)
        self.poll()
        status = self.source_event()["payload"]
        self.assertEqual(status["cursor"], len(initial), "cursor advanced before the changed source prefix could be verified")
        self.assertEqual(status["reason"], "prefix_verification_pending")
        for _ in range(10):
            if self.source_event()["payload"]["cursor"] == len(initial + appended):
                break
            self.poll()
        self.assertEqual(self.source_event()["payload"]["cursor"], len(initial + appended))
        events = self.transcript_events()
        self.assertEqual([event["payload"]["entry"]["marker"] for event in events], ["one", "two", "three", "four"])
        self.assertEqual({event["payload"]["source_id"] for event in events}, {first_source})
        self.assertFalse(any(event["payload"]["status"] == "gap"
                             for event in events_of(self.recorder.snapshot(), "native.transcript_source")),
                         "a verified ordinary append must not invent a source-generation gap")

    def test_consumed_prefix_verification_progresses_while_each_poll_has_an_append(self):
        initial = b"".join(self.line("initial-%02d" % index) for index in range(6))
        self.source.write_bytes(initial)
        self.hook()
        first_source = self.transcript_events()[0]["payload"]["source_id"]
        self.assertEqual(self.source_event()["payload"]["cursor"], len(initial))
        self.driver.max_transcript_bytes = len(self.line("appended-00")) * 2 + 8
        self.assertGreater(len(initial), self.driver.max_transcript_bytes * 2)
        for index in range(12):
            with self.source.open("ab") as stream:
                stream.write(self.line("appended-%02d" % index))
            self.poll()
        events = self.transcript_events()
        markers = [event["payload"]["entry"]["marker"] for event in events]
        self.assertEqual(markers.count("appended-00"), 1,
                         "ordinary append before every bounded pass must not starve ingestion behind a fixed consumed prefix")
        self.assertEqual({event["payload"]["source_id"] for event in events}, {first_source})
        self.assertFalse(any(event["payload"]["status"] == "gap"
                             for event in events_of(self.recorder.snapshot(), "native.transcript_source")),
                         "continuous ordinary appends must not be misreported as a replacement generation")
        self.assertTrue(any(event["payload"].get("continuity") == "unconfirmed_during_growth"
                            for event in events if event["payload"]["entry"]["marker"].startswith("appended-")),
                        "bounded progress during growth must retain its non-atomic continuity qualification")
        prior_statuses = len(events_of(self.recorder.snapshot(), "native.transcript_source"))
        # Finish the in-flight growing-source pass, then allow a fresh quiet
        # recheck of the now-fixed source, plus bounded tail admission.
        quiet_passes = 2 * (self.source.stat().st_size // self.driver.max_transcript_bytes + 2) + 2
        for _ in range(quiet_passes):
            self.poll()
        quiet_statuses = events_of(self.recorder.snapshot(), "native.transcript_source")[prior_statuses:]
        self.assertTrue(any(event["payload"].get("continuity") == "verified_prefix" for event in quiet_statuses),
                        "stopped writes must permit the required complete prefix recheck to clear growth uncertainty")
        self.assertFalse(any(event["payload"]["status"] == "gap" for event in quiet_statuses))

    def test_equal_size_midprefix_rewrite_during_verification_is_detected_despite_appends(self):
        self.assert_in_progress_prefix_rewrite_detected("changed-01")

    def test_longer_midprefix_rewrite_during_verification_is_detected_despite_appends(self):
        self.assert_in_progress_prefix_rewrite_detected("changed-01-with-extra-content")

    def assert_in_progress_prefix_rewrite_detected(self, replacement_marker):
        lines = [self.line("initial-%02d" % index) for index in range(6)]
        initial = b"".join(lines)
        self.source.write_bytes(initial)
        inode = self.source.stat().st_ino
        self.hook()
        original_source = self.transcript_events()[0]["payload"]["source_id"]
        self.driver.max_transcript_bytes = max(map(len, lines)) * 3 + 16
        appended = self.line("tail-before-rewrite")
        with self.source.open("ab") as stream:
            stream.write(appended)
        self.poll()
        self.assertEqual(self.source_event()["payload"]["cursor"], len(initial))
        self.assertEqual(self.source_event()["payload"]["reason"], "prefix_verification_pending")
        # Rewrite an already checked middle record while retaining the inode.
        # Equal-length changes must not hide behind a carried partial hash;
        # longer changes must not splice the old byte offset into a new record.
        lines[1] = self.line(replacement_marker)
        self.source.write_bytes(b"".join(lines) + appended)
        self.assertEqual(self.source.stat().st_ino, inode)
        for index in range(16):
            with self.source.open("ab") as stream:
                stream.write(self.line("tail-after-rewrite-%02d" % index))
            self.poll()
        replacement_events = [event for event in self.transcript_events()
                              if event["payload"]["source_id"] != original_source]
        self.assertTrue(replacement_events,
                        "a rewritten consumed prefix must establish a new generation even while the file keeps growing")
        self.assertEqual(replacement_events[0]["payload"]["range_start"], 0)
        markers = [event["payload"].get("entry", {}).get("marker") for event in replacement_events]
        self.assertIn(replacement_marker, markers, "replacement records must be reread from their own generation")
        self.assertTrue(any(event["payload"]["status"] == "gap"
                            for event in events_of(self.recorder.snapshot(), "native.transcript_source")))

    def assert_source_rewrite_is_detected(self, marker):
        original, replacement = self.line("old-marker"), self.line(marker)
        self.source.write_bytes(original)
        inode = self.source.stat().st_ino
        self.hook()
        first = require_event(self.recorder.snapshot(), "native.transcript")
        self.source.write_bytes(replacement)
        self.assertEqual(self.source.stat().st_ino, inode, "fixture must replace content without rotating the inode")
        self.poll()
        events = self.transcript_events()
        self.assertEqual(len(events), 2, "replacement content was skipped or split using the prior source cursor")
        self.assertIsInstance(events[1]["payload"].get("entry"), dict,
                              "prior source offset split a replacement record into an invalid fragment")
        self.assertEqual(events[1]["payload"]["entry"]["marker"], marker)
        self.assertNotEqual(first["payload"]["source_id"], events[1]["payload"]["source_id"])
        self.assertTrue(any(event["payload"]["status"] == "gap"
                            for event in events_of(self.recorder.snapshot(), "native.transcript_source")))

    def test_transcript_password_filtered_without_rewriting_source_or_usage(self):
        secret = "transcript-synthetic-private-value"
        original = self.line("safe", password=secret, tokens={"input_tokens": 0, "output_tokens": 2})
        self.source.write_bytes(original)
        self.hook()
        event = require_event(self.recorder.snapshot(), "native.transcript")
        self.assertEqual(event["payload"]["entry"]["tokens"], {"input_tokens": 0, "output_tokens": 2})
        assert_protected(self.recorder.snapshot(), secret)
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()


class TranscriptApplicationToolResultTests(unittest.TestCase):
    """Outer-session transcript capture cannot carry nested application results."""

    secret = "NESTED-TRANSCRIPT-TOOL-RESULT-CANARY"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xor-transcript-tools-")
        self.root = Path(self.temporary.name)
        self.recorder = Recorder()
        self.driver = native_driver("claude", state=self.root / "state")
        bind_if_supported(self.driver, self.recorder)
        self.driver.bind_application_tools([Capability("get_work", "Read work", {"type": "object"})],
                                           lambda *args: {})
        self.session = self.driver.start({"context": {"session_id": "app-session", "work_id": "work-one"}})
        self.live = self.driver._get(self.session)
        self.source = self.root / (self.session["native_id"] + ".jsonl")

    def tearDown(self):
        self.driver.close()
        self.temporary.cleanup()

    def record(self, kind, *blocks, **fields):
        entry = {"sessionId": self.session["native_id"], "type": kind,
                 "message": {"role": kind, "content": list(blocks)}, **fields}
        return (json.dumps(entry) + "\n").encode()

    def capture(self, content):
        self.source.write_bytes(content)
        self.driver._hook(self.live, {"hook_event_name": "SessionStart", "session_id": self.session["native_id"],
            "model": "fixture-model", "transcript_path": str(self.source)})
        self.assertTrue(self.driver.drain_telemetry(timeout=1))
        return events_of(self.recorder.snapshot(), "native.transcript")

    def result(self, call_id, text):
        return {"type": "tool_result", "tool_use_id": call_id, "content": [{"type": "text", "text": text}]}

    def test_application_and_unattributed_tool_results_are_explicit_gaps(self):
        builtin = "ordinary builtin tool output"
        lines = [
            self.record("assistant", {"type": "tool_use", "id": "app-call", "name": "mcp__xorchestrate__get_work",
                                      "input": {"work_id": "private"}}),
            self.record("user", self.result("app-call", self.secret), toolUseResult={"goal": self.secret}),
            self.record("user", self.result("never-observed", self.secret)),
            self.record("assistant", {"type": "tool_use", "id": "builtin-call", "name": "Bash", "input": {}}),
            self.record("user", self.result("builtin-call", builtin)),
        ]
        events = self.capture(b"".join(lines))
        self.assertEqual(len(events), len(lines))
        self.assertNotIn(self.secret, json.dumps(events), "TRANSCRIPT_TOOL_RESULT_MUST_NOT_BYPASS_NESTED_AUTHORIZATION")
        suppressed = [event for event in events if event["payload"].get("representation") == "suppressed"]
        self.assertEqual([item["classification"] for event in suppressed for item in event["payload"]["tool_results"]],
                         ["application_tool", "unattributed"])
        for event in suppressed:
            self.assertEqual(event["completeness"], "suppressed")
            self.assertEqual(event["reason"], "callback_response_requires_typed_observation")
            self.assertEqual(event["payload"]["captured_bytes"], 0)
            self.assertFalse({"content", "encoding", "entry"} & event["payload"].keys())
        self.assertEqual([event["payload"]["source_bytes"] for event in suppressed], [len(lines[1]), len(lines[2])])
        retained = [retained_bytes(event) for event in events if event["payload"].get("encoding") == "base64"]
        self.assertIn(lines[4], retained, "Non-application tool results keep ordinary protected capture")
        self.assertEqual(require_event(self.recorder.snapshot(), "native.transcript_source")["payload"]["cursor"],
                         sum(map(len, lines)), "Suppressed records still advance the committed source cursor")
