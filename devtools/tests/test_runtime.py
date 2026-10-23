import json
import os
import unittest
from unittest.mock import patch

from fixtures.commands import GOAL, responsibility
from fixtures.runtime import RuntimeFixture
from support import adopt_plan, initialize

from devtools.api import API
from devtools.control import ROLES
from devtools.launch import agent, configuration, start
from devtools.runtime import load, resolve


class RuntimeTests(RuntimeFixture, unittest.TestCase):


    def test_fallback_all_roles_config_revision_and_fail_closed(self):
        first = resolve(self.state, "coder")
        self.vendor("openai", False)
        self.vendor("xai", True)
        for role in ROLES:
            selected = resolve(self.state, role)
            self.assertEqual("grok", selected["harness"])
            self.assertEqual(2, len(selected["selection"]["skipped"]))
            self.assertNotEqual(first["selection"]["revision"], selected["selection"]["revision"])
        self.vendor("xai", False)
        with self.assertRaisesRegex(ValueError, "No runtime available"):
            self.co.call("direct", GOAL)
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM work_orders").fetchone()[0])
        policy = json.loads((self.state / "roles.json").read_text())
        del policy["roles"]["architect"]
        self.write("roles", policy)
        load(self.state)
        with self.assertRaisesRegex(ValueError, "Configure architect preferences"):
            resolve(self.state, "architect")

    def test_runtime_subset_missing_requested_role_and_unknown_identity(self):
        original = json.loads((self.state / "roles.json").read_text())
        self.assertEqual(set(ROLES), set(original["roles"]))
        policy = json.loads(json.dumps(original))
        policy["roles"]["test-writer"] = policy["roles"]["test_writer"]
        self.write("roles", policy)
        with self.assertRaisesRegex(ValueError, "unknown catalog roles: test-writer"):
            load(self.state)
        self.write("roles", original)
        xo = self.xo()
        before = xo.control.snapshot()
        original["roles"] = {"xo": original["roles"]["xo"]}
        self.write("roles", original)
        load(self.state)
        self.assertEqual("xo", resolve(self.state, "xo")["selection"]["role"])
        with self.assertRaisesRegex(ValueError, "Configure planner preferences"):
            resolve(self.state, "planner")
        self.assertEqual(before, xo.control.snapshot())

    def test_dynamic_multiple_instances_normal_tools_hide_runtime_and_capacity_is_atomic(self):
        xo = self.xo()
        for name in ("One", "Two", "Three", "Four"):
            xo.work.job_create(title=name, objective="Runtime fixture", done_when="Bound", work_order=xo.control.snapshot()["work_order"])
        xo.call("orchestrate", {"work": [responsibility("One"), responsibility("Two")]})
        report = xo.call("monitor", {})
        self.assertEqual(["coder", "coder"], [row["role"] for row in report["collaborators"]])
        self.assertNotIn("configured-model", json.dumps(report))
        self.assertNotIn("harness", json.dumps(report))
        self.assertEqual(1, len(json.loads((self.state / "team.json").read_text())["members"]))
        with self.assertRaises(ValueError):
            xo.call("runtime_status", {})
        with self.assertRaises(ValueError):
            xo.call("orchestrate", {"work": [responsibility("Three") | {"selection": {"harness": "grok"}}]})
        policy = json.loads((self.state / "roles.json").read_text())
        policy["max_sessions"] = 4
        self.write("roles", policy)
        with self.assertRaisesRegex(ValueError, "capacity"):
            xo.call("orchestrate", {"work": [responsibility("Three"), responsibility("Four")]})
        with self.store.connect() as db:
            self.assertEqual(4, db.execute("SELECT count(*) FROM jobs").fetchone()[0])
            self.assertEqual(4, db.execute("SELECT count(*) FROM members").fetchone()[0])

    def test_running_session_retains_snapshot_after_vendor_disabled(self):
        xo = self.xo()
        before = configuration(self.state, xo.member)
        generation = self.store.begin_session(xo.member, os.getpid(), settings=before)
        self.store.session(xo.member, generation=generation, state="idle")
        self.vendor("openai", False)
        self.vendor("xai", True)
        with patch("devtools.processes.owned", return_value=[{"pid": os.getpid()}]):
            self.assertTrue(start(self.state, xo.member)["already_running"])
        self.assertEqual(generation, self.store.member(xo.member)["generation"])
        self.assertEqual("codex", self.store.member(xo.member)["harness"])
        self.store.session(xo.member, generation=generation, state="stopped", pid=None)
        self.assertEqual("grok", configuration(self.state, xo.member)["harness"])

    def test_missing_binary_falls_back_and_policy_read_cannot_choose_shell_arguments(self):
        self.vendor("anthropic", True)
        with patch("devtools.runtime.shutil.which", side_effect=[None, "/bin/true"]):
            selected = resolve(self.state, "research")
        self.assertEqual("claude", selected["harness"])
        policy = json.loads((self.state / "roles.json").read_text())
        policy["profiles"]["codex"]["args"] = ["--unsafe"]
        self.write("roles", policy)
        with self.assertRaisesRegex(ValueError, "Unknown runtime profile"):
            load(self.state)

    def test_migrated_fixed_members_keep_their_work_after_a_harness_change(self):
        state = self.root / "fixed-state"
        initialize(state, self.workspace, ["owner:external", "chief:codex", "worker:claude"], co="owner")
        team = json.loads((state / "team.json").read_text())
        for member in team["members"][1:]:
            member["binary"] = "/bin/true"
        (state / "team.json").write_text(json.dumps(team))
        co, xo = API(state, "owner"), API(state, "chief")
        co.call("direct", GOAL)
        adopt_plan(xo, [{"name": "Implementation", **{key: responsibility()[key] for key in ("objective", "done_when", "constraints")}}])
        xo.call("orchestrate", {"work": [responsibility()]})
        before = API(state, "worker").control.snapshot()
        co.store.begin_session("worker", os.getpid())
        co.store.session("worker", state="stopped", desired="stopped", pid=None)
        team = json.loads((state / "team.json").read_text())
        team["runtime"] = True
        (state / "team.json").write_text(json.dumps(team))
        (state / "roles.json").write_text((self.state / "roles.json").read_text())
        self.vendor("openai", False)
        self.vendor("xai", True)
        (state / "vendors.json").write_text((self.state / "vendors.json").read_text())

        class Native:
            def __init__(self, directory, member, config, generation):
                self.harness = config["harness"]

            async def run(self):
                assert self.harness == "grok"

        with (patch("devtools.native.Native", Native), patch("devtools.launch.signal.signal"),
              patch("devtools.processes.register")):
            agent(state, "worker")
        self.assertEqual("grok", co.store.member("worker")["harness"])
        self.assertEqual("claude", team["members"][2]["harness"])
        co.call("monitor", {})
        co.call("direct", GOAL)
        xo.call("orchestrate", {"work": [responsibility()]})
        self.assertEqual(before, API(state, "worker").control.snapshot())
        self.assertEqual("grok", configuration(state, "worker")["harness"])
        # Runtime changes do not authorize workspace changes or converting an external identity.
        with self.assertRaisesRegex(ValueError, "different harness or worktree"):
            co.store.initialize(team["members"])
        other = self.root / "other-workspace"
        other.mkdir()
        changed = [{**team["members"][2], "workspace": str(other)}]
        with self.assertRaisesRegex(ValueError, "different harness or worktree"):
            co.store.initialize(changed, runtime=True)
        with self.assertRaisesRegex(ValueError, "different harness or worktree"):
            co.store.initialize([{**team["members"][0], "harness": "codex"}], runtime=True)
