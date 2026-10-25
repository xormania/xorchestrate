import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from fixtures.commands import GOAL
from fixtures.runtime import RuntimeFixture

from devtools import duty, processes, standby
from devtools.api import API
from devtools.launch import configuration, start
from devtools.runtime import allocate
from devtools.sessions import Lease

PASSED = {"status": "preflight_passed", "checks": [{"name": "fixture", "status": "passed"}]}
BLOCKED = {"status": "blocked", "checks": [{"name": "fixture", "status": "failed"}]}


class StandbyTests(RuntimeFixture, unittest.TestCase):
    def request(self, preflight=PASSED):
        with patch("devtools.readiness.startup_preflight", return_value=preflight):
            return standby.start(self.co)

    def scope(self, member):
        with self.store.connect() as db:
            return standby.binding(db, member)

    def test_initial_request_creates_no_work_and_reserves_one_xo(self):
        report = self.request()
        member = report["xo"]["member"]
        scope = self.scope(member)
        self.assertEqual("starting", report["state"])
        self.assertEqual("xo", standby.role(self.store, member))
        self.assertEqual("standby", standby.context(self.store, member)["state"])
        self.assertEqual("owner", scope["reports_to"])
        self.assertGreater(scope["deadline"], time.time())
        self.assertEqual(1, scope["launch_pending"])
        with self.store.connect() as db:
            for table in ("member_bindings", "work_orders", "jobs", "order_workspaces"):
                self.assertEqual(0, db.execute("SELECT count(*) FROM " + table).fetchone()[0], table)
        with patch("devtools.readiness.startup_preflight") as preflight:
            repeated = standby.start(self.co)
        preflight.assert_not_called()
        self.assertEqual(member, repeated["xo"]["member"])
        self.assertEqual(scope["revision"], self.scope(member)["revision"])

    def test_preflight_failure_is_retained_and_retry_uses_same_reserved_seat(self):
        first = self.request(BLOCKED)
        member = first["xo"]["member"]
        before = self.scope(member)
        self.assertEqual("blocked", first["state"])
        self.assertEqual(BLOCKED, first["preflight"])
        self.assertEqual(0, before["launch_pending"])
        second = self.request()
        self.assertEqual(member, second["xo"]["member"])
        self.assertGreater(self.scope(member)["revision"], before["revision"])
        self.assertEqual("starting", second["state"])

    def test_capacity_counts_standby_and_allocator_never_reuses_reserved_seat(self):
        first = self.request()
        member = first["xo"]["member"]
        policy = json.loads((self.state / "roles.json").read_text())
        policy["max_sessions"] = 1
        self.write("roles", policy)
        self.assertEqual(0, self.co.call("monitor", {})["capacity"][0]["available"])
        with self.store.connect() as db, self.assertRaisesRegex(ValueError, "capacity"):
            allocate(self.store, db, self.co.commands._config(), "coder")
        policy["max_sessions"] = 2
        self.write("roles", policy)
        with self.store.connect() as db:
            slot = allocate(self.store, db, self.co.commands._config(), "coder")
        self.assertNotEqual(member, slot["name"])

    def test_standby_configuration_does_not_create_a_workspace_or_select_for_work(self):
        member = self.request()["xo"]["member"]
        with (patch("devtools.workspaces.Workspaces.ensure", side_effect=AssertionError("No work exists")),
              patch("devtools.assignment_decisions.select_bound_runtime", side_effect=AssertionError("No work exists"))):
            configured = configuration(self.state, member)
        self.assertEqual(str(self.workspace), configured["workspace"])
        self.assertEqual("codex", configured["harness"])

    def test_standby_has_no_work_authority_and_other_members_cannot_request_it(self):
        member = self.request()["xo"]["member"]
        xo = API(self.state, member)
        self.assertEqual({"onboard", "skill_read", "work_context", "readiness_report"},
                         {tool["name"] for tool in xo.tools()})
        for capability in ("orchestrate", "direct", "work_report", "conclude"):
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                xo.call(capability, {})
        with self.assertRaisesRegex(ValueError, "appointed CO"):
            standby.start(xo)

    def test_direct_promotes_same_seat_and_fences_diagnostic_generation(self):
        member = self.request()["xo"]["member"]
        before = self.scope(member)
        first = self.store.begin_session(member, os.getpid(), standby_revision=before["revision"])
        self.store.session(member, generation=first, state="idle", native_id="standby-native")
        lease = Lease(self.store, member, first)
        lease.check()
        self.co.call("direct", GOAL)
        self.assertIsNone(self.scope(member))
        assigned = API(self.state, member).control.snapshot()
        self.assertEqual("xo", assigned["role"])
        self.assertEqual("owner", assigned["reports_to"])
        self.assertEqual(1, assigned["launch_pending"])
        with self.assertRaisesRegex(ValueError, "stopped or been replaced"):
            lease.check()
        self.store.session(member, generation=first, state="stopped", desired="stopped", pid=None)
        second = self.store.begin_session(member, os.getpid(), binding_revision=assigned["revision"])
        self.assertNotEqual(first, second)
        with self.store.connect() as db:
            receipts = list(db.execute("SELECT generation,status FROM duty_readiness WHERE member=? ORDER BY id", (member,)))
            self.assertEqual(1, db.execute("SELECT count(*) FROM work_orders").fetchone()[0])
        self.assertEqual([(first, "superseded"), (second, "starting")], [tuple(row) for row in receipts])

    def test_superseded_launch_intent_cannot_start_a_native_session(self):
        member = self.request()["xo"]["member"]
        revision = self.scope(member)["revision"]
        with patch("devtools.launch._start", side_effect=AssertionError("Must not launch")):
            self.assertEqual({"superseded": True}, start(self.state, member, standby_revision=revision + 1))
        with self.assertRaisesRegex(ValueError, "superseded"):
            self.store.begin_session(member, os.getpid(), standby_revision=revision + 1)
        self.assertIsNone(self.store.member(member)["generation"])

    def test_standby_channel_can_only_receive_current_readiness_messages(self):
        member = self.request()["xo"]["member"]
        self.store.send("owner", member, "Unrelated unbound message", key="unrelated", work_order=None, job=None)
        generation = self.store.begin_session(member, os.getpid(), standby_revision=self.scope(member)["revision"])
        delivered = self.store.take_delivery(member, scoped=True, generation=generation)
        self.assertEqual("readiness", delivered["category"])
        self.assertIsNone(self.store.take_delivery(member, scoped=True, generation=generation))

    def test_appointment_revocation_removes_standby_authority_and_launch(self):
        member = self.request()["xo"]["member"]
        revision = self.scope(member)["revision"]
        team = json.loads((self.state / "team.json").read_text())
        del team["co"]
        self.write("team", team)
        self.assertIsNone(standby.role(self.store, member))
        self.assertIsNone(standby.context(self.store, member))
        self.assertEqual(["work_context"], [tool["name"] for tool in API(self.state, member).tools()])
        with self.assertRaisesRegex(ValueError, "appointment"):
            configuration(self.state, member)
        self.assertEqual({"superseded": True}, start(self.state, member, standby_revision=revision))
        standby.tick(self.store)
        self.assertIsNone(self.scope(member))

    def test_expired_initial_deadline_prevents_launch(self):
        member = self.request()["xo"]["member"]
        with self.store.connect() as db:
            db.execute("UPDATE standby_bindings SET deadline=? WHERE member=?", (time.time() - 1, member))
        self.assertEqual("timed_out", standby.status(self.co)["state"])
        with patch("devtools.launch.start", side_effect=AssertionError("Must not launch")):
            standby.tick(self.store)
        self.assertEqual(0, self.scope(member)["launch_pending"])
        self.assertEqual("stopped", self.store.member(member)["desired"])
        self.assertEqual("timed_out", standby.status(self.co)["state"])

    def test_abrupt_supervisor_death_invalidates_ready_and_new_roll_call_retries(self):
        member = self.request()["xo"]["member"]
        before = self.scope(member)
        process = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"])
        try:
            generation = self.store.begin_session(member, process.pid, standby_revision=before["revision"])
            registered = processes.register(self.store, member, generation, process.pid)
            self.assertIsNotNone(registered)
            self.store.session(member, generation=generation, state="idle", native_id="standby-native")
            xo = API(self.state, member)
            onboard = xo.call("onboard", {})
            result = xo.call("readiness_report", {key: onboard["readiness"][key]
                             for key in ("challenge", "onboarding_revision")})
            self.assertEqual("ready", result["status"])
            # A reused PID must not make a previous birth identity look live.
            with patch("devtools.processes.identity", return_value={**registered, "start": "different-birth"}):
                with self.store.connect() as db:
                    self.assertEqual("blocked", duty.snapshot_in(self.store, db, member)["status"])
            process.kill()
            process.wait(timeout=5)
            # No graceful session update or watcher tick occurs between death
            # and these reads. The receipt and queued delivery must be current.
            self.assertIsNone(self.store.take_delivery("owner", scoped=True))
            self.assertEqual("blocked", standby.status(self.co)["state"])
            retried = self.request()
            self.assertEqual("starting", retried["state"])
            self.assertEqual(member, retried["xo"]["member"])
            revision = self.scope(member)["revision"]
            self.assertGreater(revision, before["revision"])
            self.assertEqual("stopped", self.store.member(member)["desired"])
            with patch("devtools.readiness.startup_preflight") as preflight:
                standby.start(self.co)
            preflight.assert_not_called()
            self.assertEqual(revision, self.scope(member)["revision"])
            with patch("devtools.launch.start") as launch:
                standby.tick(self.store)
            launch.assert_called_once_with(self.state, member, standby_revision=revision)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def test_expired_native_handshake_stops_standby_and_can_be_retried(self):
        member = self.request()["xo"]["member"]
        before = self.scope(member)
        generation = self.store.begin_session(member, os.getpid(), standby_revision=before["revision"])
        with self.store.connect() as db:
            deadline = db.execute("SELECT deadline FROM duty_readiness WHERE member=?", (member,)).fetchone()[0]
            self.assertEqual(before["deadline"], deadline)
            db.execute("UPDATE duty_readiness SET deadline=? WHERE member=?", (time.time() - 1, member))
        duty.tick(self.store)
        standby.tick(self.store)
        self.assertEqual("stopped", self.store.member(member)["desired"])
        self.assertEqual("timed_out", standby.status(self.co)["state"])
        self.store.session(member, generation=generation, state="stopped", desired="stopped", pid=None)
        report = self.request()
        self.assertEqual("starting", report["state"])
        self.assertEqual(member, report["xo"]["member"])
        self.assertGreater(self.scope(member)["revision"], before["revision"])
        with patch("devtools.launch.start") as launch:
            standby.tick(self.store)
        launch.assert_called_once_with(self.state, member, standby_revision=self.scope(member)["revision"])
        self.assertEqual(1, self.scope(member)["launch_pending"])

    def test_failure_before_native_registration_is_routed_to_co(self):
        self.request()
        with patch("devtools.launch.start", side_effect=ValueError("Native executable failed to start")):
            standby.tick(self.store)
        self.assertEqual("blocked", standby.status(self.co)["state"])
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM duty_readiness").fetchone()[0])
            self.assertEqual(1, len(standby.failure_messages_in(self.store, db, "owner")))
            message = db.execute("SELECT * FROM messages WHERE recipient='owner'").fetchone()
        self.assertIn("Native executable failed to start", message["body"])
        self.assertEqual("readiness", message["category"])
        self.assertIsNone(message["work_order"])
        delivered = self.store.take_delivery("owner", scoped=True)
        self.assertEqual(message["id"], delivered["id"])
        # A retry supersedes the old failure message as well as its launch intent.
        self.request()
        with self.store.connect() as db:
            self.assertEqual([], standby.failure_messages_in(self.store, db, "owner"))

    def test_fixed_team_selects_a_managed_seat_after_external_collaborators(self):
        team = json.loads((self.state / "team.json").read_text())
        team["runtime"] = False
        team["members"] += [
            {"name": "external", "harness": "external", "workspace": str(self.workspace)},
            {"name": "chief", "harness": "codex", "binary": "/bin/true", "workspace": str(self.workspace)},
        ]
        self.write("team", team)
        report = self.request()
        self.assertEqual("chief", report["xo"]["member"])
        self.assertEqual("codex", configuration(self.state, "chief")["harness"])


if __name__ == "__main__":
    unittest.main()
