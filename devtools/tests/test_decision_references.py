"""Arbitration packets delegate exact evidence within the caller's authority."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools.api import API
from devtools.decisions import Decisions
from devtools.launch import initialize


class DecisionReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        self.state = root / "state"
        initialize(self.state, workspace, ["owner:external", "xo:external", "investigator:external"])
        self.operator = API(self.state, "owner", operator=True)
        self.store = self.operator.store
        self.order = self.operator.work.work_order_create(
            title="Integration", objective="Verify product", done_when="Current proof"
        )["work_order"]["id"]
        self.job = self.operator.work.job_create(
            title="Sibling job", objective="Other evidence", done_when="Separate proof", work_order=self.order
        )["job"]["id"]
        self.operator.control.bind("owner", "co", self.order)
        self.operator.control.bind("xo", "xo", self.order, reports_to="owner")
        self.xo = API(self.state, "xo")
        self.packet_operation = self.operation(None, "Integrated product passed")
        self.unrelated_operation = self.operation(self.job, "Unrequested sibling evidence")
        artifact = self.xo.artifacts.save(name="assessment.json", content="Ambiguous completion",
                                         work_order=self.order)["artifact"]["id"]
        outcome = {"answers": {"complete": {"type": "noul", "noul": 0.75}}, "evidence": f"artifact:{artifact}"}
        with patch("devtools.advice.Advice.evaluate", return_value=outcome):
            with self.assertRaisesRegex(ValueError, "assigned Arbitrator"):
                Decisions(self.store, "xo").select(
                    "completion", {"operations": [{"reference": self.packet_operation}]},
                    {"complete": {"type": "noul", "instructions": "Is the product complete?"}},
                    identity="current-product-proof",
                )
        with self.store.connect() as db:
            self.decision = dict(db.execute("SELECT * FROM decisions").fetchone())
        self.arbitrator = API(self.state, self.decision["arbitrator"])

    def operation(self, job, diagnostic):
        scope = self.xo.control.snapshot()
        with self.store.connect() as db:
            ident = db.execute(
                "INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,created,updated) "
                "VALUES (?,?,?,?,?,?,'completed',?,'fixture','fixture')",
                ("xo", self.order, job, json.dumps(scope), "work_check", "{}", json.dumps({"diagnostic": diagnostic})),
            ).lastrowid
        return f"operation:{ident}"

    def test_order_arbitrator_can_follow_its_exact_operation_evidence(self):
        read = self.arbitrator.call("work_context", {"reference": self.packet_operation})
        self.assertEqual("Integrated product passed", json.loads(read["content"])["result"]["diagnostic"])
        with self.assertRaisesRegex(ValueError, "not available"):
            self.arbitrator.call("work_context", {"reference": self.unrelated_operation})

    def test_packet_reference_cannot_exceed_original_callers_authority(self):
        other = self.operator.work.work_order_create(
            title="Unrelated team", objective="Private", done_when="Separate"
        )["work_order"]["id"]
        with self.store.connect() as db:
            ident = db.execute(
                "INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,created,updated) "
                "VALUES (?,?,NULL,?,?,?,'completed',?,'fixture','fixture')",
                ("xo", other, self.decision["binding"], "work_check", "{}", '{"diagnostic":"Other team"}'),
            ).lastrowid
            payload = json.loads(self.decision["request"])
            payload["state"]["operations"].append({"reference": f"operation:{ident}"})
            db.execute("UPDATE decisions SET request=? WHERE id=?", (json.dumps(payload), self.decision["id"]))
        # Even a supplied reference is data, not authority over another team.
        with self.assertRaisesRegex(ValueError, "not available"):
            self.arbitrator.call("work_context", {"reference": f"operation:{ident}"})

    def test_changed_requester_binding_revokes_delegated_read(self):
        self.operator.control.bind("xo", "xo", self.order, reports_to="owner", brief="Revised owner instruction")
        with self.assertRaisesRegex(ValueError, "changed|current"):
            self.arbitrator.call("work_context", {"reference": self.packet_operation})

    def test_rebind_during_reference_read_cannot_use_old_arbitration_packet(self):
        def rebind():
            self.operator.control.bind(self.arbitrator.member, "arbitrator", self.order,
                                       reports_to="xo", brief="New arbitration scope")

        with self.assertRaisesRegex(ValueError, "not available"):
            self.arbitrator.call("work_context", {"reference": self.packet_operation}, on_context=rebind)


if __name__ == "__main__":
    unittest.main()
