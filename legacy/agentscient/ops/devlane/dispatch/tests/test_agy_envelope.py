"""PROVISIONAL agy result-transport tests for launch._envelope.

Authored from ops/devlane/dispatch/CONTRACT.md §Configured workers
(the `_envelope` seam) and the supplied primary-source wire evidence.
launch.py was not read. Contract review is pending.

Pins: terminal result extraction from published stream-json frames,
exact canonical envelope semantics, vendor failure, missing required
vendor status, missing / ambiguous / malformed / truncated terminal
results, mixed bare JSON rejected under stream-json (including one
decoy-free init-plus-bare-result fixture), unrelated intermediate JSON,
decoy-free reject variants with a shared success control, legacy
extraction unchanged, SUCCESS plus a tool-level denial not turned into
an undeclared blanket failure, and a measured status-only CANCELED
carrier beside a plausible envelope. Cause vocabulary is not a second
schema: ENVELOPE_SCHEMA is not prohibited in notes, and missing-status
does not require a status substring.

Preserved reported receipts at e25e35d (not a fresh execution): 32
tests, 30 assertion failures, 2 legacy passes, zero errors, clean
registered lint. Original file SHA256 at that ref:
test_agy_envelope.py
0aa36d7cd000c7b5a91f519a24b17def1f8e93825f69a360e1d37d6ba88fb91d;
agy_envelope_support.py
d3bb7333ad761666f87a4d56ddba6cc73af8abfd7cc7dae7ec0419872a477e64.
"""

from __future__ import annotations

import json
import unittest

import agy_envelope_support as agy
import launch_support as ls

from ops.testkit import World

_OMIT = object()


class _AgyEnvelopeCase(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="agy-envelope-")
        self.addCleanup(self.world.close)
        self.launch = ls.require_module(self, "launch")
        self.ref = agy.LAUNCHER_REF
        self.raw_path = self.world.tmp / "raw.out"

    def parse(self, raw, job="plan", *, harness="agy", note=None):
        planted = self.world.plant_bytes(self.raw_path, raw)
        landed = planted.read_bytes()
        self.assertEqual(landed, raw, "plant: raw stdout landed")
        fn = getattr(self.launch, "_envelope", None)
        self.assertTrue(callable(fn), "launch._envelope parses stdout")
        args = [landed, job, self.ref]
        kwargs = {}
        if note is not None:
            args.append(note)
        if harness is not _OMIT:
            kwargs["harness"] = harness
        got = fn(*args, **kwargs)
        self.assertIsInstance(
            got, dict,
            f"_envelope returned {type(got).__name__}: {got!r}",
        )
        return got

    def assert_planted_decoys(self, raw):
        text = raw.decode("utf-8")
        self.assertIn(agy.DECOY_LEAD_NOTE, text, "plant: leading decoy")
        self.assertIn(agy.DECOY_TRAIL_NOTE, text, "plant: trailing decoy")
        lead = json.loads(text.splitlines()[0])
        trail = json.loads(text.splitlines()[-1])
        self.assertEqual(lead.get("note"), agy.DECOY_LEAD_NOTE)
        self.assertEqual(trail.get("note"), agy.DECOY_TRAIL_NOTE)
        for key in agy.NINE:
            self.assertIn(key, lead, f"plant: leading decoy has {key}")
            self.assertIn(key, trail, f"plant: trailing decoy has {key}")

    def assert_not_a_decoy(self, env):
        note = str(env.get("note") or "")
        self.assertNotEqual(env.get("note"), agy.DECOY_LEAD_NOTE, env)
        self.assertNotEqual(env.get("note"), agy.DECOY_TRAIL_NOTE, env)
        self.assertNotIn(agy.DECOY_LEAD_NOTE, note, env)
        self.assertNotIn(agy.DECOY_TRAIL_NOTE, note, env)

    def assert_invalid_cause(self, env, *needles):
        self.assertEqual(
            env.get("status"), "invalid",
            f"expected invalid, got {env!r}",
        )
        self.assertIsNone(
            env.get("verdict"),
            f"invalid carries no verdict: {env!r}",
        )
        note = str(env.get("note") or "")
        self.assertTrue(note, f"invalid must say why: {env!r}")
        blob = note.lower()
        for needle in needles:
            self.assertIn(
                needle.lower(), blob,
                f"cause must name {needle!r}: {env!r}",
            )
        self.assert_not_a_decoy(env)

    def assert_no_scan_decoys(self, raw):
        text = raw.decode("utf-8")
        self.assertNotIn(agy.DECOY_LEAD_NOTE, text, "plant: no leading decoy")
        self.assertNotIn(agy.DECOY_TRAIL_NOTE, text, "plant: no trailing decoy")

    def assert_carrier_reject(self, env, *, nested_note=None):
        """Invalid carrier: nonempty cause, no nested success."""
        self.assert_invalid_cause(env)
        if nested_note is not None:
            self.assertNotEqual(env.get("note"), nested_note, env)


