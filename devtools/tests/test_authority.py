"""Role authority stays enforced below the managed MCP catalogue."""

import unittest
from unittest.mock import patch

from fixtures.commands import GOAL, CommandFixture, responsibility
from support import adopt_plan

from devtools.api import API
from devtools.delivery import Delivery
from devtools.orders import Orders


class AuthorityTests(unittest.TestCase):
    setUp = CommandFixture.setUp
    team = CommandFixture.team

    def retained_state(self):
        with self.store.connect() as db:
            return {table: [tuple(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                    for table in ("member_bindings", "members", "jobs", "work_orders", "messages", "work_updates",
                                  "inspections", "order_inspections", "order_deliveries", "deliveries")}

    def test_co_execution_calls_are_denied_without_changing_work_or_delivery(self):
        self.team()
        co = self.co
        scope = co.control.snapshot()
        before = self.retained_state()
        for name, args in (
            ("conclude", {"assessment": "Accept"}),
            ("restart_xo", {"reason": "Replace"}),
            ("steer", {"action": "pause", "work": "Implementation"}),
            ("orchestrate", {"work": [responsibility()]}),
            ("work_assess", {"proposal": "Accept"}),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "not available"):
                co.call(name, args)
            self.assertEqual(before, self.retained_state())
        with patch("devtools.orders.GitHub") as provider:
            calls = (
                lambda: co.commands.conclude("Accept"),
                lambda: co.commands.restart_xo("Replace"),
                lambda: co.commands.steer("pause", work="Implementation"),
                lambda: co.commands.monitor(section="review"),
                lambda: co.commands.monitor(work="Implementation"),
                lambda: Orders(self.store, "owner").finish(scope),
                lambda: Orders(self.store, "owner").inspect(),
                lambda: Delivery(self.store, "owner").inspect(job=1),
            )
            for call in calls:
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual(before, self.retained_state())
            provider.assert_not_called()
        self.assertEqual("Prepare local development", co.call("monitor", {})["work"]["title"])

    def test_owner_context_reaches_xo_without_directing_specialists(self):
        coder = self.team()
        report = coder.call("work_report", {"text": "Specialist question", "evidence": {
            "name": "facts.md", "content": "Measured implementation facts"}})
        with self.store.connect() as db:
            last = db.execute("SELECT max(id) FROM messages").fetchone()[0]
        for reference in (report["reference"], report["evidence"]):
            relayed = self.co.call("work_report", {"text": "Owner supplied clarification", "responding_to": reference})
            with self.store.connect() as db:
                update = db.execute("SELECT * FROM work_updates WHERE id=?", (int(relayed["reference"].split(":")[1]),)).fetchone()
                message = db.execute("SELECT * FROM messages WHERE id=?", (update["message"],)).fetchone()
            self.assertEqual(("owner", "xo", None), (message["sender"], message["recipient"], message["job"]))
            self.assertIn("Context: " + reference, message["body"])
        self.co.call("direct", GOAL | {"constraints": "Owner's revised boundary"})
        with self.store.connect() as db:
            directions = list(db.execute("SELECT sender,recipient FROM messages WHERE id>? AND category='direction'", (last,)))
        self.assertEqual([("owner", "xo")], [tuple(row) for row in directions])
        self.assertEqual("Owner's revised boundary", coder.call("work_context", {})["work"]["constraints"])
        with self.assertRaises(ValueError):
            self.co.call("work_report", {"text": "Unrelated reference", "responding_to": "update:999999"})

    def test_legacy_co_job_binding_does_not_expose_execution_tools(self):
        self.team()
        with self.store.connect() as db:
            job = db.execute("SELECT id FROM jobs").fetchone()[0]
            db.execute("UPDATE member_bindings SET job=? WHERE member='owner'", (job,))
        with patch("devtools.api.project_config", return_value={"configured": True}), \
                patch("devtools.api.development_configured", return_value=True), \
                patch("devtools.api.advice_configured", return_value=True):
            self.assertEqual({"work_context", "work_report", "guidance_request", "onboard", "skill_read", "readiness_report", "direct", "monitor", "roll_call"},
                             {row["name"] for row in self.co.tools()})

    def test_xo_superior_steering_retains_real_sender_and_nco_ownership(self):
        self.co.call("direct", GOAL)
        self.xo = API(self.state, "xo")
        jobs = [{"name": name, "objective": name, "done_when": "Done", "constraints": "Bounded"}
                for name in ("Alpha", "Beta")]
        adopt_plan(self.xo, jobs)
        self.xo.call("orchestrate", {"work": [{"name": name, "job": name, "role": "nco"} for name in ("Alpha", "Beta")]})
        with self.store.connect() as db:
            nco_member = db.execute("SELECT b.member FROM member_bindings b JOIN jobs j ON j.id=b.job WHERE b.role='nco' AND j.title='Alpha'").fetchone()[0]
        nco = API(self.state, nco_member)
        nco.call("orchestrate", {"work": [responsibility("Implement") | {"job": "Alpha"}]})
        with self.store.connect() as db:
            specialist = db.execute("SELECT member FROM member_bindings WHERE reports_to=?", (nco_member,)).fetchone()[0]
        nco.call("steer", {"action": "guide", "work": "Alpha", "assignment": "Implement", "direction": "NCO direction"})
        self.xo.call("steer", {"action": "guide", "work": "Alpha", "assignment": "Implement", "direction": "XO superior direction"})
        with self.store.connect() as db:
            messages = list(db.execute("SELECT sender FROM messages WHERE recipient=? AND category='direction' ORDER BY id DESC LIMIT 2", (specialist,)))
            parent = db.execute("SELECT reports_to FROM member_bindings WHERE member=?", (specialist,)).fetchone()[0]
        self.assertEqual(["xo", nco_member], [row["sender"] for row in messages])
        self.assertEqual(nco_member, parent)
        self.assertEqual("XO superior direction", API(self.state, specialist).call("work_context", {})["responsibility"])
        with self.assertRaises(ValueError):
            nco.call("steer", {"action": "guide", "work": "Beta", "direction": "Cross job"})

    def test_planner_reports_references_to_xo_and_cannot_reach_specialists(self):
        coder = self.team()
        update = coder.call("work_report", {"text": "Specialist findings"})
        self.xo.call("orchestrate", {"work": [responsibility("Follow-up plan", "planner")]})
        with self.store.connect() as db:
            member = db.execute("SELECT member FROM member_bindings WHERE role='planner'").fetchone()[0]
        planner = API(self.state, member)
        result = planner.call("work_report", {"text": "Planning context", "responding_to": update["reference"]})
        with self.store.connect() as db:
            message = db.execute("SELECT m.* FROM messages m JOIN work_updates u ON u.message=m.id WHERE u.id=?",
                                 (int(result["reference"].split(":")[1]),)).fetchone()
        self.assertEqual("xo", message["recipient"])
        self.assertIsNone(message["job"])
        self.assertIn(update["reference"], message["body"])


if __name__ == "__main__":
    unittest.main()
