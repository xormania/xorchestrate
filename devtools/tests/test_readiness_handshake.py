"""Readiness over real MCP and native protocol fixtures, without vendor credentials."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.commands import GOAL
from support import Client, adopt_plan, initialize, require_tmux, retain_native, terminal_team, until

from devtools import processes
from devtools.api import API
from devtools.control import Control
from devtools.store import Store

PREFLIGHT = {"status": "preflight_passed", "checks": [
    {"name": "fixture dependencies", "status": "passed", "detail": "Supplied by the protocol test."},
]}


def configure(root, harness="codex"):
    workspace, state = root / "workspace", root / "state"
    workspace.mkdir()
    initialize(state, workspace, ["owner:external", "chief:" + harness, "worker:codex"], co="owner")
    team = json.loads((state / "team.json").read_text())
    for member in team["members"][1:]:
        fixture = "claude_fixture.py" if member["harness"] == "claude" else "native_fixture.py"
        member["binary"] = str(Path(__file__).with_name(fixture))
    (state / "team.json").write_text(json.dumps(team))
    return state, Store(state)


def start_roll_call(state):
    # Project/PHP/Docker probes have their own subprocess tests. Keep this test
    # about real MCP transport, session identity, and parent/child handoff.
    with patch("devtools.readiness.startup_preflight", return_value=PREFLIGHT):
        return API(state, "owner").call("roll_call", {})


def report(client, onboard):
    readiness = onboard["readiness"]
    return client.tool("readiness_report", {
        "challenge": readiness["challenge"], "onboarding_revision": onboard["revision"],
    })


class ReadinessMCPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state, self.store = configure(Path(temporary.name))

    def client(self, member, **kwargs):
        client = Client(self.state, member, **kwargs)
        self.addCleanup(client.close)
        return client

    def test_standby_round_trip_has_no_work_authority_and_reconnect_needs_fresh_receipt(self):
        start_roll_call(self.state)
        # Supply a session identity directly to isolate MCP from tmux; the
        # separate native tests below exercise real harness session creation.
        with self.store.connect() as db:
            db.execute("UPDATE standby_bindings SET launch_pending=0")
        first = self.store.begin_session("chief", os.getpid())
        processes.register(self.store, "chief", first, os.getpid())
        self.store.session("chief", generation=first, state="idle", native_id="fixture-first")
        co = self.client("owner")
        xo = self.client("chief", generation=first)
        self.assertEqual({"onboard", "skill_read", "work_context", "readiness_report"},
                         {row["name"] for row in xo.call("tools/list")["tools"]})
        self.assertIn("roll_call", {row["name"] for row in co.call("tools/list")["tools"]})
        for tool, args in (("direct", GOAL), ("orchestrate", {"work": []}),
                           ("work_report", {"text": "Cannot coordinate standby"}), ("roll_call", {})):
            with self.subTest(tool=tool), self.assertRaisesRegex(ValueError, "not available"):
                xo.tool(tool, args)
        onboard = xo.tool("onboard", {})
        self.assertEqual("standby", onboard["context"]["state"])
        self.assertEqual("starting", onboard["readiness"]["status"])
        report(xo, onboard)
        current = co.tool("roll_call", {"action": "status"})
        self.assertEqual("ready", current["state"])
        messages = co.tool("work_context", {"section": "messages"})["messages"]
        self.assertTrue(any("reporting for duty" in row["text"].lower() for row in messages))
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM work_orders").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM member_bindings").fetchone()[0])

        self.store.session("chief", generation=first, state="stopped", desired="stopped", pid=None)
        second = self.store.begin_session("chief", os.getpid())
        processes.register(self.store, "chief", second, os.getpid())
        self.store.session("chief", generation=second, state="idle", native_id="fixture-second")
        with self.assertRaisesRegex(ValueError, "replaced"):
            report(xo, onboard)
        replacement = self.client("chief", generation=second)
        fresh = replacement.tool("onboard", {})
        self.assertNotEqual(onboard["readiness"]["challenge"], fresh["readiness"]["challenge"])
        self.assertEqual("starting", co.tool("roll_call", {"action": "status"})["state"])
        with self.assertRaises(ValueError):
            report(replacement, onboard)
        report(replacement, fresh)
        self.assertEqual("ready", co.tool("roll_call", {"action": "status"})["state"])

    def test_specialist_receipt_follows_the_actual_nco_reporting_relationship(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, state = root / "workspace", root / "state"
            workspace.mkdir()
            initialize(state, workspace, ["owner:external", "chief:external", "coordinator:external", "worker:codex"],
                       co="owner")
            team = json.loads((state / "team.json").read_text())
            team["members"][-1]["binary"] = str(Path(__file__).with_name("native_fixture.py"))
            (state / "team.json").write_text(json.dumps(team))
            co, xo = API(state, "owner"), API(state, "chief")
            co.call("direct", GOAL)
            jobs = [{"name": name, "objective": "Inspect the assigned area", "done_when": "Evidence retained",
                     "constraints": "Protocol fixture only"} for name in ("Alpha", "Beta")]
            adopt_plan(xo, jobs)
            xo.call("orchestrate", {"work": [{"name": "Coordinate Alpha", "role": "nco", "job": "Alpha"}]})
            nco = API(state, "coordinator")
            nco.call("orchestrate", {"work": [{"name": "Inspect Alpha", "role": "research", "job": "Alpha",
                                                "objective": "Inspect Alpha", "done_when": "Report facts",
                                                "constraints": "Research only"}]})
            with co.store.connect() as db:
                db.execute("UPDATE member_bindings SET launch_pending=0")
            generation = co.store.begin_session("worker", os.getpid())
            processes.register(co.store, "worker", generation, os.getpid())
            co.store.session("worker", generation=generation, state="idle", native_id="fixture-nco-specialist")
            parent, worker = Client(state, "coordinator"), Client(state, "worker", generation=generation)
            try:
                onboard = worker.tool("onboard", {})
                ready = report(worker, onboard)
                self.assertEqual("ready", ready["status"])
                messages = parent.tool("work_context", {"section": "messages"})["messages"]
                self.assertTrue(any(row["reference"] == ready["receipt"] for row in messages))
                with co.store.connect() as db:
                    receipt = db.execute("SELECT recipient FROM messages WHERE id=?",
                                         (int(ready["receipt"].split(":")[1]),)).fetchone()
                self.assertEqual("coordinator", receipt["recipient"])
                with self.assertRaisesRegex(ValueError, "not available"):
                    worker.tool("roll_call", {})
            finally:
                worker.close()
                parent.close()


class NativeReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_tmux()

    def test_initial_readiness_uses_each_harness_communication_path(self):
        for harness in ("codex", "grok", "claude"):
            with self.subTest(harness=harness), terminal_team() as temporary:
                state, store = configure(Path(temporary), harness)
                start_roll_call(state)
                co = Client(state, "owner")
                try:
                    ready = until(lambda: value if (value := co.tool("roll_call", {"action": "status"}))["state"] == "ready" else None,
                                  timeout=15)
                    self.assertTrue(ready["initial"])
                    self.assertEqual("xo", ready["xo"]["role"])
                    self.assertTrue(store.member("chief")["native_id"])
                    self.assertIsNone(Control(store, "chief").snapshot())
                    self.assertTrue(store.events(actor="chief", kind="onboarding_delivered")["events"])
                    messages = co.tool("work_context", {"section": "messages"})["messages"]
                    self.assertTrue(any("reporting for duty" in row["text"].lower() for row in messages))
                    with store.connect() as db:
                        self.assertEqual(0, db.execute("SELECT count(*) FROM work_orders").fetchone()[0])
                        receipt = db.execute("SELECT * FROM duty_readiness WHERE member='chief' ORDER BY id DESC LIMIT 1").fetchone()
                    self.assertEqual(store.member("chief")["generation"], receipt["generation"])
                    self.assertIsNotNone(receipt["receipt_message"])
                finally:
                    co.close()
                    retain_native(state, "initial-readiness-" + harness)

    def test_work_promotion_gets_new_session_and_specialist_reports_to_its_parent(self):
        with terminal_team() as temporary:
            state, store = configure(Path(temporary))
            start_roll_call(state)
            co = Client(state, "owner")
            old = None
            try:
                until(lambda: co.tool("roll_call", {"action": "status"})["state"] == "ready", timeout=15)
                original = store.member("chief")
                old = Client(state, "chief", generation=original["generation"])
                before = old.tool("onboard", {})
                co.tool("direct", GOAL)
                until(lambda: store.member("chief")["generation"] != original["generation"], timeout=15)

                def ready_coder():
                    with store.connect() as db:
                        row = db.execute("SELECT * FROM duty_readiness WHERE role='coder' AND status='ready' ORDER BY id DESC LIMIT 1").fetchone()
                    return dict(row) if row else None

                worker = until(ready_coder, timeout=15)
                self.assertEqual("chief", worker["parent"])
                self.assertNotEqual(original["native_id"], store.member("chief")["native_id"])
                self.assertIsNotNone(Control(store, "chief").snapshot())
                with self.assertRaisesRegex(ValueError, "replaced"):
                    report(old, before)
                with store.connect() as db:
                    receipt = db.execute("SELECT * FROM messages WHERE id=?", (worker["receipt_message"],)).fetchone()
                    work_ready = db.execute("SELECT * FROM duty_readiness WHERE member='chief' AND generation=?",
                                            (store.member("chief")["generation"],)).fetchone()
                self.assertEqual("chief", receipt["recipient"])
                self.assertEqual("ready", work_ready["status"])
                self.assertNotEqual(before["readiness"]["challenge"], work_ready["challenge"])
                self.assertFalse(co.tool("roll_call", {"action": "status"})["initial"])
            finally:
                if old:
                    old.close()
                co.close()
                retain_native(state, "readiness-work-promotion")