class AgyStreamJsonTerminalResultIsTheCarrier(_AgyEnvelopeCase):
    """Decode only the measured terminal result carrier for this attempt."""

    def test_a_success_stream_json_result_extracts_the_nested_envelope(self):
        nested = agy.canonical_envelope(note="agy-stream-terminal")
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.step_update_frame(state="ACTIVE", text_delta="o"),
            agy.step_update_frame(state="DONE", text_delta="k"),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"event": "result"', raw)
        self.assertIn(b"agy-stream-terminal", raw)
        self.assertNotIn(b'"note": "agy-stream-terminal"', raw.splitlines()[0])
        env = self.parse(raw)
        self.assertEqual(
            env.get("note"), "agy-stream-terminal",
            "the terminal result.response JSON string is the envelope, "
            f"not a scan-shaped decoy: {env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)
        self.assertEqual(env.get("verdict"), "approve", env)
        self.assertEqual(env.get("job"), "plan", env)
        stamp = env.get("stamp") or {}
        self.assertEqual(
            stamp.get("ref"), self.ref,
            f"launcher overwrites stamp.ref: stamp={stamp!r}",
        )
        self.assertNotEqual(stamp.get("ref"), agy.PLACEHOLDER_REF)

    def test_a_mixed_bare_published_result_is_rejected_under_stream_json(self):
        nested = agy.canonical_envelope(note="agy-print-terminal")
        result = agy.published_result(nested)
        raw = agy.ndjson_bytes(
            agy.decoy_envelope(agy.DECOY_LEAD_NOTE),
            result,
            agy.decoy_envelope(agy.DECOY_TRAIL_NOTE),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"conversation_id"', raw)
        self.assertNotIn(b'"event": "result"', raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env)
        self.assertNotEqual(
            env.get("note"), "agy-print-terminal",
            "a mixed bare published result is not the stream-json "
            f"terminal carrier: {env!r}",
        )
        self.assertNotEqual(env.get("status"), "ok", env)

    def test_step_update_json_is_not_the_terminal_result(self):
        nested = agy.canonical_envelope(note="agy-from-result")
        decoy_json = agy.dumps(agy.canonical_envelope(note="step-delta-decoy"))
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.step_update_frame(
                state="ACTIVE",
                step_type="agent_response",
                text_delta=decoy_json,
            ),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b"step-delta-decoy", raw, "plant: step text_delta")
        self.assertIn(b'"event": "step_update"', raw)
        env = self.parse(raw)
        self.assertEqual(
            env.get("note"), "agy-from-result",
            "a step_update payload is intermediate JSON, not the "
            f"terminal result: {env!r}",
        )
        self.assertNotEqual(env.get("note"), "step-delta-decoy", env)

    def test_a_raw_nine_key_object_is_not_an_agy_terminal_result(self):
        planted = agy.canonical_envelope(note="unrelated-intermediate")
        raw = agy.ndjson_bytes(planted)
        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual(parsed.get("note"), "unrelated-intermediate")
        for key in agy.NINE:
            self.assertIn(key, parsed, f"plant: {key} present")
        env = self.parse(raw, harness="agy")
        self.assert_invalid_cause(env, "missing")
        self.assertNotEqual(
            env.get("note"), "unrelated-intermediate",
            "agy must not treat unrelated intermediate JSON as the "
            f"terminal result: {env!r}",
        )


class AgyVendorFailureDoesNotCertifyWork(_AgyEnvelopeCase):
    """Vendor errors even beside a plausible envelope; SUCCESS is not enough.

    Status-only CANCELED is the measured native non-SUCCESS status.
    Fixtures do not invent further enums.
    """

    def test_a_vendor_error_is_invalid_even_beside_a_plausible_envelope(self):
        nested = agy.canonical_envelope(note="agy-beside-vendor-error")
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(
                nested, error="vendor boom",
            )),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"error": "vendor boom"', raw, "plant: vendor error")
        self.assertIn(b"agy-beside-vendor-error", raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "error")
        self.assertNotEqual(env.get("status"), "ok", env)
        self.assertNotEqual(
            env.get("note"), "agy-beside-vendor-error",
            "a vendor error must not close as the nested envelope: "
            f"{env!r}",
        )

    def test_a_canceled_status_only_result_is_invalid_beside_a_plausible_envelope(
            self):
        nested = agy.canonical_envelope(note="agy-beside-canceled")
        result = agy.published_result(
            nested,
            status=agy.MEASURED_CANCELED,
            extra={"denied_actions": []},
        )
        self.assertEqual(result["status"], agy.MEASURED_CANCELED)
        self.assertNotIn("error", result)
        self.assertEqual(result["denied_actions"], [])
        self.assertNotEqual(result["response"], "")
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(result),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"status": "CANCELED"', raw, "plant: measured status")
        self.assertIn(b'"denied_actions": []', raw, "plant: denied_actions")
        self.assertNotIn(b'"error"', raw.split(
            b'"event": "result"',
        )[-1], "plant: status-only, no error string")
        self.assertIn(b"agy-beside-canceled", raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env)
        self.assertNotEqual(env.get("status"), "ok", env)
        self.assertNotEqual(
            env.get("note"), "agy-beside-canceled",
            "a status-only CANCELED result must not close as the "
            f"nested envelope: {env!r}",
        )

    def test_a_result_missing_required_vendor_status_is_invalid(self):
        nested = agy.canonical_envelope(note="agy-missing-vendor-status")
        result = agy.drop_fields(agy.published_result(nested), "status")
        self.assertNotIn("status", result)
        self.assertIn("conversation_id", result)
        self.assertIn("response", result)
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(result),
        )
        self.assert_planted_decoys(raw)
        planted = json.loads(
            [ln for ln in raw.decode("utf-8").splitlines()
             if '"event": "result"' in ln][0],
        )
        self.assertNotIn("status", planted["result"], "plant: status omitted")
        env = self.parse(raw)
        self.assert_invalid_cause(env)
        self.assertNotEqual(
            env.get("note"), "agy-missing-vendor-status",
            "a result missing required vendor status is not a "
            f"terminal carrier: {env!r}",
        )

    def test_vendor_success_with_plain_response_does_not_certify_work(self):
        result = agy.published_result("ok")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["response"], "ok")
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(result),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"status": "SUCCESS"', raw)
        self.assertIn(b'"response": "ok"', raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env)
        self.assertNotEqual(
            env.get("status"), "ok",
            "vendor schema acceptance alone never certifies work: "
            f"{env!r}",
        )


