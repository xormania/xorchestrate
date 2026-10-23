import unittest
from unittest.mock import patch

from fixtures.control import ControlFixture

from devtools.control import Control
from devtools.diagnostics import Diagnostics
from devtools.recovery import capture_recovery, record_recovery


class RecoveryTelemetryTests(unittest.TestCase):
    setUp = ControlFixture.setUp
    client = ControlFixture.client

    def guidance(self, *, member="coder", failed_call=None, actions=None, **overrides):
        diagnostics = Diagnostics(self.store, member)
        scope = diagnostics.current()
        data = {
            "context_identity": diagnostics.identity(scope),
            "configuration_revision": diagnostics.settings(scope)["revision"],
            "operation": "event:100", "trigger": "steer",
            "failed_call": failed_call or {"tool": "steer", "arguments": {"action": "accept", "work": "Feature"}},
            "failure": {"category": "not_ready", "reason": "stale_measurement", "details": {}},
            "actions": actions if actions is not None else [{
                "tool": "work_check", "arguments": {"check": "behavior"},
                "condition": {"reason": "stale_measurement", "check": "behavior", "revision": "a" * 40},
            }],
            **overrides,
        }
        return self.store.event(member, "skill_guidance_delivered", work_order=scope["work_order"],
                                job=scope["job"], **data)

    def observed(self):
        return [row["data"] for row in self.store.events(kind="recovery_action_observed", limit=100)["events"]]

    def test_matching_check_records_measurement_without_claiming_completion_or_causality(self):
        guidance = self.guidance()
        candidates = capture_recovery(self.store, "coder", "work_check", {"check": "behavior"})
        self.assertEqual(1, len(candidates))
        record_recovery(self.store, "coder", candidates, operation="operation:12", result={
            "passed": True, "revision": "a" * 40, "uncommitted": [],
            "checks": [{"check": "behavior", "exit_code": 0, "evidence": "artifact:10"}],
        })
        event = self.observed()[0]
        self.assertEqual(f"event:{guidance}", event["guidance_event"])
        self.assertEqual("event:100", event["originating_operation"])
        self.assertEqual("operation:12", event["subsequent_operation"])
        self.assertEqual(["suggested_action"], event["matches"])
        self.assertIsNone(event["condition_resolved"])
        self.assertEqual("succeeded", event["outcome"])
        self.assertEqual({"check": "behavior", "passed": True, "evidence": "artifact:10",
                          "revision": "a" * 40, "expected_revision": "a" * 40,
                          "revision_matches": True, "workspace_clean": True}, event["measurements"][0])
        self.assertNotIn("caused", event)

    def test_unchanged_retry_fails_then_observed_original_success_resolves(self):
        self.guidance()
        args = {"work": "Feature", "action": "accept"}
        candidates = capture_recovery(self.store, "coder", "steer", args)
        record_recovery(self.store, "coder", candidates, operation="event:101", error=ValueError("Still stale"))
        record_recovery(self.store, "coder", candidates, operation="event:102", result={"retained": True, "outcome": "done"})
        self.assertEqual([False, True], [item["condition_resolved"] for item in self.observed()])
        self.assertEqual(["failed", "succeeded"], [item["outcome"] for item in self.observed()])

    def test_completed_worker_with_failed_check_and_uncertain_or_empty_results_do_not_resolve(self):
        self.guidance(failed_call={"tool": "work_check", "arguments": {"check": "behavior"}})
        candidates = capture_recovery(self.store, "coder", "work_check", {"check": "behavior"})
        results = [
            {"state": "completed", "result": {"passed": False}},
            {"state": "queued"}, {"state": "running"}, {"state": "uncertain"},
            {"state": "unknown"}, {"state": "completed"},
            {"state": "completed", "result": None}, None, {},
        ]
        for index, result in enumerate(results):
            record_recovery(self.store, "coder", candidates, operation=f"operation:{index}", result=result)
        events = self.observed()
        self.assertEqual(len(results), len(events))
        self.assertFalse(any(item["condition_resolved"] for item in events))
        self.assertEqual("failed", events[0]["outcome"])
        self.assertEqual("pending", events[1]["outcome"])
        self.assertEqual("unknown", events[-1]["outcome"])

    def test_arguments_actor_scope_identity_and_configuration_must_match(self):
        self.guidance()
        self.assertFalse(capture_recovery(self.store, "coder", "work_check", {"check": "other"}))
        self.assertFalse(capture_recovery(self.store, "audit", "work_check", {"check": "behavior"}))
        self.assertFalse(capture_recovery(self.store, "idle", "work_check", {"check": "behavior"}))
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET objective='Changed assignment' WHERE id=?", (self.job,))
        self.assertFalse(capture_recovery(self.store, "coder", "work_check", {"check": "behavior"}))
        self.guidance(configuration_revision="obsolete")
        self.assertFalse(capture_recovery(self.store, "coder", "work_check", {"check": "behavior"}))
        self.guidance()
        Control(self.store, "owner").bind("coder", "docs", self.order, self.job, "xo")
        self.assertFalse(capture_recovery(self.store, "coder", "work_check", {"check": "behavior"}))

    def test_successful_completion_can_release_binding_after_capture(self):
        self.guidance()
        candidates = capture_recovery(self.store, "coder", "steer", {"action": "accept", "work": "Feature"})
        with self.store.connect() as db:
            db.execute("DELETE FROM member_bindings WHERE member='coder'")
            db.execute("UPDATE members SET selected_work_order=NULL,selected_job=NULL WHERE name='coder'")
        record_recovery(self.store, "coder", candidates, operation="event:101", result={"retained": True})
        event = self.store.events(kind="recovery_action_observed")["events"][0]
        self.assertTrue(event["data"]["condition_resolved"])
        self.assertEqual(self.order, event["work_order"])
        self.assertEqual(self.job, event["job"])

    def test_unrelated_checks_cannot_substitute_for_suggested_measurement(self):
        self.guidance()
        candidates = capture_recovery(self.store, "coder", "work_check", {"check": "behavior"})
        record_recovery(self.store, "coder", candidates, operation="operation:1", result={
            "passed": True, "checks": [{"check": "other", "exit_code": 0}],
            "revision": "a" * 40, "uncommitted": [],
        })
        self.assertNotIn("measurements", self.observed()[0])
        record_recovery(self.store, "coder", candidates, operation="operation:2", result={
            "passed": True, "checks": [{"check": "behavior", "exit_code": 0}],
            "revision": "b" * 40, "uncommitted": ["changed.php"],
        })
        measured = self.observed()[1]["measurements"][0]
        self.assertTrue(measured["passed"])
        self.assertFalse(measured["revision_matches"])
        self.assertFalse(measured["workspace_clean"])

    def test_duplicate_delivery_and_observation_are_not_counted_twice(self):
        self.guidance()
        latest = self.guidance()
        candidates = capture_recovery(self.store, "coder", "work_check", {"check": "behavior"})
        self.assertEqual([f"event:{latest}"], [item["guidance_event"] for item in candidates])
        for _ in range(2):
            record_recovery(self.store, "coder", candidates, operation="operation:1", result={"passed": True})
        self.assertEqual(1, len(self.observed()))

    def test_capture_and_record_fail_harmlessly_and_current_identity_is_rechecked(self):
        self.guidance()
        candidates = capture_recovery(self.store, "coder", "work_check", {"check": "behavior"})
        with patch.object(Diagnostics, "unchanged", return_value=False):
            self.assertEqual([], capture_recovery(self.store, "coder", "work_check", {"check": "behavior"}))
        with patch.object(self.store, "connect", side_effect=RuntimeError("Telemetry unavailable")):
            self.assertEqual([], capture_recovery(self.store, "coder", "work_check", {"check": "behavior"}))
            self.assertIsNone(record_recovery(self.store, "coder", candidates, operation="operation:1", result={"passed": True}))
        self.assertFalse(self.observed())


if __name__ == "__main__":
    unittest.main()
