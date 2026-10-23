"""Capability observations retain the existing report authority and transaction."""

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fixtures.control import ControlFixture

from devtools.api import API
from devtools.control import GAP_REPORTING
from devtools.store import Store


class CapabilityGapReportingTests(ControlFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.coder = API(self.state, "coder")

    @staticmethod
    def arguments(key="encounter-1", **metadata):
        return {
            "kind": "blocker", "text": "Need to inspect an installed dependency's public API.",
            "capability_gap": {
                "capability": "Inspect dependency API", "category": "unknown",
                "occurrence_key": key, **metadata,
            },
        }

    def counts(self):
        with self.store.connect() as db:
            return [db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in
                    ("work_updates", "messages", "capability_gaps", "capability_gap_occurrences")]

    def test_report_is_routed_and_metadata_readable_only_in_existing_scope(self):
        arguments = self.arguments(attempted_tool="framework_inspect", category="discovery")
        result = self.coder.call("work_report", arguments)
        self.assertTrue(result["capability_gap_recorded"])
        self.assertEqual({"reference", "saved", "reused", "delivery", "capability_gap_recorded"}, set(result))
        for member in ("coder", "xo", "owner"):
            report = API(self.state, member).call("work_context", {"reference": result["reference"]})
            self.assertEqual(arguments["capability_gap"], report["capability_gap"])
            self.assertNotIn("gap_id", report)
        with self.assertRaisesRegex(ValueError, "not available"):
            API(self.state, "audit").call("work_context", {"reference": result["reference"]})
        with self.store.connect() as db:
            message = db.execute("SELECT * FROM messages").fetchone()
            self.assertEqual("xo", message["recipient"])
            self.assertIn("Capability gap reported", message["body"])
            self.assertEqual("unknown", db.execute("SELECT classification FROM capability_gaps").fetchone()[0])
        self.assertEqual([1, 1, 1, 1], self.counts())

    def test_guidance_and_successful_workaround_can_report_without_a_failed_call(self):
        args = self.arguments(workaround="Completed using two permitted inspection calls.")
        args.pop("kind")
        result = self.coder.call("guidance_request", args)
        report = self.coder.call("work_context", {"reference": result["reference"]})
        self.assertEqual("guidance", report["kind"])
        self.assertEqual(args["capability_gap"]["workaround"], report["capability_gap"]["workaround"])
        self.assertEqual([1, 1, 1, 1], self.counts())

    def test_concurrent_retries_and_restart_reuse_one_encounter(self):
        arguments = self.arguments()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: API(self.state, "coder").call("work_report", arguments), range(4)))
        self.assertEqual(1, len({r["reference"] for r in results}))
        retry = API(self.state, "coder").call("work_report", arguments)
        self.assertTrue(retry["reused"])
        self.assertEqual([1, 1, 1, 1], self.counts())
        self.coder.call("work_report", self.arguments("encounter-2"))
        self.assertEqual([2, 2, 1, 2], self.counts())

    def test_same_encounter_with_changed_payload_is_rejected(self):
        arguments = self.arguments()
        self.coder.call("work_report", arguments)
        arguments["text"] = "Changed account of the same encounter"
        with self.assertRaises(ValueError):
            self.coder.call("work_report", arguments)
        self.assertEqual([1, 1, 1, 1], self.counts())

    def test_delivery_failure_rolls_back_report_and_gap(self):
        with patch.object(self.coder.store, "_send", side_effect=ValueError("Cannot route report")):
            with self.assertRaisesRegex(ValueError, "Cannot route report"):
                self.coder.call("work_report", self.arguments())
        self.assertEqual([0, 0, 0, 0], self.counts())
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM events WHERE kind='capability_gap_reported'").fetchone()[0])

    def test_rebind_during_request_cannot_record_wrong_work(self):
        with self.assertRaisesRegex(ValueError, "changed during this report"):
            self.coder.call("work_report", self.arguments(), on_context=lambda: self.coder.control.bind(
                "coder", "coder", self.order, self.other, "xo"))
        self.assertEqual([0, 0, 0, 0], self.counts())

    def test_reference_must_be_accessible_and_existing(self):
        sibling = API(self.state, "audit").call("work_report", {"text": "Private sibling evidence"})
        for reference in (sibling["reference"], "operation:999", "event:1"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.coder.call("work_report", self.arguments(reference=reference))
        self.assertEqual([1, 1, 0, 0], self.counts())
        own = self.coder.call("work_report", {"text": "My permitted inspection result"})
        self.coder.call("work_report", self.arguments(reference=own["reference"]))
        self.assertEqual([3, 3, 1, 1], self.counts())

    def test_command_reply_occurrence_uses_committed_report_scope(self):
        source = self.coder.call("work_report", {"text": "Please inspect my job's missing capability"})
        arguments = self.arguments()
        arguments["responding_to"] = source["reference"]
        result = API(self.state, "xo").call("work_report", arguments)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM work_updates WHERE id=?", (int(result["reference"].split(":")[1]),)).fetchone()
            self.assertEqual(self.job, row["job"])
            events = list(db.execute("SELECT * FROM events WHERE kind='capability_gap_reported'"))
            self.assertEqual(1, len(events))
            self.assertEqual(self.job, events[0]["job"])

    def test_invalid_gap_fails_before_publication_and_direct_report_also_validates(self):
        for metadata in ({"capability": " "}, {"category": "confirmed"}, {"occurrence_key": ""},
                         {"capability": "x" * 161}, {"status": "resolved"}):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.coder.call("work_report", self.arguments(**metadata))
        with self.assertRaises(ValueError):
            self.coder.control.report("Direct call", capability_gap={"capability": "No encounter"})
        self.assertEqual([0, 0, 0, 0], self.counts())

    def test_denial_does_not_confirm_gap_or_change_authority(self):
        before = self.coder.control.snapshot()
        for name in ("missing_tool", "capability_gap_review", "capability_gap_list"):
            with self.assertRaises(ValueError):
                self.coder.call(name, {})
        self.assertEqual([0, 0, 0, 0], self.counts())
        self.coder.call("work_report", self.arguments(category="permission"))
        self.assertEqual(before, self.coder.control.snapshot())
        with self.assertRaises(ValueError):
            self.coder.call("missing_tool", {})
        self.assertEqual([1, 1, 1, 1], self.counts())

    def test_metadata_uses_capture_filter_and_preserves_suppression(self):
        secret = "gap-test-secret-123456789"
        with patch.dict("os.environ", {"TEST_API_KEY": secret}):
            result = self.coder.call("work_report", self.arguments(workaround="Tried " + secret))
        report = self.coder.call("work_context", {"reference": result["reference"]})
        self.assertNotIn(secret, json.dumps(report["capability_gap"]))
        self.assertIn("[REDACTED]", report["capability_gap"]["workaround"])
        with patch("devtools.credentials.configured_values", side_effect=ValueError("Unavailable")):
            result = self.coder.call("work_report", self.arguments("suppressed", workaround=secret))
        report = self.coder.call("work_context", {"reference": result["reference"]})
        self.assertEqual({"capture": "suppressed", "reason": "credential_filter_unavailable"}, report["capability_gap"])
        Store(self.state)  # Recorded gaps survive a fresh service instance.
        self.assertEqual([2, 2, 1, 2], self.counts())

    def test_onboarding_teaches_gap_reporting_and_versions_the_guidance(self):
        value = self.coder.call("onboard", {})
        self.assertEqual(GAP_REPORTING, value["capability_gap_reporting"])
        with patch("devtools.onboarding.GAP_REPORTING", GAP_REPORTING + " Revised guidance."):
            changed = self.coder.call("onboard", {})
        self.assertNotEqual(value["revision"], changed["revision"])


if __name__ == "__main__":
    unittest.main()