class AgyDoesNotBlanketFailBenignToolDenial(_AgyEnvelopeCase):
    """1.1.20/1.1.27: a tool denial is not an undeclared blanket failure."""

    def test_success_with_denied_actions_still_extracts_the_envelope(self):
        nested = agy.canonical_envelope(note="agy-denied-actions-present")
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(
                nested, extra={"denied_actions": []},
            )),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"denied_actions": []', raw, "plant: key present")
        self.assertIn(b'"status": "SUCCESS"', raw)
        env = self.parse(raw)
        self.assertEqual(
            env.get("note"), "agy-denied-actions-present",
            "denied_actions on a SUCCESS result is not a blanket "
            f"failure: {env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)
        self.assertEqual(env.get("verdict"), "approve", env)

    def test_a_tool_info_permission_error_does_not_fail_a_success_result(self):
        nested = agy.canonical_envelope(note="agy-tool-info-error")
        tool_info = {
            "name": "synthetic_tool",
            "parameters": {},
            "output": "",
            "error": {"type": "permission", "message": "denied"},
        }
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.step_update_frame(
                state="DONE",
                step_type="tool",
                extra={
                    "tool_name": "synthetic_tool",
                    "tool_info": tool_info,
                },
            ),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b'"type": "permission"', raw, "plant: tool_info.error")
        self.assertIn(b'"message": "denied"', raw)
        self.assertIn(b'"status": "SUCCESS"', raw)
        env = self.parse(raw)
        self.assertEqual(
            env.get("note"), "agy-tool-info-error",
            "a benign tool-level denial must not become a blanket "
            f"failure: {env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)


class AgyMissingAmbiguousMalformedTruncatedResultsAreInvalid(_AgyEnvelopeCase):
    """The agy branch rejects those terminal-result defects."""

    def test_a_missing_result_event_is_invalid(self):
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.step_update_frame(state="DONE", text_delta="k"),
        )
        self.assert_planted_decoys(raw)
        self.assertNotIn(b'"event": "result"', raw, "plant: no result event")
        env = self.parse(raw)
        self.assert_invalid_cause(env, "missing")

    def test_two_result_events_are_ambiguous(self):
        first = agy.canonical_envelope(note="agy-first-result")
        second = agy.canonical_envelope(note="agy-second-result")
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(first)),
            agy.result_frame(agy.published_result(second)),
        )
        self.assert_planted_decoys(raw)
        self.assertEqual(raw.count(b'"event": "result"'), 2, "plant: two")
        env = self.parse(raw)
        self.assert_invalid_cause(env, "ambiguous")
        self.assertNotEqual(env.get("note"), "agy-first-result", env)
        self.assertNotEqual(env.get("note"), "agy-second-result", env)

    def test_a_truncated_result_line_is_invalid(self):
        nested = agy.canonical_envelope(note="agy-truncated-line")
        complete = agy.dumps(agy.result_frame(agy.published_result(nested)))
        self.assertGreater(len(complete), 40, "plant: line is long enough")
        truncated = complete[:40]
        self.assertNotEqual(truncated, complete)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(truncated)
        raw = agy.ndjson_bytes(
            agy.decoy_envelope(agy.DECOY_LEAD_NOTE),
            agy.init_frame(),
        ) + truncated.encode("utf-8") + b"\n" + agy.ndjson_bytes(
            agy.decoy_envelope(agy.DECOY_TRAIL_NOTE),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(truncated.encode("utf-8"), raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env)

    def test_a_result_payload_that_is_not_an_object_is_malformed(self):
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            {"event": "result", "result": "not-an-object"},
        )
        self.assert_planted_decoys(raw)
        planted = json.loads(
            [ln for ln in raw.decode("utf-8").splitlines()
             if '"event": "result"' in ln][0],
        )
        self.assertEqual(planted.get("result"), "not-an-object")
        env = self.parse(raw)
        self.assert_invalid_cause(env)

    def test_a_truncated_response_string_is_invalid(self):
        nested = agy.canonical_envelope(note="agy-truncated-response")
        cut = agy.dumps(nested)[:24]
        self.assertTrue(cut.startswith("{"), "plant: response starts as JSON")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(cut)
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(cut)),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(cut.encode("utf-8"), raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env)


