import json
import unittest
from unittest.mock import patch

from fixtures.completion import CompletionFixture, answer, completion_rule
from fixtures.workspaces import WorkspaceFixture

from devtools.api import API
from devtools.completion import Completion
from devtools.configuration import defaults, resolve, validate
from devtools.failures import Failure, failure_info
from devtools.jev import JevUnavailable
from devtools.workspaces import git


class CompletionTests(CompletionFixture, unittest.TestCase):


    def test_independent_completion_enable_is_operator_owned(self):
        self.configure(None)
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("proof_missing", failure_info(blocked.exception)["reason"])
        self.configure({"features": {"completion": False}})
        self.assertEqual("disabled", self.gate.require("job", job=self.job)["state"])
        self.assertEqual([], self.requests)
        self.configure({"features": {"completion": "false"}})
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.gate.require("job", job=self.job)

    def test_result_claim_without_retained_proof_does_not_call_jev(self):
        self.api.call("work_report", {"kind": "result", "text": "All done"})
        with self.assertRaisesRegex(Failure, "claim alone") as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("not_ready", failure_info(blocked.exception)["category"])
        self.assertEqual("proof_missing", failure_info(blocked.exception)["reason"])
        self.assertEqual([], self.requests)
        event = self.store.events(kind="completion_blocked")["events"][0]["data"]
        self.assertEqual(str(blocked.exception), event["reason"])
        self.assertEqual(failure_info(blocked.exception), event["failure"])

    def test_paused_completion_is_not_ready_for_current_coordinators(self):
        self.xo.control.bind("docs", "nco", self.order, self.job, "xo")
        with self.store.connect() as db:
            db.execute("UPDATE member_bindings SET paused=1 WHERE member IN ('xo','docs')")
        for member in ("xo", "docs"):
            gate = Completion(self.store, member)
            for enabled in (True, False):
                self.configure({"features": {"completion": enabled}})
                for method in ("settings", "collect", "require"):
                    with self.subTest(member=member, enabled=enabled, method=method):
                        arguments = {"boundary": "job", "job": self.job}
                        if method == "collect":
                            arguments.update(assignment=None, revision=None, mechanical=None)
                        with self.assertRaisesRegex(Failure, "paused") as blocked:
                            getattr(gate, method)(**arguments)
                        self.assertEqual({"category": "not_ready", "reason": "work_paused", "details": {}}, failure_info(blocked.exception))
        with self.store.connect() as db:
            db.execute("UPDATE member_bindings SET paused=0 WHERE member IN ('xo','docs')")
        for member in ("xo", "docs"):
            self.assertEqual("disabled", Completion(self.store, member).require("job", job=self.job)["state"])
        self.assertEqual([], self.requests)

    def test_pause_does_not_mask_invalid_completion_bindings(self):
        with self.store.connect() as db:
            db.execute("UPDATE member_bindings SET paused=1")
        stale = {**self.gate.control.current(), "revision": -1}
        cases = (("xo", stale), ("coder", self.api.control.current()), ("docs", None))
        for member, scope in cases:
            gate = Completion(self.store, member)
            with patch.object(gate.control, "current", return_value=scope):
                for method in ("settings", "collect", "require"):
                    with self.subTest(member=member, method=method):
                        arguments = {"boundary": "job", "job": self.job}
                        if method == "collect":
                            arguments.update(assignment=None, revision=None, mechanical=None)
                        with self.assertRaises(Failure) as blocked:
                            getattr(gate, method)(**arguments)
                        self.assertEqual({"category": "not_permitted", "reason": "scope_not_permitted", "details": {}}, failure_info(blocked.exception))
        self.assertEqual([], self.requests)

    def test_assignment_uses_its_original_brief_and_full_owner_boundaries(self):
        assignment = self.assignment()
        self.report()
        self.assertEqual("ready", self.gate.require("assignment", assignment=assignment)["state"])
        state = self.requests[0][0]
        self.assertEqual("Implement behavior", state["requirements"]["objective"])
        self.assertEqual("Behavior demonstrated", state["requirements"]["done_when"])
        self.assertEqual("Implement", state["owner"]["objective"])
        self.assertEqual("Feature", state["job"]["objective"])
        self.assertIn("specialist need not", state["boundary_rule"])

    def test_assignment_recovery_metadata_keeps_its_proof_binding(self):
        assignment = self.assignment()
        with self.assertRaises(Failure) as blocked:
            self.gate.require("assignment", assignment=assignment)
        self.assertEqual(assignment, failure_info(blocked.exception)["details"]["assignment"])
        cases = (
            ({"state": "running"}, "a" * 40, "measurement_active"),
            ({"state": "uncertain"}, "a" * 40, "measurement_incomplete"),
            ({"passed": False}, "a" * 40, "check_failed"),
            ({"revision": "a" * 40}, "b" * 40, "proof_stale"),
        )
        for arguments, revision, reason in cases:
            with self.subTest(reason=reason):
                with self.store.connect() as db:
                    db.execute("DELETE FROM operations")
                operation = self.operation(**arguments)
                with self.assertRaises(Failure) as blocked:
                    self.gate.require("assignment", assignment=assignment, revision=revision)
                failure = failure_info(blocked.exception)
                self.assertEqual(reason, failure["reason"])
                self.assertEqual(assignment, failure["details"]["assignment"])
                self.assertEqual(self.job, failure["details"]["job"])
                self.assertIn("operation:" + str(operation), failure["details"]["references"])
        self.assertEqual([], self.requests)

    def test_each_required_condition_must_pass_no_average_or_done_plan_substitution(self):
        self.report("Only the happy path was measured; recovery remains untested.")

        def incomplete(state, questions, model):
            result = answer(state, questions, model)
            result["answers"]["done_when"]["noul"] = 0.4
            return result

        self.provider.side_effect = incomplete
        with self.assertRaisesRegex(Failure, "done_when") as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("proof_insufficient", failure_info(blocked.exception)["reason"])
        event = self.store.events(kind="completion_assessed")["events"][0]
        self.assertEqual(["done_when"], event["data"]["unresolved"])
        self.assertTrue(event["data"]["evidence"].startswith("artifact:"))

    def test_original_owner_goal_survives_narrowed_job_and_cancelled_contributions(self):
        self.report()
        with self.store.connect() as db:
            db.execute("UPDATE work_orders SET objective='Deliver happy path and recovery',done_when='Both paths measured' WHERE id=?", (self.order,))
            db.execute("UPDATE jobs SET objective='Happy path',status='cancelled' WHERE id=?", (self.job,))
        self.gate.require("work_order")
        state = self.requests[0][0]
        self.assertEqual("Deliver happy path and recovery", state["requirements"]["objective"])
        self.assertEqual("cancelled", state["jobs"][0]["status"])
        self.assertIn("cancelled jobs", state["boundary_rule"])

    def test_mechanical_failure_cannot_be_overridden_by_high_jev_probability(self):
        self.report()
        operation = self.operation(passed=False)
        with self.assertRaisesRegex(Failure, "behavior") as blocked:
            self.gate.require("job", job=self.job, revision="a" * 40)
        failure = failure_info(blocked.exception)
        self.assertEqual("check_failed", failure["reason"])
        self.assertEqual(["behavior"], failure["details"]["checks"])
        self.assertIn("operation:" + str(operation), failure["details"]["references"])
        self.assertEqual(self.job, failure["details"]["job"])
        self.assertEqual([], self.requests)
        with self.assertRaisesRegex(ValueError, "ci check to pass"):
            self.gate.require("job", job=self.job, mechanical={"ci": {"passed": False}})

    def test_failed_or_uncertain_operation_without_named_checks_blocks(self):
        self.report()
        self.operation(passed=False, checks=False)
        with self.assertRaisesRegex(Failure, "measurement to pass") as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("check_failed", failure_info(blocked.exception)["reason"])
        operation = self.operation(state="uncertain")
        with self.assertRaisesRegex(Failure, "completed current measurement") as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("measurement_incomplete", failure_info(blocked.exception)["reason"])
        self.assertIn("operation:" + str(operation), failure_info(blocked.exception)["details"]["references"])
        self.assertEqual([], self.requests)

    def test_active_measurement_reports_the_operation_to_wait_for(self):
        operation = self.operation(state="running")
        with self.assertRaisesRegex(Failure, "active measurement") as blocked:
            self.gate.require("job", job=self.job)
        failure = failure_info(blocked.exception)
        self.assertEqual("measurement_active", failure["reason"])
        self.assertIn("operation:" + str(operation), failure["details"]["references"])
        self.assertEqual([], self.requests)

    def test_current_measurement_supersedes_failure_and_identical_proof_cache_reuses_request(self):
        self.report()
        self.operation(passed=False)
        self.operation(passed=True)
        first = self.gate.require("job", job=self.job, revision="a" * 40)
        second = self.gate.require("job", job=self.job, revision="a" * 40)
        self.assertEqual(first["evidence"], second["evidence"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(1, len(self.requests))
        self.assertTrue(any(p["current_source"] is True for p in self.requests[0][0]["proof"]))

    def test_unrelated_named_check_cannot_erase_failed_check(self):
        self.report()
        self.operation(passed=False, name="recovery", arguments={"check": "recovery"})
        self.operation(passed=True, name="happy", arguments={"check": "happy"})
        with self.assertRaisesRegex(ValueError, "recovery"):
            self.gate.require("job", job=self.job)

    def test_other_revision_cannot_mask_current_failed_measurement(self):
        self.report()
        self.operation(passed=False, revision="a" * 40)
        self.operation(passed=True, revision="b" * 40)
        with self.assertRaisesRegex(ValueError, "behavior"):
            self.gate.require("job", job=self.job, revision="a" * 40)

    def test_old_check_and_current_claim_cannot_prove_current_code(self):
        report = self.report()
        operation = self.operation(revision="a" * 40)
        for _ in range(2):
            with self.assertRaisesRegex(Failure, "current source revision") as blocked:
                self.gate.require("job", job=self.job, revision="b" * 40)
            failure = failure_info(blocked.exception)
            self.assertEqual("not_ready", failure["category"])
            self.assertEqual("proof_stale", failure["reason"])
            self.assertEqual(["behavior"], failure["details"]["checks"])
            self.assertEqual("b" * 40, failure["details"]["revision"])
            self.assertIn("operation:" + str(operation), failure["details"]["references"])
            self.assertIn(report["evidence"], failure["details"]["references"])
        self.assertEqual([], self.requests)
        self.operation(revision="b" * 40)
        self.assertEqual("ready", self.gate.require("job", job=self.job, revision="b" * 40)["state"])

    def test_stale_measurement_without_current_report_keeps_its_recovery_reference(self):
        operation = self.operation(revision="a" * 40)
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job, revision="b" * 40)
        failure = failure_info(blocked.exception)
        self.assertEqual("proof_stale", failure["reason"])
        self.assertEqual(["behavior"], failure["details"]["checks"])
        self.assertIn("operation:" + str(operation), failure["details"]["references"])
        self.assertEqual([], self.requests)

    def test_report_capture_is_current_document_provenance_not_executed_measurement(self):
        report = self.report("Design deliverable: requested behavior and its verification are defined.")
        self.capture(report, "a" * 40)
        self.gate.require("job", job=self.job, revision="a" * 40)
        proof = self.requests[0][0]["proof"][0]
        self.assertEqual("report_capture", proof["kind"])
        self.assertTrue(proof["current_source"])
        self.assertIn("not execution", self.requests[0][1]["done_when"]["instructions"]["rules"][-1])

    def test_reused_artifact_selects_only_the_current_reports_capture(self):
        first = self.report()
        self.capture(first, "a" * 40)
        with self.assertRaisesRegex(ValueError, "current source revision"):
            self.gate.require("job", job=self.job, revision="b" * 40)
        second = self.api.call("work_report", {"kind": "result", "text": "Feature verified again", "evidence": {
            "name": "proof.txt", "content": "Measured requested behavior: expected Works, observed Works."}})
        self.assertEqual(first["evidence"], second["evidence"])
        self.capture(second, "b" * 40)
        self.gate.require("job", job=self.job, revision="b" * 40)
        self.assertEqual("b" * 40, self.requests[0][0]["proof"][0]["source_revision"])

    def test_actual_report_capture_rebinds_identical_proof_after_saved_source_changes(self):
        root = WorkspaceFixture.prepared(self)
        before = git(root, "rev-parse", "HEAD").stdout.decode().strip()
        first = self.report("Current design deliverable defines expected feature behavior.")
        self.gate.require("job", job=self.job, revision=before)
        (root / "src/Example.php").write_text("Implemented and verified behavior\n")
        self.api.call("work_save", {"note": "Implement behavior"})
        after = git(root, "rev-parse", "HEAD").stdout.decode().strip()
        second = self.report("Current design deliverable defines expected feature behavior.")
        self.assertNotEqual(first["reference"], second["reference"])
        self.assertEqual(first["evidence"], second["evidence"])
        self.gate.require("job", job=self.job, revision=after)
        proof = self.requests[-1][0]["proof"][0]
        self.assertEqual(after, proof["source_revision"])
        self.assertTrue(proof["workspace_clean"])
        self.assertEqual("report_capture", proof["kind"])

    def test_unrelated_capture_or_dirty_capture_cannot_make_proof_current(self):
        report = self.report()
        self.capture(report, "a" * 40)
        foreign = {**report, "reference": "update:999999"}
        self.capture(foreign, "b" * 40)
        with self.assertRaisesRegex(ValueError, "current source revision"):
            self.gate.require("job", job=self.job, revision="b" * 40)
        with self.store.connect() as db:
            db.execute("DELETE FROM artifact_sources")
        self.capture(report, "b" * 40, clean=False)
        with self.assertRaisesRegex(ValueError, "current source revision"):
            self.gate.require("job", job=self.job, revision="b" * 40)
        self.assertEqual([], self.requests)

    def test_dirty_measurement_and_mismatched_ci_revision_block(self):
        self.report()
        self.operation(uncommitted=["src/Example.php"])
        with self.assertRaisesRegex(Failure, "saved source") as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("source_unsaved", failure_info(blocked.exception)["reason"])
        with self.store.connect() as db:
            db.execute("DELETE FROM operations")
        with self.assertRaisesRegex(Failure, "ci evidence for the current") as blocked:
            self.gate.require("job", job=self.job, revision="a" * 40, mechanical={"ci": {"passed": True, "revision": "b" * 40}})
        self.assertEqual("proof_stale", failure_info(blocked.exception)["reason"])

    def test_same_named_checks_in_two_owned_jobs_remain_independent(self):
        self.report()
        self.operation(passed=False)
        other = self.xo.work.job_create(title="Other", objective="Other", done_when="Works", constraints="Product", work_order=self.order)["job"]["id"]
        self.operation(passed=True, job=other)
        with self.assertRaisesRegex(Failure, "job " + str(self.job)) as blocked:
            self.gate.require("work_order")
        details = failure_info(blocked.exception)["details"]
        self.assertIsNone(details["job"])
        self.assertEqual([], details["checks"])
        self.assertTrue(any(ref.startswith("operation:") for ref in details["references"]))

    def test_job_and_assignment_secrets_from_independent_xo_are_never_sent(self):
        self.report()
        self.xo.control.bind("docs", "xo", self.order, reports_to="co")
        other = API(self.state, "docs").work.job_create(title="Sibling secret", objective="Private objective", done_when="Private criterion", work_order=self.order)["job"]["id"]
        # A foreign job's operator override cannot grant the caller authority.
        self.configure({}, jobs={str(other): {"features": {"completion": False}}})
        with self.assertRaisesRegex(Failure, "outside current work") as blocked:
            self.gate.require("job", job=other)
        self.assertEqual("not_permitted", failure_info(blocked.exception)["category"])
        self.assertEqual("scope_not_permitted", failure_info(blocked.exception)["reason"])
        self.assertEqual({}, failure_info(blocked.exception)["details"])
        with self.assertRaisesRegex(ValueError, "independent command teams"):
            self.gate.require("work_order")
        self.assertEqual([], self.requests)
        self.assertNotIn("Sibling secret", json.dumps(self.store.events(kind="completion_blocked")))

    def test_jev_unavailable_keeps_boundary_closed_and_retains_failure_event(self):
        self.report()
        self.configure({"decisions": {"failure_mode": "block"}})
        self.provider.side_effect = JevUnavailable("Jev temporarily unavailable")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.gate.require("job", job=self.job)
        self.assertEqual("unavailable", self.store.events(kind="jev_evaluation")["events"][0]["data"]["outcome"])
        self.assertEqual(1, len(self.store.events(kind="completion_blocked")["events"]))

    def test_invalid_resolved_decisions_never_accept(self):
        proof = self.report()
        for decision in (None, 1, 0, "true", {}, []):
            result = {"id": 1, "evidence": proof["evidence"], "decisions": dict.fromkeys(("objective", "done_when", "constraints"), True)}
            result["decisions"]["objective"] = decision
            with self.subTest(decision=decision), patch("devtools.completion.Decisions.select", return_value=result), self.assertRaisesRegex(Failure, "invalid.*decision") as blocked:
                self.gate.require("job", job=self.job)
            self.assertEqual("evaluation_invalid", failure_info(blocked.exception)["category"])
        with patch("devtools.completion.Decisions.select", return_value={"evidence": proof["evidence"], "decisions": {}}), self.assertRaisesRegex(ValueError, "incomplete"):
            self.gate.require("job", job=self.job)

    def test_owner_change_during_jev_cannot_accept_stale_proof(self):
        self.report()

        def changed(state, questions, model):
            with self.store.connect() as db:
                db.execute("UPDATE work_orders SET objective='New owner goal' WHERE id=?", (self.order,))
            return answer(state, questions, model)

        self.provider.side_effect = changed
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("decision_stale", failure_info(blocked.exception)["reason"])

    def test_acceptance_receipt_rechecks_owner_goal_and_operator_policy(self):
        self.report()
        receipt = self.gate.require("job", job=self.job)
        self.gate.verify(receipt)
        with self.store.connect() as db:
            db.execute("UPDATE work_orders SET done_when='Recovery must be measured' WHERE id=?", (self.order,))
        with self.assertRaisesRegex(ValueError, "changed after assessment"):
            self.gate.verify(receipt)
        receipt = self.gate.require("job", job=self.job)
        self.configure(completion_rule(0.8, 0.9))
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.gate.verify(receipt)

    def test_changed_result_changes_assessment_cache_and_raw_retained_input(self):
        self.report()
        first = self.gate.require("job", job=self.job)
        self.report("Measured feature and recovery with new evidence.")
        second = self.gate.require("job", job=self.job)
        self.assertNotEqual(first["evidence"], second["evidence"])
        content, offset = [], 0
        while offset is not None:
            page = self.xo.call("work_context", {"reference": second["evidence"], "offset": offset})
            content.append(page["content"])
            offset = page["next_offset"]
        retained = json.loads("".join(content))
        self.assertEqual("completion", retained["purpose"])
        self.assertIn("questions", retained["request"])
        self.assertEqual(2, len(self.requests))

    def test_disabled_receipt_detects_enablement_before_acceptance(self):
        self.configure({"features": {"completion": False}})
        receipt = self.gate.require("job", job=self.job)
        self.gate.verify(receipt)
        self.configure({})
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.gate.verify(receipt)

    def test_target_job_override_wins_and_invalidates_receipt_after_change(self):
        self.configure(completion_rule(0.5, 0.6),
                       roles={"xo": completion_rule(0.7, 0.8)},
                       jobs={str(self.job): completion_rule(0.98, 0.99)})
        self.report()
        with self.assertRaisesRegex(ValueError, "stronger current proof"):
            self.gate.require("job", job=self.job)
        self.configure(completion_rule(0.5, 0.6),
                       jobs={str(self.job): completion_rule(0.8, 0.9)})
        receipt = self.gate.require("job", job=self.job)
        self.assertEqual(resolve(self.store, role="xo", job=self.job)["revision"], receipt["_configuration"])
        self.configure(completion_rule(0.5, 0.6),
                       jobs={str(self.job): completion_rule(0.98, 0.99)})
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.gate.verify(receipt)

    def test_assignment_policy_uses_trusted_assignment_job(self):
        assignment = self.assignment()
        self.configure({}, jobs={str(self.job): completion_rule(0.98, 0.99)})
        self.report()
        with self.assertRaisesRegex(ValueError, "stronger current proof"):
            self.gate.require("assignment", assignment=assignment)

    def test_configuration_change_during_assessment_blocks_acceptance(self):
        self.report()

        def changed(state, questions, model):
            self.configure({}, jobs={str(self.job): completion_rule(0.98, 0.99)})
            return answer(state, questions, model)

        self.provider.side_effect = changed
        with self.assertRaisesRegex(ValueError, "configuration changed|changed during assessment"):
            self.gate.require("job", job=self.job)


class CompletionPolicyTests(unittest.TestCase):
    def test_shared_configuration_preserves_required_boundary_fields(self):
        value = defaults()
        self.assertEqual({"objective", "done_when", "constraints"}, set(value["completion"]["questions"]))
        del value["completion"]["questions"]["constraints"]
        with self.assertRaises(ValueError):
            validate(value)
