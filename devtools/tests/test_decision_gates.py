"""Real gate paths retain ambiguity, investigate it, and consume the resolution."""

import json
import unittest
from unittest.mock import patch

from fixtures import completion as test_completion
from fixtures.decision_gates import CompletionDecisionFixture
from fixtures.diagnostics import DiagnosticFixture

from devtools.api import API
from devtools.failures import Failure, failure_info
from devtools.jev import JevUnavailable


class CompletionDecisionTests(CompletionDecisionFixture, unittest.TestCase):


    def test_ambiguous_completion_is_idempotent_and_resolution_drives_the_gate(self):
        proof = self.report()
        self.provider.side_effect = self.ambiguous
        first = self.pending()
        second = self.pending()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["assignment"], second["assignment"])
        self.assertEqual(1, self.provider.call_count)
        arbitrator = API(self.state, first["arbitrator"])
        self.assertEqual("arbitrator", arbitrator.control.snapshot()["role"])
        self.assertEqual(["done_when"], json.loads(first["unresolved"]))
        self.resolve(first, proof)
        receipt = self.gate.require("job", job=self.job)
        self.assertEqual("ready", receipt["state"])
        self.assertEqual(first["id"], receipt["decision"])
        self.assertEqual(1, self.provider.call_count)
        self.gate.verify(receipt)
        with self.store.connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM decisions").fetchone()[0])
            self.assertEqual("done", db.execute("SELECT status FROM role_assignments WHERE id=?", (first["assignment"],)).fetchone()[0])

    def test_arbitrator_negative_result_blocks_completion(self):
        proof = self.report()
        self.provider.side_effect = self.ambiguous
        pending = self.pending()
        self.resolve(pending, proof, False)
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job)
        self.assertEqual("proof_insufficient", failure_info(blocked.exception)["reason"])
        self.assertEqual(["done_when"], failure_info(blocked.exception)["details"]["criteria"])
        self.assertEqual(1, self.provider.call_count)

    def test_assignment_and_work_order_arbitration_preserve_their_proof_binding(self):
        assignment = self.assignment()
        proof = self.report()
        self.provider.side_effect = self.ambiguous
        for boundary, arguments in (("assignment", {"assignment": assignment}), ("work_order", {})):
            with self.subTest(boundary=boundary):
                with self.assertRaises(Failure) as blocked:
                    self.gate.require(boundary, **arguments)
                self.assertEqual("decision_pending", failure_info(blocked.exception)["reason"])
                with self.store.connect() as db:
                    pending = dict(db.execute("SELECT * FROM decisions WHERE id=?", (
                        failure_info(blocked.exception)["details"]["decision"],
                    )).fetchone())
                self.resolve(pending, proof)
                receipt = self.gate.require(boundary, **arguments)
                self.assertEqual("ready", receipt["state"])
                self.gate.verify(receipt)

    def test_resolution_never_overrides_a_new_failed_measurement(self):
        proof = self.report()
        self.operation()
        self.provider.side_effect = self.ambiguous
        pending = self.pending(revision="a" * 40)
        self.operation(passed=False)
        self.resolve(pending, proof)
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job, revision="a" * 40)
        self.assertEqual("check_failed", failure_info(blocked.exception)["reason"])
        self.assertEqual(1, self.provider.call_count)
        with self.store.connect() as db:
            self.assertNotEqual("done", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])

    def test_resolution_never_makes_old_proof_current(self):
        proof = self.report()
        self.operation()
        self.provider.side_effect = self.ambiguous
        pending = self.pending(revision="a" * 40)
        self.resolve(pending, proof)
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job, revision="b" * 40)
        self.assertEqual("proof_stale", failure_info(blocked.exception)["reason"])
        self.assertEqual(1, self.provider.call_count)

    def test_provider_outage_investigation_supplies_a_retained_resolution(self):
        proof = self.report()
        self.provider.side_effect = JevUnavailable("Fixture outage")
        pending = self.pending()
        self.assertEqual("evaluation_unavailable", json.loads(pending["failure"])["category"])
        self.resolve(pending, proof)
        receipt = self.gate.require("job", job=self.job)
        self.assertEqual("ready", receipt["state"])
        self.assertTrue(receipt["evidence"].startswith("artifact:"))
        self.gate.verify(receipt)

    def test_arbitration_resumes_the_waiting_acceptance_without_an_agent_retry(self):
        proof = self.report()
        self.provider.side_effect = self.ambiguous
        with self.assertRaises(Failure) as blocked:
            self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Accept verified work"})
        self.assertEqual("decision_pending", failure_info(blocked.exception)["reason"])
        with self.store.connect() as db:
            pending = dict(db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone())
            self.assertNotEqual("done", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])
        result = self.resolve(pending, proof)
        self.assertEqual("completed", result["continuation"]["state"])
        with self.store.connect() as db:
            self.assertEqual("done", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM deliveries WHERE job=?", (self.job,)).fetchone()[0])

    def test_source_assessment_outage_can_resume_with_an_arbitrator_verdict(self):
        self.configure({"features": {"advice": True, "filtering": False, "completion": False}})
        proof = self.report()
        self.provider.side_effect = JevUnavailable("Fixture outage")
        arguments = {"sources": [{"reference": proof["evidence"]}], "questions": [{
            "name": "match", "instructions": "Does the observed output match the expected behavior?",
        }]}
        with self.assertRaises(Failure) as blocked:
            self.api.call("work_assess", arguments)
        self.assertEqual("decision_pending", failure_info(blocked.exception)["reason"])
        with self.store.connect() as db:
            pending = dict(db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone())
        result = self.resolve(pending, proof)
        self.assertEqual("completed", result["continuation"]["state"])
        self.assertEqual({"s0_q0": True}, result["continuation"]["result"]["decisions"])
        self.assertEqual({"s0_q0": True}, self.api.call("work_assess", arguments)["decisions"])
        self.assertEqual(1, self.provider.call_count)

    def test_arbitration_starts_when_capacity_returns_without_an_agent_retry(self):
        self.report()
        self.xo.control.bind("docs", "research", self.order, self.job, "xo")
        self.provider.side_effect = self.ambiguous
        with self.assertRaises(ValueError):
            self.gate.require("job", job=self.job)
        with self.store.connect() as db:
            pending = dict(db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone())
            self.assertEqual("pending", pending["state"])
            self.assertIsNone(pending["arbitrator"])
        self.xo.control.unbind("docs")
        self.xo.commands.tick()
        with self.store.connect() as db:
            assigned = dict(db.execute("SELECT * FROM decisions WHERE id=?", (pending["id"],)).fetchone())
            self.assertIsNotNone(assigned["arbitrator"])
            self.assertEqual(1, db.execute("SELECT count(*) FROM role_assignments WHERE status='active'").fetchone()[0])
        self.assertEqual(1, self.provider.call_count)


class ContextDecisionTests(unittest.TestCase):
    setUp = DiagnosticFixture.setUp
    setUp_base = DiagnosticFixture.setUp_base
    client = DiagnosticFixture.client
    configure = DiagnosticFixture.configure
    report = DiagnosticFixture.report
    context = DiagnosticFixture.context

    def test_pending_preserves_facts_then_arbitration_controls_projection(self):
        fact = self.report("Unrelated color preference")
        context = self.context()

        def ambiguous(state, questions, model):
            response = test_completion.answer(state, questions, model)
            response["answers"] = {name: {"type": "noul", "noul": 0.2} for name in questions}
            return response

        with patch("devtools.advice.Jev.evaluate", side_effect=ambiguous) as provider:
            self.assertEqual(context, self.diagnostics.project(context))
            with self.store.connect() as db:
                pending = dict(db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone())
            self.assertEqual("pending", pending["state"])
            self.assertIsNotNone(pending["arbitrator"])
            API(self.state, pending["arbitrator"]).call("decision_resolve", {
                "decisions": {"fact_0": False}, "reason": "The preference is unrelated to this job's requirements.",
                "evidence": [fact["reference"]],
            })
            self.assertEqual([], self.diagnostics.project(context)["recent_activity"])
            self.assertEqual(1, provider.call_count)
        self.assertEqual("Unrelated color preference", self.api.call("work_context", {"reference": fact["reference"]})["text"])

    def test_arbitrator_context_never_recursively_requests_a_selection(self):
        self.api.control.bind("idle", "arbitrator", self.order, self.job, "xo")
        arbitrator = API(self.state, "idle")
        self.report("Background to investigate", member="idle")
        context = arbitrator.control.work_context(project=False)
        with patch("devtools.advice.Jev.evaluate") as provider:
            self.assertEqual(context, arbitrator.control.work_context())
        provider.assert_not_called()

    def test_new_context_facts_replace_the_obsolete_pending_investigation(self):
        self.report("Earlier background preference")

        def ambiguous(state, questions, model):
            response = test_completion.answer(state, questions, model)
            response["answers"] = {name: {"type": "noul", "noul": 0.2} for name in questions}
            return response

        with patch("devtools.advice.Jev.evaluate", side_effect=ambiguous):
            self.diagnostics.project(self.context())
            self.report("Later background preference")
            current = self.context()
            self.assertEqual(current, self.diagnostics.project(current))
        with self.store.connect() as db:
            decisions = [dict(row) for row in db.execute("SELECT * FROM decisions ORDER BY id")]
            self.assertEqual(["stale", "pending"], [row["state"] for row in decisions])
            self.assertIsNotNone(decisions[-1]["arbitrator"])
            self.assertEqual(1, db.execute("SELECT count(*) FROM role_assignments WHERE status='active'").fetchone()[0])

    def test_restoring_policy_restarts_its_previously_superseded_investigation(self):
        self.report("Background preference")
        context = self.context()
        path = self.state / "configuration.json"
        original = path.read_text()
        changed = json.loads(original)
        changed["project"]["decisions"] = {"rules": {"context_relevance": {"noul": {"accept_at": 0.4}}}}

        def ambiguous(state, questions, model):
            response = test_completion.answer(state, questions, model)
            response["answers"] = {name: {"type": "noul", "noul": 0.2} for name in questions}
            return response

        with patch("devtools.advice.Jev.evaluate", side_effect=ambiguous):
            self.diagnostics.project(context)
            path.write_text(json.dumps(changed))
            self.diagnostics.project(context)
            path.write_text(original)
            self.assertEqual(context, self.diagnostics.project(context))
        with self.store.connect() as db:
            pending = db.execute("SELECT * FROM decisions WHERE state='pending'").fetchall()
            self.assertEqual(1, len(pending))
            self.assertIsNotNone(pending[0]["arbitrator"])
            self.assertEqual(1, db.execute("SELECT count(*) FROM role_assignments WHERE status='active'").fetchone()[0])