class AgyCanonicalEnvelopeSemantics(_AgyEnvelopeCase):
    """Apply ENVELOPE_SCHEMA and envelope.validate to the extracted object."""

    def test_an_invalid_envelope_with_null_verdict_is_accepted(self):
        nested = agy.canonical_envelope(
            status="invalid", verdict=None, note="agy-null-verdict",
        )
        self.assertIsNone(nested["verdict"])
        self.assertNotIn("commit", nested)
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        env = self.parse(raw)
        self.assertEqual(env.get("note"), "agy-null-verdict", env)
        self.assertEqual(env.get("status"), "invalid", env)
        self.assertIsNone(env.get("verdict"), env)

    def test_a_tripped_envelope_with_changes_is_accepted(self):
        nested = agy.canonical_envelope(
            status="tripped",
            verdict="changes",
            findings=[agy.finding("p1")],
            counts={"p1": 1, "p2": 0, "p3": 0, "opinions": 0},
            note="agy-tripped-changes",
        )
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        env = self.parse(raw)
        self.assertEqual(env.get("note"), "agy-tripped-changes", env)
        self.assertEqual(env.get("status"), "tripped", env)
        self.assertEqual(env.get("verdict"), "changes", env)
        self.assertEqual(env.get("counts", {}).get("p1"), 1, env)

    def test_optional_commit_is_accepted(self):
        nested = agy.canonical_envelope(
            note="agy-with-commit",
            commit={"subject": "pin tests", "body": "provisional agy pins"},
        )
        self.assertEqual(set(nested["commit"]), {"subject", "body"})
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        env = self.parse(raw)
        self.assertEqual(env.get("note"), "agy-with-commit", env)
        self.assertEqual(env.get("status"), "ok", env)
        commit = env.get("commit") or {}
        self.assertEqual(commit.get("subject"), "pin tests", env)
        self.assertEqual(commit.get("body"), "provisional agy pins", env)

    def test_a_finding_with_null_reproduce_is_an_opinion(self):
        nested = agy.canonical_envelope(
            status="ok",
            verdict="changes",
            findings=[agy.finding(reproduce=None)],
            counts={"p1": 0, "p2": 1, "p3": 0, "opinions": 1},
            note="agy-opinion",
        )
        self.assertIsNone(nested["findings"][0]["reproduce"])
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        env = self.parse(raw)
        self.assertEqual(env.get("note"), "agy-opinion", env)
        self.assertEqual(env.get("counts", {}).get("opinions"), 1, env)
        self.assertIsNone(env["findings"][0]["reproduce"], env)

    def test_an_extra_top_level_key_is_invalid(self):
        nested = agy.canonical_envelope(
            note="agy-extra-key",
        )
        nested["transcript"] = "not a field"
        self.assertIn("transcript", nested)
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        self.assertIn(b"agy-extra-key", raw, "plant: nested extra-key envelope")
        self.assertIn(b"transcript", raw, "plant: extra key")
        env = self.parse(raw)
        self.assert_invalid_cause(env, "transcript")

    def test_counts_that_disagree_with_findings_are_invalid(self):
        nested = agy.canonical_envelope(
            verdict="changes",
            findings=[agy.finding("p1")],
            counts={"p1": 4, "p2": 0, "p3": 0, "opinions": 0},
            note="agy-counts-mismatch",
        )
        self.assertEqual(nested["counts"]["p1"], 4)
        self.assertEqual(len(nested["findings"]), 1)
        raw = agy.stream_with_decoys(
            agy.init_frame(),
            agy.result_frame(agy.published_result(nested)),
        )
        self.assert_planted_decoys(raw)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "counts")


