"""Coordinator uniqueness follows registered behavior, including existing-team upgrades."""

import unittest
from unittest.mock import patch

from fixtures.workspaces import WorkspaceFixture

from devtools import role_packages
from devtools.store import SCHEMA_VERSION, Store


class RoleBindingTests(unittest.TestCase):
    def setUp(self):
        WorkspaceFixture.setUp(self)
        self.scope = self.api.control.snapshot()
        self.catalog = role_packages.catalog()
        self.catalog["roles"]["alternate_coordinator"] = self.catalog["roles"]["nco"].copy()
        configured = patch("devtools.role_packages._catalog", return_value=self.catalog)
        configured.start()
        self.addCleanup(configured.stop)
        self.api.control.bind("coder", "nco", self.scope["work_order"], self.scope["job"], "xo")

    def test_two_coordinator_role_names_cannot_own_one_job(self):
        with self.assertRaisesRegex(ValueError, "already has a coordinator"):
            self.api.control.bind("docs", "alternate_coordinator", self.scope["work_order"], self.scope["job"], "xo")
        # A role change for the same trusted participant preserves uniqueness.
        self.api.control.bind("coder", "alternate_coordinator", self.scope["work_order"], self.scope["job"], "xo")
        self.assertEqual("alternate_coordinator", self.api.control.snapshot()["role"])

    def test_upgrade_removes_literal_role_index_without_losing_bindings(self):
        with self.store.connect() as db:
            db.execute("CREATE UNIQUE INDEX job_nco ON member_bindings(job) WHERE role='nco' AND job IS NOT NULL")
            db.execute("PRAGMA user_version=12")
        upgraded = Store(self.state)
        with upgraded.connect() as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='job_nco'").fetchone())
            self.assertEqual("nco", db.execute("SELECT role FROM member_bindings WHERE member='coder'").fetchone()[0])

    def test_upgrade_rejects_preexisting_duplicate_behavior_bindings(self):
        with self.store.connect() as db:
            row = dict(db.execute("SELECT * FROM member_bindings WHERE member='coder'").fetchone())
            row.update(member="docs", role="alternate_coordinator")
            db.execute("INSERT INTO member_bindings(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
            db.execute("PRAGMA user_version=12")
        with self.assertRaisesRegex(ValueError, "multiple coordinator bindings"):
            Store(self.state)
        with self.store.connect() as db:
            self.assertEqual(12, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(2, db.execute("SELECT count(*) FROM member_bindings WHERE job=?", (self.scope["job"],)).fetchone()[0])


class RenamedWorkflowTests(unittest.TestCase):
    def test_entire_workflow_uses_configured_role_identities(self):
        import json
        import tempfile
        from pathlib import Path

        from support import initialize

        from devtools.api import API
        from devtools.configuration import defaults

        settings = defaults()
        catalog = role_packages.catalog()
        names = {name: "custom_" + name for name in catalog["roles"]}
        catalog["roles"] = {names[name]: value for name, value in catalog["roles"].items()}
        catalog["defaults"] = {kind: names[name] for kind, name in catalog["defaults"].items()}
        for group in ("routes", "actions"):
            for rule in settings["guidance"][group]:
                rule["roles"] = [names[name] for name in rule["roles"]]

        def assessed(state, questions, model):
            return {"model": model, "answers": {name: {"type": "noul", "noul": 0.97} for name in questions},
                    "usage": {"input_tokens": 500, "output_tokens": 30}}

        with tempfile.TemporaryDirectory() as directory, patch("devtools.role_packages._catalog", return_value=catalog), \
                patch("devtools.advice.Jev.evaluate", side_effect=assessed) as provider:
            root = Path(directory)
            workspace = root / "source"
            workspace.mkdir()
            state = root / "state"
            initialize(state, workspace, [name + ":external" for name in ("owner", "leader", *["slot" + str(i) for i in range(7)])], co="owner")
            team = json.loads((state / "team.json").read_text())
            team["project"] = {"configuration_file": "configuration.json"}
            (state / "team.json").write_text(json.dumps(team))
            (state / "configuration.json").write_text(json.dumps({"version": 1, "project": {
                "features": {"completion": True, "selection": False, "filtering": False}, "guidance": settings["guidance"],
                "decisions": {"arbitrator_role": names["arbitrator"]}}}))
            co, xo = API(state, "owner"), API(state, "leader")
            co.call("direct", {"title": "Research", "objective": "Deliver Alpha and Beta", "done_when": "Both results demonstrated", "constraints": "Research only"})
            self.assertEqual(names["co"], co.control.snapshot()["role"])
            self.assertEqual(names["xo"], xo.control.snapshot()["role"])

            def member(role, job=None):
                with xo.store.connect() as db:
                    rows = [row for row in db.execute("SELECT * FROM member_bindings WHERE role=?", (names[role],))
                            if job is None or row["job"] == job]
                self.assertEqual(1, len(rows))
                return API(state, rows[0]["member"])

            xo.call("orchestrate", {"work": [{"name": "Plan", "role": names["planner"], "objective": "Plan both contributions",
                                             "done_when": "Complete job breakdown", "constraints": "Research only"}]})
            planner = member("planner")
            definitions = [{"name": name, "objective": "Research " + name, "done_when": "Evidence demonstrates " + name, "constraints": "Research only"}
                           for name in ("Alpha", "Beta")]
            plan = planner.call("work_report", {"kind": "result", "text": "Complete job breakdown", "evidence": {
                "name": "plan.json", "content": json.dumps(definitions)}})
            xo.call("plan_adopt", {"plan": plan["reference"], "jobs": definitions})
            self.assertIsNone(planner.control.snapshot())
            for definition in definitions:
                name = definition["name"]
                xo.call("orchestrate", {"work": [{"name": "Coordinate " + name, "job": name, "role": names["nco"]}]})
                with xo.store.connect() as db:
                    job = db.execute("SELECT id FROM jobs WHERE title=?", (name,)).fetchone()[0]
                nco = member("nco", job)
                nco.call("orchestrate", {"work": [{"name": "Investigate", "job": name, "role": names["research"]}]})
                researcher = member("research", job)
                self.assertEqual(names["research"], researcher.call("work_context", {})["role"])
                researcher.call("work_report", {"kind": "result", "text": "Research demonstrated", "evidence": {
                    "name": name + ".txt", "content": "Observed evidence supports the requested " + name + " result."}})
                nco.call("steer", {"action": "complete", "work": name, "assignment": "Investigate", "direction": "Evidence inspected"})
                nco.call("work_report", {"kind": "result", "text": name + " checked and ready"})
                result = xo.call("steer", {"action": "accept", "work": name, "direction": "Accepted demonstrated result"})
                self.assertEqual("done", result["outcome"])
            result = xo.call("conclude", {"assessment": "Both research outcomes demonstrated"})
            self.assertEqual("done", result["outcome"])
            self.assertGreaterEqual(provider.call_count, 6)
            with xo.store.connect() as db:
                self.assertTrue(all(row[0] in names.values() for row in db.execute("SELECT DISTINCT role FROM role_assignments")))
