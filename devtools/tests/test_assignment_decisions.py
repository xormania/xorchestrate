import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.commands import GOAL
from support import adopt_plan, runtime_policy

from devtools.api import API
from devtools.failures import Failure
from devtools.launch import configuration, initialize
from devtools.store import Store


class AssignmentDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "source"
        self.workspace.mkdir()
        self.state = self.root / "state"
        initialize(self.state, self.workspace, ["owner:external"], co="owner", runtime=True)
        policy = runtime_policy()
        for name, row in policy["profiles"].items():
            row.update(binary="/bin/true", model=name + "-fixture", reasoning="high")
        (self.state / "roles.json").write_text(json.dumps(policy))
        (self.state / "vendors.json").write_text(json.dumps({"version": 1, "vendors": {
            row["vendor"]: {"available": True} for row in policy["profiles"].values()}}))
        self.settings(selection=False)
        self.store = Store(self.state)
        self.co = API(self.state, "owner")
        self.co.call("direct", GOAL)
        with self.store.connect() as db:
            name = db.execute("SELECT member FROM member_bindings WHERE role='xo'").fetchone()[0]
        self.xo = API(self.state, name)
        self.requests = []
        self.decisions = {}
        self.before_answer = None
        self.selection_patch = patch("devtools.decisions.Decisions.select", side_effect=self.answer)
        self.verification_patch = patch("devtools.decisions.Decisions.verify")
        self.select = self.selection_patch.start()
        self.verify = self.verification_patch.start()
        self.addCleanup(self.selection_patch.stop)
        self.addCleanup(self.verification_patch.stop)

    def settings(self, *, selection=True, roles=None):
        team = json.loads((self.state / "team.json").read_text())
        team.setdefault("project", {})["configuration_file"] = "selection.json"
        (self.state / "team.json").write_text(json.dumps(team))
        settings = {"features": {"selection": selection, "completion": False, "filtering": False, "advice": False}}
        if roles is not None:
            settings["assignment"] = {"roles": roles}
        (self.state / "selection.json").write_text(json.dumps({"version": 1, "project": settings}))

    def answer(self, purpose, state, questions, **kwargs):
        self.requests.append((purpose, state, questions, kwargs))
        if self.before_answer:
            callback, self.before_answer = self.before_answer, None
            callback()
        defaults = {"scope": "required", "overlap": False, "parallel": True, "role": "coder", "runtime": "p1"}
        choices = {name: self.decisions.get(name, defaults[name]) for name in questions}
        return {"id": len(self.requests), "decisions": choices, "answers": {}, "evidence": "artifact:1", "state": "resolved"}

    def plan(self, names=("Feature",)):
        adopt_plan(self.xo, [{"name": name, "objective": "Deliver " + name, "done_when": "Behavior passes tests",
                              "constraints": "Product changes only"} for name in names])

    def assigned(self, role, name="Implement"):
        with self.store.connect() as db:
            rows = list(db.execute("SELECT b.*,a.details,a.revision AS assignment_revision FROM member_bindings b "
                                   "JOIN role_assignments a ON a.id=b.assignment_id WHERE a.role=? AND a.name=?", (role, name)))
        self.assertEqual(1, len(rows))
        return dict(rows[0])

    def counts(self):
        with self.store.connect() as db:
            return {table: db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                    for table in ("members", "member_bindings", "role_assignments", "messages")}

    def launch_configuration(self, member):
        # These fixtures exercise assignment/profile loading, without creating a
        # Git checkout or contacting GitHub for an unrelated workspace setup.
        with patch("devtools.workspaces.project_config", return_value={}):
            return configuration(self.state, member)

    def test_orchestrate_consumes_nondefault_profile_and_retries_same_selection(self):
        self.plan()
        self.settings(roles=["coder"])
        request = {"name": "Implement", "job": "Feature", "role": "coder"}
        self.xo.call("orchestrate", {"work": [request]})
        assigned = self.assigned("coder")
        launched = self.launch_configuration(assigned["member"])
        self.assertEqual("claude", launched["selection"]["profile"])
        self.assertEqual("claude-fixture", launched["model"])
        self.assertEqual(["assignment", "runtime"], [row[0] for row in self.requests])
        before = self.counts()
        self.xo.call("orchestrate", {"work": [request]})
        self.assertEqual(before, self.counts())
        self.assertEqual(2, len(self.requests))
        self.assertEqual(assigned["assignment_id"], self.assigned("coder")["assignment_id"])

    def test_clear_scope_selection_blocks_without_allocating(self):
        self.plan()
        self.settings(roles=["coder"])
        self.decisions["scope"] = "separate"
        before = self.counts()
        with self.assertRaises(Failure) as caught:
            self.xo.call("orchestrate", {"work": [{"name": "Implement", "job": "Feature", "role": "coder"}]})
        self.assertEqual("assignment_scope_not_required", caught.exception.failure["reason"])
        self.assertEqual(before, self.counts())
        self.assertEqual(1, len(self.requests))

    def test_role_choice_executes_and_changed_responsibility_does_not_duplicate_it(self):
        self.plan()
        self.settings(roles=["coder", "research"])
        self.decisions["role"] = "research"
        request = {"name": "Implement", "job": "Feature", "role": "coder"}
        self.xo.call("orchestrate", {"work": [request]})
        assigned = self.assigned("research")
        self.assertNotIn("planner", self.requests[0][2]["role"]["criteria"])
        self.assertNotIn("arbitrator", self.requests[0][2]["role"]["criteria"])
        self.xo.call("orchestrate", {"work": [request]})
        self.assertEqual(2, len(self.requests))
        self.xo.call("orchestrate", {"work": [{**request, "objective": "Investigate the revised boundary"}]})
        revised = self.assigned("research")
        self.assertEqual(assigned["assignment_id"], revised["assignment_id"])
        self.assertEqual(assigned["assignment_revision"] + 1, revised["assignment_revision"])
        self.assertNotIn("role", self.requests[-2][2])

    def test_work_change_during_evaluation_rejects_stale_decision(self):
        self.plan()
        self.settings(roles=["coder"])
        before = self.counts()

        def change():
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET objective='Changed while evaluating' WHERE title='Feature'")

        self.before_answer = change
        with self.assertRaises(Failure) as caught:
            self.xo.call("orchestrate", {"work": [{"name": "Implement", "job": "Feature", "role": "coder"}]})
        self.assertEqual("assignment_selection_stale", caught.exception.failure["reason"])
        self.assertEqual(before, self.counts())

    def test_changed_profile_restarts_existing_native_session_through_persisted_intent(self):
        self.plan()
        self.settings(roles=["research"])
        request = {"name": "Implement", "job": "Feature", "role": "research"}
        self.xo.call("orchestrate", {"work": [request]})
        assigned = self.assigned("research")
        with self.store.connect() as db:
            db.execute("UPDATE members SET pid=777777,state='idle',desired='running' WHERE name=?", (assigned["member"],))
        self.decisions["runtime"] = "p0"
        self.xo.call("orchestrate", {"work": [{**request, "objective": "Investigate the new requirement"}]})
        session = self.store.member(assigned["member"])
        self.assertEqual("stopped", session["desired"])
        self.assertEqual(1, self.assigned("research")["launch_pending"])
        self.assertEqual("codex", self.launch_configuration(assigned["member"])["selection"]["profile"])

    def test_nco_assessment_uses_its_target_job_and_does_not_include_sibling_assignment(self):
        self.plan(("Alpha", "Beta"))
        for name in ("Alpha", "Beta"):
            self.xo.call("orchestrate", {"work": [{"name": name, "job": name, "role": "nco"}]})
        with self.store.connect() as db:
            coordinators = {row["title"]: dict(row) for row in db.execute(
                "SELECT b.*,j.title FROM member_bindings b JOIN jobs j ON b.job=j.id WHERE b.role='nco'")}
        beta = API(self.state, coordinators["Beta"]["member"])
        beta.call("orchestrate", {"work": [{"name": "BETA PRIVATE", "job": "Beta", "role": "research"}]})
        self.settings(roles=["research"])
        alpha = API(self.state, coordinators["Alpha"]["member"])
        alpha.call("orchestrate", {"work": [{"name": "Inspect", "job": "Alpha", "role": "research"}]})
        self.assertEqual(coordinators["Alpha"]["job"], self.requests[0][3]["target_job"])
        self.assertEqual("Alpha", self.requests[0][1]["job"]["title"])
        self.assertNotIn("BETA PRIVATE", json.dumps(self.requests))

    def test_real_runtime_arbitration_reuses_pending_decision_and_resumes_assignment(self):
        self.plan()
        self.settings(roles=["coder"])
        self.selection_patch.stop()
        self.verification_patch.stop()

        def evaluate(state, questions, model):
            answers = {}
            for name, question in questions.items():
                if question["type"] == "noul":
                    answers[name] = {"type": "noul", "noul": 0.01 if name == "overlap" else 0.99}
                else:
                    choice = "p0" if name == "runtime" else "required"
                    probabilities = {key: float(key == choice) for key in question["criteria"]}
                    if name == "runtime":
                        probabilities.update(p0=0.5, p1=0.5)
                    answers[name] = {"type": "choice", "choice": choice, "confidence": 0.5 if name == "runtime" else 0.99,
                                     "probabilities": probabilities}
            return {"model": model, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}

        request = {"work": [{"name": "Implement", "job": "Feature", "role": "coder"}]}
        with patch("devtools.advice.Jev.evaluate", side_effect=evaluate) as provider:
            with self.assertRaises(Failure) as caught:
                self.xo.call("orchestrate", request)
            self.assertEqual("decision_pending", caught.exception.failure["reason"])
            pending = caught.exception.failure["details"]
            with self.assertRaises(Failure) as retried:
                self.xo.call("orchestrate", request)
            self.assertEqual(pending["decision"], retried.exception.failure["details"]["decision"])
            self.assertEqual(2, provider.call_count)
            with self.store.connect() as db:
                self.assertEqual(0, db.execute("SELECT count(*) FROM role_assignments WHERE role='coder'").fetchone()[0])
                self.assertEqual(1, db.execute("SELECT count(*) FROM role_assignments WHERE role='arbitrator' AND status='active'").fetchone()[0])
            with self.assertRaises(ValueError):
                self.xo.call("orchestrate", {"work": [{"name": "Bypass", "job": "Feature", "role": "arbitrator"}]})
            with self.assertRaisesRegex(ValueError, "Arbitrator|arbitration"):
                self.xo.call("steer", {"action": "cancel", "work": "Feature", "assignment": "Decision " + str(pending["decision"]),
                                      "direction": "Skip this investigation"})
            arbitrator = API(self.state, pending["arbitrator"])
            evidence = arbitrator.artifacts.save("runtime-investigation.md", content="The Claude profile passed the representative fixture.")
            result = arbitrator.call("decision_resolve", {"decisions": {"runtime": "p1"},
                                      "reason": "The retained fixture evidence distinguishes the two candidates.",
                                      "evidence": ["artifact:" + str(evidence["artifact"]["id"])]})
            self.assertEqual("completed", result["continuation"]["state"], result)
            self.assertEqual(2, provider.call_count)
            self.assertEqual("claude", self.launch_configuration(self.assigned("coder")["member"])["selection"]["profile"])
            with self.store.connect() as db:
                self.assertEqual(0, db.execute("SELECT count(*) FROM member_bindings WHERE role='arbitrator'").fetchone()[0])

    def test_fixed_batch_excludes_the_member_already_selected_for_another_responsibility(self):
        self.state = self.root / "fixed"
        initialize(self.state, self.workspace, [name + ":external" for name in ("owner", "xo", "first", "second", "third")], co="owner")
        self.settings(selection=False)
        self.store = Store(self.state)
        self.co = API(self.state, "owner")
        self.co.call("direct", GOAL)
        self.xo = API(self.state, "xo")
        self.plan()
        self.settings(roles=["research"])
        self.decisions["runtime"] = "p0"
        self.xo.call("orchestrate", {"work": [
            {"name": "Inspect", "job": "Feature", "role": "research"},
            {"name": "Compare", "job": "Feature", "role": "research"},
        ]})
        first = self.assigned("research", "Inspect")["member"]
        second = self.assigned("research", "Compare")["member"]
        self.assertEqual("first", first)
        self.assertEqual("second", second)
        runtime_requests = [row for row in self.requests if row[0] == "runtime"]
        self.assertIn("first", runtime_requests[0][1]["profiles"])
        self.assertNotIn("first", runtime_requests[1][1]["profiles"])

    def test_selected_roles_cannot_collapse_two_responsibilities_into_one_assignment(self):
        self.plan()
        self.settings(roles=["research"])
        before = self.counts()
        with self.assertRaises(Failure) as caught:
            self.xo.call("orchestrate", {"work": [
                {"name": "Inspect", "job": "Feature", "role": "coder"},
                {"name": "Inspect", "job": "Feature", "role": "research"},
            ]})
        self.assertEqual("assignment_selection_collision", caught.exception.failure["reason"])
        self.assertEqual(before, self.counts())

    def test_steer_semantic_revision_is_selected_and_plain_direction_keeps_the_session(self):
        self.plan()
        self.settings(roles=["research"])
        self.xo.call("orchestrate", {"work": [{"name": "Implement", "job": "Feature", "role": "research"}]})
        assigned = self.assigned("research")
        with self.store.connect() as db:
            db.execute("UPDATE members SET pid=777777,state='idle',desired='running' WHERE name=?", (assigned["member"],))
        self.xo.call("steer", {"action": "guide", "work": "Feature", "assignment": "Implement", "direction": "Start by inspecting existing tests."})
        self.assertEqual(2, len(self.requests))
        self.assertEqual("running", self.store.member(assigned["member"])["desired"])
        self.assertEqual("claude", self.launch_configuration(assigned["member"])["selection"]["profile"])
        self.decisions["scope"] = "separate"
        with self.assertRaises(Failure) as rejected:
            self.xo.call("steer", {"action": "guide", "work": "Feature", "assignment": "Implement", "objective": "Unrelated investigation"})
        self.assertEqual("assignment_scope_not_required", rejected.exception.failure["reason"])
        self.assertEqual(assigned["assignment_revision"], self.assigned("research")["assignment_revision"])
        self.decisions.update(scope="required", runtime="p0")
        self.xo.call("steer", {"action": "guide", "work": "Feature", "assignment": "Implement", "objective": "Investigate the revised requirement",
                              "direction": "Use the current tests as evidence."})
        self.assertEqual(assigned["assignment_revision"] + 1, self.assigned("research")["assignment_revision"])
        self.assertEqual("stopped", self.store.member(assigned["member"])["desired"])
        self.assertEqual("codex", self.launch_configuration(assigned["member"])["selection"]["profile"])

    def test_bound_xo_runtime_arbitration_schedules_launch_with_the_resolved_profile(self):
        self.settings(roles=["coder"])
        self.selection_patch.stop()
        self.verification_patch.stop()

        def evaluate(state, questions, model):
            question = questions["runtime"]
            probabilities = {key: 0.0 for key in question["criteria"]}
            probabilities.update(p0=0.5, p1=0.5)
            return {"model": model, "answers": {"runtime": {"type": "choice", "choice": "p0", "confidence": 0.5,
                    "probabilities": probabilities}}, "usage": {"input_tokens": 1, "output_tokens": 1}}

        with patch("devtools.advice.Jev.evaluate", side_effect=evaluate) as provider:
            with self.assertRaises(Failure) as waiting:
                self.launch_configuration(self.xo.member)
            self.assertEqual("decision_pending", waiting.exception.failure["reason"])
            details = waiting.exception.failure["details"]
            with self.assertRaises(Failure) as repeated:
                self.launch_configuration(self.xo.member)
            self.assertEqual(details["decision"], repeated.exception.failure["details"]["decision"])
            arbitrator = API(self.state, details["arbitrator"])
            evidence = arbitrator.artifacts.save("xo-runtime.md", content="The Claude profile passed this coordinator fixture.")
            result = arbitrator.call("decision_resolve", {"decisions": {"runtime": "p1"}, "reason": "Select the tested coordinator profile.",
                                      "evidence": ["artifact:" + str(evidence["artifact"]["id"])]})
            self.assertEqual("scheduled", result["continuation"]["state"])
            self.assertEqual("claude", self.launch_configuration(self.xo.member)["selection"]["profile"])
            self.assertEqual(1, provider.call_count)
            with self.store.connect() as db:
                self.assertEqual(1, db.execute("SELECT launch_pending FROM member_bindings WHERE member=?", (self.xo.member,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