class AgyRejectRulesWithoutScanDecoys(_AgyEnvelopeCase):
    """Bounded decoy-free reject variants with one shared success control.

    Original decoy extraction controls stay on the cases above. Reject
    methods do not re-parse that success stream. One mixed-bare fixture
    plants init plus an unframed published result, with no scan decoys.
    """

    def valid_control(self, note):
        nested = agy.canonical_envelope(note=note)
        raw = agy.valid_terminal_stream(nested)
        self.assert_no_scan_decoys(raw)
        self.assertIn(b'"event": "result"', raw)
        self.assertIn(note.encode("utf-8"), raw)
        return nested, raw

    def test_a_decoy_free_success_stream_extracts_the_nested_envelope(self):
        nested, raw = self.valid_control("agy-decoy-free-ok")
        env = self.parse(raw)
        self.assertEqual(env.get("note"), nested["note"], env)
        self.assertEqual(env.get("status"), "ok", env)
        self.assertEqual(env.get("verdict"), "approve", env)
        stamp = env.get("stamp") or {}
        self.assertEqual(stamp.get("ref"), self.ref, stamp)
        self.assertNotEqual(stamp.get("ref"), agy.PLACEHOLDER_REF)

    def test_a_mixed_bare_published_result_is_rejected_without_decoys(self):
        nested = agy.canonical_envelope(note="agy-print-terminal-nodecoy")
        result = agy.published_result(nested)
        raw = agy.ndjson_bytes(agy.init_frame(), result)
        self.assert_no_scan_decoys(raw)
        self.assertIn(b'"event": "init"', raw, "plant: init frame")
        self.assertIn(b'"conversation_id"', raw)
        self.assertNotIn(b'"event": "result"', raw, "plant: no result frame")
        self.assertIn(b"agy-print-terminal-nodecoy", raw)
        parsed_lines = [
            json.loads(ln) for ln in raw.decode("utf-8").splitlines() if ln
        ]
        self.assertEqual(parsed_lines[0].get("event"), "init")
        self.assertEqual(parsed_lines[1].get("status"), "SUCCESS")
        self.assertNotIn("event", parsed_lines[1])
        env = self.parse(raw)
        self.assert_invalid_cause(env)
        self.assertNotEqual(
            env.get("note"), "agy-print-terminal-nodecoy",
            "a mixed bare published result is not the stream-json "
            f"terminal carrier: {env!r}",
        )
        self.assertNotEqual(env.get("status"), "ok", env)

    def test_a_missing_result_event_is_invalid_without_decoys(self):
        _nested, control = self.valid_control("agy-missing-nodecoy")
        raw = agy.ndjson_bytes(
            agy.init_frame(),
            agy.step_update_frame(state="DONE", text_delta="k"),
        )
        self.assert_no_scan_decoys(raw)
        self.assertNotIn(b'"event": "result"', raw, "plant: no result")
        self.assertIn(b'"event": "result"', control)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "missing")

    def test_two_result_events_are_ambiguous_without_decoys(self):
        first = agy.canonical_envelope(note="agy-first-nodecoy")
        second = agy.canonical_envelope(note="agy-second-nodecoy")
        _nested, control = self.valid_control("agy-first-nodecoy")
        raw = agy.ndjson_bytes(
            agy.init_frame(),
            agy.result_frame(agy.published_result(first)),
            agy.result_frame(agy.published_result(second)),
        )
        self.assert_no_scan_decoys(raw)
        self.assertEqual(raw.count(b'"event": "result"'), 2, "plant: two")
        self.assertEqual(control.count(b'"event": "result"'), 1)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "ambiguous")
        self.assertNotEqual(env.get("note"), "agy-first-nodecoy", env)
        self.assertNotEqual(env.get("note"), "agy-second-nodecoy", env)

    def test_a_truncated_result_line_is_invalid_without_decoys(self):
        nested, control = self.valid_control("agy-truncated-line-nodecoy")
        complete = agy.dumps(agy.result_frame(agy.published_result(nested)))
        self.assertGreater(len(complete), 40, "plant: line is long enough")
        truncated = complete[:40]
        with self.assertRaises(json.JSONDecodeError):
            json.loads(truncated)
        raw = (
            agy.ndjson_bytes(agy.init_frame())
            + truncated.encode("utf-8")
            + b"\n"
        )
        self.assert_no_scan_decoys(raw)
        self.assertIn(truncated.encode("utf-8"), raw)
        self.assertNotEqual(raw, control)
        env = self.parse(raw)
        self.assert_carrier_reject(env, nested_note=nested["note"])

    def test_a_result_payload_that_is_not_an_object_is_malformed_without_decoys(
            self):
        _nested, control = self.valid_control("agy-malformed-nodecoy")
        raw = agy.ndjson_bytes(
            agy.init_frame(),
            {"event": "result", "result": "not-an-object"},
        )
        self.assert_no_scan_decoys(raw)
        planted = json.loads(
            [ln for ln in raw.decode("utf-8").splitlines()
             if '"event": "result"' in ln][0],
        )
        self.assertEqual(planted.get("result"), "not-an-object")
        self.assertIn(b'"event": "result"', control)
        env = self.parse(raw)
        self.assert_carrier_reject(env, nested_note="agy-malformed-nodecoy")

    def test_a_truncated_response_string_is_invalid_without_decoys(self):
        nested, control = self.valid_control("agy-truncated-response-nodecoy")
        cut = agy.dumps(nested)[:24]
        self.assertTrue(cut.startswith("{"), "plant: response starts as JSON")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(cut)
        raw = agy.valid_terminal_stream(cut)
        self.assert_no_scan_decoys(raw)
        planted = json.loads(
            [ln for ln in raw.decode("utf-8").splitlines()
             if '"event": "result"' in ln][0],
        )
        self.assertEqual(
            planted["result"]["response"], cut,
            "plant: response is the truncated JSON string",
        )
        with self.assertRaises(json.JSONDecodeError):
            json.loads(planted["result"]["response"])
        self.assertNotEqual(raw, control)
        env = self.parse(raw)
        self.assert_carrier_reject(env, nested_note=nested["note"])

    def test_an_extra_top_level_key_is_invalid_without_decoys(self):
        nested = agy.canonical_envelope(note="agy-extra-key-nodecoy")
        nested["transcript"] = "not a field"
        self.assertIn("transcript", nested)
        _control_nested, control = self.valid_control("agy-extra-key-nodecoy")
        raw = agy.valid_terminal_stream(nested)
        self.assert_no_scan_decoys(raw)
        self.assertIn(b"transcript", raw, "plant: extra key")
        self.assertNotIn(b"transcript", control)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "transcript")

    def test_counts_that_disagree_with_findings_are_invalid_without_decoys(self):
        nested = agy.canonical_envelope(
            verdict="changes",
            findings=[agy.finding("p1")],
            counts={"p1": 4, "p2": 0, "p3": 0, "opinions": 0},
            note="agy-counts-mismatch-nodecoy",
        )
        self.assertEqual(nested["counts"]["p1"], 4)
        self.assertEqual(len(nested["findings"]), 1)
        _control_nested, control = self.valid_control(
            "agy-counts-mismatch-nodecoy",
        )
        raw = agy.valid_terminal_stream(nested)
        self.assert_no_scan_decoys(raw)
        self.assertNotEqual(raw, control)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "counts")

    def test_a_vendor_error_is_invalid_without_decoys(self):
        nested, control = self.valid_control("agy-vendor-error-nodecoy")
        raw = agy.valid_terminal_stream(nested, error="vendor boom")
        self.assert_no_scan_decoys(raw)
        self.assertIn(b'"error": "vendor boom"', raw, "plant: vendor error")
        self.assertNotIn(b'"error"', control)
        env = self.parse(raw)
        self.assert_invalid_cause(env, "error")
        self.assertNotEqual(env.get("note"), nested["note"], env)

    def test_a_result_missing_required_vendor_status_is_invalid_without_decoys(
            self):
        nested, control = self.valid_control("agy-missing-status-nodecoy")
        result = agy.drop_fields(agy.published_result(nested), "status")
        self.assertNotIn("status", result)
        raw = agy.ndjson_bytes(agy.init_frame(), agy.result_frame(result))
        self.assert_no_scan_decoys(raw)
        planted = json.loads(
            [ln for ln in raw.decode("utf-8").splitlines()
             if '"event": "result"' in ln][0],
        )
        self.assertNotIn("status", planted["result"], "plant: status omitted")
        self.assertIn("status", json.loads(
            [ln for ln in control.decode("utf-8").splitlines()
             if '"event": "result"' in ln][0],
        )["result"])
        env = self.parse(raw)
        self.assert_invalid_cause(env)
        self.assertNotEqual(env.get("note"), nested["note"], env)


class LegacyEnvelopeExtractionIsUnchanged(_AgyEnvelopeCase):
    """Do not rewrite legacy extraction for grok/plain stdout."""

    def test_omitted_harness_still_parses_a_raw_envelope(self):
        planted = agy.canonical_envelope(note="legacy-omitted-harness")
        raw = agy.dumps(planted).encode("utf-8")
        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual(parsed.get("note"), "legacy-omitted-harness")
        env = self.parse(raw, harness=_OMIT)
        self.assertEqual(
            env.get("note"), "legacy-omitted-harness",
            f"legacy scan without harness= must still parse: {env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_grok_harness_still_parses_a_raw_envelope(self):
        planted = agy.canonical_envelope(note="legacy-grok-harness")
        raw = agy.dumps(planted).encode("utf-8")
        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual(parsed.get("note"), "legacy-grok-harness")
        env = self.parse(raw, harness="grok")
        self.assertEqual(
            env.get("note"), "legacy-grok-harness",
            f"grok extraction must stay the legacy scan: {env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)


if __name__ == "__main__":
    unittest.main()
