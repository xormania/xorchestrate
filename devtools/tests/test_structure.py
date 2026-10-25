import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from fixtures.commands import GOAL, responsibility
from fixtures.structure import StructureFixture, job
from support import Client, adopt_plan, fixture_work

from devtools.api import API
from devtools.store import Store


class StructureTests(StructureFixture, unittest.TestCase):


    def test_one_job_contains_several_roles_and_only_xo_accepts(self):
        adopted, plan = adopt_plan(self.xo, [job("Feature")])
        self.assertEqual("xo", adopted["coordination"])
        with self.store.connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM jobs").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM member_bindings WHERE role='planner'").fetchone()[0])
        coder = self.assign(self.xo, "coder", "Feature")
        researcher = self.assign(self.xo, "research", "Feature")
        self.assertEqual(coder.control.snapshot()["job"], researcher.control.snapshot()["job"])
        self.assertIn("Proposed job", coder.call("work_context", {"reference": plan["reference"]})["text"])
        with self.assertRaisesRegex(ValueError, "editing assignment"):
            self.assign(self.xo, "test_writer", "Feature")
        with self.assertRaisesRegex(ValueError, "single job"):
            self.assign(self.xo, "nco", "Feature")
        self.complete(self.xo, coder, "Feature", "coder")
        tester = self.assign(self.xo, "test_writer", "Feature")
        self.complete(self.xo, tester, "Feature", "test_writer")
        with self.assertRaisesRegex(ValueError, "Complete this job"):
            self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Too early"})
        self.complete(self.xo, researcher, "Feature", "research")
        receipt = self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Verified all contributions"})
        self.assertEqual("done", receipt["outcome"])
        self.assertEqual(1, len(self.xo.call("monitor", {})["jobs"]))

    def test_each_job_has_its_own_nco_and_private_context(self):
        _, plan = adopt_plan(self.xo, [job("Alpha"), job("Beta")])
        alpha = self.assign(self.xo, "nco", "Alpha")
        beta = self.assign(self.xo, "nco", "Beta")
        self.assertNotEqual(alpha.member, beta.member)
        self.assertNotEqual(alpha.control.snapshot()["job"], beta.control.snapshot()["job"])
        with self.assertRaisesRegex(ValueError, "NCO"):
            self.assign(self.xo, "coder", "Alpha")
        for nco, work in ((alpha, "Alpha"), (beta, "Beta")):
            for request, message in ((responsibility("Plan", "planner"), "Only XO"),
                                     ({"name": "nco", "job": work, "role": "nco"}, "XO appoints")):
                # The MCP schema rejects these roles early; direct service calls
                # must still enforce the same authority before creating work.
                with self.assertRaisesRegex(ValueError, r"arguments.work\[\].role is not an allowed value"):
                    nco.call("orchestrate", {"work": [request]})
                with self.assertRaisesRegex(ValueError, message):
                    nco.commands.orchestrate([request])
            with self.assertRaisesRegex(ValueError, "command role"):
                nco.call("steer", {"action": "accept", "work": work, "direction": "Self-accept"})
            with self.assertRaisesRegex(ValueError, "cannot perform"):
                nco.delivery.finish_job(nco.control.snapshot()["job"], "Bypass normal surface")
        a = self.assign(alpha, "coder", "Alpha")
        b = self.assign(beta, "coder", "Beta")
        secret = b.call("work_report", {"text": "BETA PRIVATE", "evidence": {"name": "beta.txt", "content": "BETA PRIVATE"}})
        for observer in (alpha, a):
            for reference in (secret["reference"], secret["evidence"]):
                with self.assertRaisesRegex(ValueError, "not available"):
                    observer.call("work_context", {"reference": reference})
            for section in ("brief", "activity", "messages", "evidence"):
                self.assertNotIn("BETA PRIVATE", json.dumps(observer.call("work_context", {"section": section})))
            self.assertEqual(plan["evidence"], observer.call("work_context", {"reference": plan["evidence"]})["reference"])
        with self.assertRaisesRegex(ValueError, "unique responsibility"):
            self.assign(alpha, "research", "Beta")
        question = a.call("guidance_request", {"text": "Need a decision"})
        self.assertIn(question["reference"], json.dumps(alpha.call("work_context", {"section": "activity"})))
        self.complete(alpha, a, "Alpha", "coder")
        with self.assertRaisesRegex(ValueError, "report the checked job"):
            self.xo.call("steer", {"action": "accept", "work": "Alpha", "direction": "NCO has not checked"})
        alpha.call("work_report", {"kind": "result", "text": "Alpha is checked and ready"})
        self.assertEqual("done", self.xo.call("steer", {"action": "accept", "work": "Alpha", "direction": "Accepted Alpha"})["outcome"])
        self.assertIsNotNone(beta.control.snapshot())

    def test_retries_and_restarts_keep_plan_assignments_and_ncos(self):
        definitions = [job("Alpha"), job("Beta")]
        first, report = adopt_plan(self.xo, definitions)
        self.assertEqual(first, API(self.state, "xo").call("plan_adopt", {"plan": report["reference"], "jobs": definitions}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: API(self.state, "xo").call("orchestrate", {
                "work": [{"name": "Coordinate Alpha", "job": "Alpha", "role": "nco"}]}), range(2)))
        self.assertEqual(results[0], results[1])
        alpha = self.member("nco", "Alpha")
        coder = self.assign(alpha, "coder", "Alpha")
        before = coder.control.snapshot()
        alpha.call("orchestrate", {"work": [{"name": "coder", "job": "Alpha", "role": "coder"}]})
        self.assertEqual(before, coder.control.snapshot())
        retained = self.complete(alpha, coder, "Alpha", "coder")
        again = API(self.state, alpha.member).call("steer", {"action": "complete", "work": "Alpha", "assignment": "coder", "direction": "Retry"})
        self.assertTrue(again["retained"])
        self.assertEqual(retained["reference"], alpha.call("work_context", {"reference": retained["reference"]})["reference"])

    def test_specification_is_explicit_shared_context_and_invalidates_stale_plan(self):
        document = self.co.call("work_report", {"text": "Prepared specification", "evidence": {"name": "spec.md", "content": "Scope from external research"}})
        self.co.call("direct", {**GOAL, "context": [document["evidence"]]})
        _, plan = adopt_plan(self.xo, [job("Feature")])
        coder = self.assign(self.xo, "coder", "Feature")
        self.assertEqual([document["evidence"]], coder.call("work_context", {})["context"])
        self.assertEqual("Scope from external research", coder.call("work_context", {"reference": document["evidence"]})["content"])
        self.co.call("direct", {**GOAL, "constraints": "Revised boundaries"})
        with self.assertRaisesRegex(ValueError, "context changed"):
            self.assign(self.xo, "research", "Feature")
        with self.assertRaisesRegex(ValueError, "current retained result"):
            self.xo.call("plan_adopt", {"plan": plan["reference"], "jobs": [job("Feature")]})

    def test_replanning_changes_coordination_without_changing_job_or_assignment_identity(self):
        _, report = adopt_plan(self.xo, [job("Alpha")])
        coder = self.assign(self.xo, "coder", "Alpha")
        before = coder.control.snapshot()
        evidence = coder.call("work_report", {"text": "Work in progress", "evidence": {"name": "finding.md", "content": "Retain this"}})
        self.xo.call("plan_adopt", {"plan": report["reference"], "jobs": [job("Alpha"), job("Beta")]})
        self.assertTrue(coder.control.snapshot()["paused"])
        with self.assertRaisesRegex(ValueError, "coordinator"):
            self.xo.call("steer", {"action": "resume", "work": "Alpha"})
        nco = self.assign(self.xo, "nco", "Alpha")
        after = coder.control.snapshot()
        self.assertEqual(before["job"], after["job"])
        self.assertEqual(before["assignment_id"], after["assignment_id"])
        self.assertEqual(nco.member, after["reports_to"])
        self.assertEqual("Retain this", nco.call("work_context", {"reference": evidence["evidence"]})["content"])
        nco.call("steer", {"action": "resume", "work": "Alpha"})
        self.assertFalse(coder.control.snapshot()["paused"])

    def test_dependencies_cycles_and_foreign_references_fail_atomically(self):
        definitions = [job("Alpha"), job("Beta", dependencies=["Alpha"])]
        _, report = adopt_plan(self.xo, definitions)
        alpha, beta = self.assign(self.xo, "nco", "Alpha"), self.assign(self.xo, "nco", "Beta")
        with self.assertRaisesRegex(ValueError, "dependency"):
            self.assign(beta, "research", "Beta")
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.xo.call("plan_adopt", {"plan": report["reference"], "jobs": [job("Alpha", dependencies=["Beta"]), job("Beta", dependencies=["Alpha"])]})
        self.assertEqual(1, self.xo.call("work_context", {})["plan_revision"])
        researcher = self.assign(alpha, "research", "Alpha")
        self.complete(alpha, researcher, "Alpha", "research")
        alpha.call("work_report", {"kind": "result", "text": "Alpha ready"})
        self.xo.call("steer", {"action": "accept", "work": "Alpha", "direction": "Dependency accepted"})
        self.assign(beta, "research", "Beta")

    def test_replanning_resumes_one_nco_before_refreshing_its_specialists(self):
        definitions = [job("Alpha"), job("Beta")]
        _, old_plan = adopt_plan(self.xo, definitions)
        alpha = self.assign(self.xo, "nco", "Alpha")
        beta = self.assign(self.xo, "nco", "Beta")
        coder = self.assign(alpha, "coder", "Alpha")
        _, new_plan = adopt_plan(self.xo, definitions)
        self.assertNotEqual(old_plan["reference"], new_plan["reference"])

        resumed = self.xo.call("steer", {"action": "resume", "work": "Alpha", "role": "nco"})
        self.assertEqual(["nco"], [row["role"] for row in resumed["work"]])
        self.assertFalse(alpha.control.snapshot()["paused"])
        self.assertTrue(beta.control.snapshot()["paused"])
        self.assertTrue(coder.control.snapshot()["paused"])
        with self.assertRaisesRegex(ValueError, "job plan changed"):
            alpha.call("steer", {"action": "resume", "work": "Alpha", "assignment": "coder"})
        alpha.call("orchestrate", {"work": [{"name": "coder", "job": "Alpha", "role": "coder"}]})
        self.assertEqual(new_plan["reference"], coder.call("work_context", {})["assignment"]["plan"]["reference"])
        alpha.call("steer", {"action": "resume", "work": "Alpha", "assignment": "coder"})
        self.assertFalse(coder.control.snapshot()["paused"])

        # Pausing/stopping an NCO still stops its team, while an unfiltered
        # job resume restarts both the coordinator and its current specialists.
        self.xo.call("steer", {"action": "pause", "work": "Alpha", "role": "nco"})
        self.assertTrue(coder.control.snapshot()["paused"])
        resumed = self.xo.call("steer", {"action": "resume", "work": "Alpha"})
        self.assertEqual({"nco", "coder"}, {row["role"] for row in resumed["work"]})
        self.assertFalse(alpha.control.snapshot()["paused"])
        self.assertFalse(coder.control.snapshot()["paused"])
        self.xo.call("steer", {"action": "stop", "work": "Alpha", "role": "nco"})
        self.assertTrue(alpha.control.snapshot()["paused"])
        self.assertTrue(coder.control.snapshot()["paused"])
        self.assertTrue(beta.control.snapshot()["paused"])

    def test_real_stdio_exposes_plan_contract_and_rejects_git_parameters(self):
        client = Client(self.state, "xo")
        self.addCleanup(client.close)
        tools = client.call("tools/list")["tools"]
        self.assertIn("plan_adopt", {row["name"] for row in tools})
        client.tool("orchestrate", {"work": [responsibility("Planning", "planner")]})
        planner = self.member("planner")
        result = planner.call("work_report", {"kind": "result", "text": "Ready", "evidence": {"name": "plan.md", "content": "One Feature job"}})
        adopted = client.tool("plan_adopt", {"plan": result["reference"], "jobs": [job("Feature")]})
        self.assertEqual("xo", adopted["coordination"])
        for forbidden in ("branch", "base", "remote", "repository"):
            with self.assertRaises(ValueError):
                client.tool("orchestrate", {"work": [{"name": "Code", "job": "Feature", "role": "coder", forbidden: "agent-choice"}]})

    def test_unsupported_jobless_coordinator_requires_explicit_repair_and_retains_history(self):
        fixture_work(self.xo, [responsibility("Alpha", "coder")])
        coder = self.member("coder", "Alpha")
        before = coder.control.snapshot()
        retained = coder.call("work_report", {"text": "Existing work", "evidence": {"name": "history.md", "content": "Keep the evidence"}})
        self.xo.control.bind("slot8", "nco", before["work_order"], before["job"], "xo")
        with self.store.connect() as db:
            db.execute("UPDATE member_bindings SET job=NULL WHERE member='slot8'")
            db.execute("PRAGMA user_version=12")
            saved = dict(db.execute("SELECT * FROM member_bindings WHERE member='slot8'").fetchone())
        with self.assertRaisesRegex(ValueError, "Rebind or retire"):
            Store(self.state)
        with self.store.connect() as db:
            self.assertEqual(12, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(saved, dict(db.execute("SELECT * FROM member_bindings WHERE member='slot8'").fetchone()))
            self.assertEqual(1, db.execute("SELECT count(*) FROM jobs").fetchone()[0])
        # The trusted existing handle can repair the exact binding explicitly.
        self.xo.control.bind("slot8", "nco", before["work_order"], before["job"], "xo")
        self.store = Store(self.state)
        self.assertEqual("Keep the evidence", coder.call("work_context", {"reference": retained["evidence"]})["content"])

    def test_only_xo_concludes_after_planning_and_planner_is_read_only(self):
        self.xo.call("orchestrate", {"work": [responsibility("Plan", "planner")]})
        planner = self.member("planner")
        self.assertIsNone(planner.control.snapshot()["job"])
        forbidden = {"orchestrate", "steer", "plan_adopt", "framework_generate", "work_save", "work_submit"}
        self.assertFalse(forbidden & {tool["name"] for tool in planner.tools()})
        with self.assertRaisesRegex(ValueError, "not available"):
            self.co.call("conclude", {"assessment": "Premature completion"})
        with self.assertRaisesRegex(ValueError, "assignments remain active"):
            self.xo.call("conclude", {"assessment": "Premature completion"})
        self.xo.call("steer", {"action": "cancel", "assignment": "Plan", "direction": "User withdrew planning"})
        self.assertEqual("done", self.xo.call("conclude", {"assessment": "No work remaining"})["outcome"])


if __name__ == "__main__":
    unittest.main()
