"""Exact-session transcript ingestion through an active native hook boundary."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.hexagonal.contracts.producer_capture import assert_transcript_gap, events_of, require_event
from tests.hexagonal.support.native import native_driver
from tests.hexagonal.support.producer_capture import Recorder, assert_protected, bind_if_supported, retained_bytes
from tests.hexagonal.support.transcript_privacy import TranscriptPrivacyFixture
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

    def test_unbound_partial_historical_tool_result_keeps_gap_until_complete(self):
        private = "HISTORICAL-UNBOUND-PARTIAL-TOOL-RESULT-CANARY"
        original = self.line("historical result", type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "historical-never-observed",
             "content": [{"type": "text", "text": private}]}]})
        self.source.write_bytes(original[:-1])
        self.hook()
        tail = require_event(self.recorder.snapshot(), "native.transcript_tail")
        assert_protected(self.recorder.snapshot(), private)
        assert_transcript_gap(tail, source_bytes=len(original) - 1, range_start=0,
                              total_bytes=len(original) - 1, parser_status="incomplete")
        self.assertEqual(self.source_event()["payload"]["cursor"], 0)
        with self.source.open("ab") as stream:
            stream.write(b"\n")
        self.poll()
        assert_protected(self.recorder.snapshot(), private)
        self.assertEqual(self.transcript_events()[0]["completeness"], "suppressed")
        self.assertEqual(self.source_event()["payload"]["cursor"], len(original))

    def test_complete_ordinary_source_preserves_opaque_provider_fields(self):
        original = self.line("ordinary opaque provider data", result={"action": "get_work", "work": {"id": "opaque"}})
        self.source.write_bytes(original)
        self.hook()
        event = self.transcript_events()[0]
        self.assertEqual(retained_bytes(event), original)
        self.assertEqual(event["payload"]["entry"]["result"], {"action": "get_work", "work": {"id": "opaque"}})
        self.assertEqual(self.source_event()["payload"]["cursor"], len(original))

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

    def tool_use(self):
        return self.record("assistant", {"type": "tool_use", "id": "app-call", "name": "mcp__xorchestrate__get_work",
                                         "input": {"work_id": "private"}})

    def assert_partial_result_is_gap(self, call_id):
        first = self.tool_use()
        result = self.record("user", self.result(call_id, self.secret))
        content = first + result[:-1]
        self.capture(content)
        assert_protected(self.recorder.snapshot(), self.secret)
        tail = require_event(self.recorder.snapshot(), "native.transcript_tail")
        assert_transcript_gap(tail, source_bytes=len(result) - 1, range_start=len(first),
                              total_bytes=len(content), parser_status="incomplete")
        status = require_event(self.recorder.snapshot(), "native.transcript_source")["payload"]
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["cursor"], len(first), "incomplete records cannot advance the committed source cursor")
        with self.source.open("ab") as stream:
            stream.write(b"\n")
        self.driver.poll(self.session)
        self.assertTrue(self.driver.drain_telemetry(timeout=1))
        assert_protected(self.recorder.snapshot(), self.secret)
        complete = require_event(self.recorder.snapshot(), "native.transcript")
        self.assertEqual(complete["completeness"], "suppressed")
        self.assertEqual(require_event(self.recorder.snapshot(), "native.transcript_source")["payload"]["cursor"],
                         len(first + result))

    def test_partial_application_result_is_gap_before_and_after_appending_newline(self):
        self.assert_partial_result_is_gap("app-call")

    def test_partial_unattributed_result_is_gap_before_and_after_appending_newline(self):
        self.assert_partial_result_is_gap("never-observed")

    def test_read_bound_application_result_prefix_cannot_retain_private_content(self):
        first = self.tool_use()
        result = self.record("user", self.result("app-call", self.secret + " padding" * 100))
        observed = result.index(self.secret.encode()) + len(self.secret) + 8
        self.driver.max_transcript_bytes = len(first) + observed
        self.capture(first + result)
        assert_protected(self.recorder.snapshot(), self.secret)
        tail = require_event(self.recorder.snapshot(), "native.transcript_tail")
        assert_transcript_gap(tail, source_bytes=observed, range_start=len(first),
                              total_bytes=len(first + result), parser_status="incomplete")
        self.assertEqual(require_event(self.recorder.snapshot(), "native.transcript_source")["payload"]["cursor"], len(first))

    def test_frame_bound_application_result_prefix_cannot_retain_private_content(self):
        first = self.tool_use()
        result = self.record("user", self.result("app-call", self.secret + " padding" * 100))
        self.driver.max_transcript_frame = max(len(first), result.index(self.secret.encode()) + len(self.secret) + 8)
        self.capture(first + result)
        assert_protected(self.recorder.snapshot(), self.secret)
        tail = require_event(self.recorder.snapshot(), "native.transcript_tail")
        assert_transcript_gap(tail, source_bytes=self.driver.max_transcript_frame + 1, range_start=len(first),
                              total_bytes=len(first + result), parser_status="incomplete")
        self.assertEqual(require_event(self.recorder.snapshot(), "native.transcript_source")["payload"]["cursor"], len(first))

    def test_malformed_application_enabled_record_keeps_only_explicit_gap(self):
        first = self.tool_use()
        malformed = self.record("user", self.result("app-call", self.secret))[:-1] + b" broken-json\n"
        self.capture(first + malformed)
        assert_protected(self.recorder.snapshot(), self.secret)
        event = require_event(self.recorder.snapshot(), "native.transcript")
        assert_transcript_gap(event, source_bytes=len(malformed), range_start=len(first),
                              total_bytes=len(first + malformed), parser_status="invalid")
        self.assertEqual(require_event(self.recorder.snapshot(), "native.transcript_source")["payload"]["cursor"],
                         len(first + malformed), "acknowledged complete malformed records may advance only with an explicit gap")

    def test_completed_suppression_leaves_no_earlier_partial_reply_in_public_payload_reads(self):
        subject = TranscriptPrivacyFixture(self.root / "composed")
        try:
            subject.capture_partial()
            subject.complete_record()
            rows = subject.public_reads()
            self.assertTrue(any(row["event_type"] == "native.transcript_tail" for row in rows),
                            "the earlier partial source observation must remain in the immutable corpus")
            self.assertTrue(any(row["event_type"] == "native.transcript" and
                                row["payload"].get("representation") == "suppressed" for row in rows),
                            "the complete application result must exercise the existing suppression path")
            assert_protected(rows, subject.secret)
        finally:
            subject.close()

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
