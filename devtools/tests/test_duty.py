import json
import os
import time
import unittest
from unittest.mock import patch

from fixtures.control import ControlFixture

from devtools import duty, processes
from devtools.api import API
from devtools.role_packages import catalog


class DutyTests(unittest.TestCase):
    setUp = ControlFixture.setUp

    def start(self, member="coder", *, native=True):
        with self.store.connect() as db:
            db.execute("UPDATE members SET harness='codex' WHERE name=?", (member,))
        generation = self.store.begin_session(member, os.getpid())
        processes.register(self.store, member, generation, os.getpid())
        self.store.session(member, generation=generation, native_id="fixture-native" if native else None, state="idle")
        return API(self.state, member), generation

    @staticmethod
    def report(api, onboard=None, blockers=None):
        onboard = onboard or api.call("onboard", {})
        args = {key: onboard["readiness"][key] for key in ("challenge", "onboarding_revision")}
        if blockers is not None:
            args["blockers"] = blockers
        return api.call("readiness_report", args)

    def test_real_onboarding_and_acknowledgement_produce_one_parent_receipt(self):
        api, generation = self.start()
        before = duty.current(self.store, "coder")
        self.assertEqual("starting", before["status"])
        self.assertIsNone(before["onboarding_revision"])
        onboard = api.call("onboard", {})
        result = self.report(api, onboard)
        self.assertEqual("ready", result["status"])
        self.assertEqual(result, self.report(api, onboard))
        with self.store.connect() as db:
            receipt = db.execute("SELECT * FROM messages WHERE id=?", (int(result["receipt"][8:]),)).fetchone()
            challenge = db.execute("SELECT * FROM duty_readiness WHERE member='coder'").fetchone()
            self.assertEqual(generation, challenge["generation"])
            self.assertIsNotNone(db.execute("SELECT acknowledged FROM messages WHERE id=?", (challenge["challenge_message"],)).fetchone()[0])
        self.assertEqual("xo", receipt["recipient"])
        self.assertIn("coder reporting for duty", receipt["body"])
        self.assertNotIn(result["challenge"], receipt["body"])
        self.assertEqual(1, len(self.store.events(kind="readiness_ready")["events"]))

    def test_server_initialization_and_context_reads_cannot_establish_onboarding(self):
        api, _ = self.start()
        api.instructions()
        api.call("work_context", {})
        challenge = duty.current(self.store, "coder")
        with self.assertRaisesRegex(ValueError, "Call onboard"):
            api.call("readiness_report", {"challenge": challenge["challenge"], "onboarding_revision": "a" * 64})
        self.assertEqual("starting", duty.current(self.store, "coder")["status"])

    def test_another_member_cannot_answer_the_challenge(self):
        api, _ = self.start()
        other, _ = self.start("audit")
        onboard = api.call("onboard", {})
        with self.assertRaisesRegex(ValueError, "not current"):
            self.report(other, onboard)
        self.assertEqual("starting", duty.current(self.store, "coder")["status"])

    def test_replacement_session_cannot_reuse_previous_onboarding_or_challenge(self):
        api, first = self.start()
        onboard = api.call("onboard", {})
        self.store.session("coder", generation=first, state="stopped", desired="stopped", pid=None)
        _, second = self.start()
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(ValueError, "not current"):
            self.report(api, onboard)
        self.assertEqual("ready", self.report(api)["status"])

    def test_rebinding_invalidates_receipt_and_requires_fresh_onboarding(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        self.report(api, onboard)
        api.control.bind("coder", "research", self.order, self.job, "xo", "Investigate the assigned issue")
        current = duty.current(self.store, "coder")
        self.assertEqual("starting", current["status"])
        self.assertNotEqual(onboard["readiness"]["challenge"], current["challenge"])
        with self.assertRaisesRegex(ValueError, "not current"):
            self.report(api, onboard)
        self.assertEqual("research", self.report(api)["role"])

    def test_role_package_change_invalidates_existing_onboarding(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        changed = catalog()
        changed["roles"]["coder"]["responsibility"] += " Changed role contract."
        with patch("devtools.role_packages._catalog", return_value=changed):
            with self.assertRaisesRegex(ValueError, "superseded"):
                self.report(api, onboard)
            self.assertEqual("ready", self.report(api)["status"])

    def test_timeout_prevents_late_ready_and_routes_one_failure(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        with self.store.connect() as db:
            db.execute("UPDATE duty_readiness SET deadline=0 WHERE member='coder'")
        duty.tick(self.store)
        duty.tick(self.store)
        with self.assertRaisesRegex(ValueError, "timed_out"):
            self.report(api, onboard)
        result = duty.current(self.store, "coder")
        self.assertEqual("timed_out", result["status"])
        self.assertIn("receipt", result)
        self.assertEqual(1, len(self.store.events(kind="readiness_timed_out")["events"]))

    def test_missing_native_identity_blocks_a_successful_tool_round_trip(self):
        api, _ = self.start(native=False)
        result = self.report(api)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["native_session"], [item["name"] for item in result["checks"] if item["status"] == "failed"])

    def test_native_reply_can_arrive_before_idle_observation(self):
        api, generation = self.start()
        self.store.session("coder", generation=generation, state="starting")
        self.assertEqual("ready", self.report(api)["status"])

    def test_missing_process_registration_cannot_establish_or_retain_ready(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        with patch("devtools.processes.registered_alive", return_value=None):
            self.assertEqual("starting", duty.current(self.store, "coder")["status"])
            result = self.report(api, onboard)
            self.assertEqual("blocked", result["status"])
            self.assertEqual(["native_session"], [item["name"] for item in result["checks"] if item["status"] == "failed"])
        self.store.session("coder", state="stopped", desired="stopped", pid=None)
        api, _ = self.start()
        self.assertEqual("ready", self.report(api)["status"])
        with patch("devtools.processes.registered_alive", return_value=None):
            self.assertEqual("blocked", duty.current(self.store, "coder")["status"])

    def test_obsolete_queued_challenge_is_superseded_on_same_session_rebind(self):
        api, _ = self.start()
        with self.store.connect() as db:
            previous = db.execute("SELECT challenge_message FROM duty_readiness WHERE member='coder'").fetchone()[0]
        api.control.bind("coder", "research", self.order, self.job, "xo", "New context")
        duty.current(self.store, "coder")
        with self.store.connect() as db:
            self.assertEqual("superseded", db.execute("SELECT delivery FROM messages WHERE id=?", (previous,)).fetchone()[0])

    def test_missing_required_tool_cannot_produce_ready(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        current = api._tools

        def without_skill(scope, **options):
            return [item for item in current(scope, **options) if item["name"] != "skill_read"]

        with patch.object(api, "_tools", side_effect=without_skill):
            result = self.report(api, onboard)
        self.assertEqual("blocked", result["status"])
        self.assertIn("skill_read", next(item["detail"] for item in result["checks"] if item["name"] == "capabilities"))

    def test_agent_blockers_are_retained_and_identical_retries_are_idempotent(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        result = self.report(api, onboard, ["Provider connection failed"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual(result, self.report(api, onboard, ["Provider connection failed"]))
        self.assertEqual(1, len(self.store.events(kind="readiness_blocked")["events"]))

    def test_stopped_ready_session_is_not_currently_ready(self):
        api, generation = self.start()
        self.report(api)
        self.store.session("coder", generation=generation, state="stopped", desired="stopped", pid=None)
        duty.tick(self.store)
        self.assertEqual("blocked", duty.current(self.store, "coder")["status"])

    def test_public_receipts_remove_acknowledgement_inputs(self):
        api, _ = self.start()
        onboard = api.call("onboard", {})
        result = duty.public(onboard["readiness"])
        self.assertNotIn("challenge", result)
        self.assertNotIn("onboarding_revision", result)
        self.assertEqual("starting", result["status"])
        self.assertIsNone(duty.public(None))

    def test_unrelated_unscoped_messages_are_not_readiness_messages(self):
        self.start()
        with self.store.connect() as db:
            self.store._send(db, "owner", "coder", "Hidden unscoped message", key="unrelated", work_order=None, job=None)
        result = duty.messages(self.store, "coder")
        self.assertEqual(1, len(result["messages"]))
        self.assertIn("Readiness challenge.", result["messages"][0]["text"])
        self.assertNotIn("Hidden", json.dumps(result))
        self.assertEqual([], duty.messages(self.store, "idle")["messages"])

    def test_parent_never_receives_queued_ready_from_replaced_generation(self):
        api, first = self.start()
        receipt = self.report(api)["receipt"]
        self.store.session("coder", generation=first, state="stopped", desired="stopped", pid=None)
        self.start()
        self.assertEqual("starting", duty.current(self.store, "coder")["status"])
        self.assertIsNone(self.store.take_delivery("xo", scoped=True))
        with self.store.connect() as db:
            self.assertEqual("superseded", db.execute("SELECT delivery FROM messages WHERE id=?", (int(receipt[8:]),)).fetchone()[0])

    def test_stopped_session_cannot_deliver_old_ready_even_before_timeout_tick(self):
        api, generation = self.start()
        receipt = self.report(api)["receipt"]
        self.store.session("coder", generation=generation, state="stopped", desired="stopped", pid=None)
        self.assertIsNone(self.store.take_delivery("xo", scoped=True))
        duty.tick(self.store)
        failure = self.store.take_delivery("xo", scoped=True)
        self.assertIn("unable to report ready", failure["body"])
        self.assertNotEqual(int(receipt[8:]), failure["id"])
        with self.store.connect() as db:
            self.assertEqual("superseded", db.execute("SELECT delivery FROM messages WHERE id=?", (int(receipt[8:]),)).fetchone()[0])

    def test_parent_snapshot_is_current_inside_an_existing_write_transaction(self):
        api, generation = self.start()
        self.report(api)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = duty.snapshot_in(self.store, db, "coder")
            self.assertEqual("ready", snapshot["status"])
            self.assertNotIn("challenge", snapshot)
            db.execute("UPDATE members SET desired='stopped' WHERE name='coder'")
            self.assertEqual("blocked", duty.snapshot_in(self.store, db, "coder")["status"])
            self.assertEqual("ready", db.execute("SELECT status FROM duty_readiness WHERE member='coder'").fetchone()[0])

    def test_live_snapshots_and_retained_updates_agree_on_lifecycle_changes(self):
        def observed(expected):
            with self.store.connect() as db:
                self.assertEqual(expected, duty.snapshot_in(self.store, db, "coder")["status"])
            duty.tick(self.store)
            with self.store.connect() as db:
                self.assertEqual(expected, duty.snapshot_in(self.store, db, "coder")["status"])
                self.assertEqual(expected, db.execute(
                    "SELECT status FROM duty_readiness WHERE member='coder' ORDER BY id DESC LIMIT 1"
                ).fetchone()[0])

        api, first = self.start()
        observed("starting")
        self.report(api)
        observed("ready")
        self.store.session("coder", generation=first, state="stopped", desired="stopped", pid=None)
        observed("blocked")

        api, second = self.start()
        observed("starting")
        with self.store.connect() as db:
            db.execute("UPDATE duty_readiness SET deadline=0 WHERE member='coder' AND generation=?", (second,))
        observed("timed_out")

        self.store.session("coder", generation=second, state="stopped", desired="stopped", pid=None)
        api, _ = self.start()
        api.control.bind("coder", "research", self.order, self.job, "xo", "Changed assignment")
        observed("superseded")
        self.assertEqual("starting", duty.current(self.store, "coder")["status"])
        observed("starting")

    def test_standby_rebind_during_onboarding_cannot_certify_stale_context(self):
        team_path = self.state / "team.json"
        team = json.loads(team_path.read_text())
        team["co"] = "owner"
        for item in team["members"]:
            if item["name"] == "idle":
                item["harness"] = "codex"
        team_path.write_text(json.dumps(team))
        with self.store.connect() as db:
            db.execute("INSERT INTO standby_bindings(member,role,reports_to,revision,deadline,created,updated) "
                       "VALUES ('idle','xo','owner',1,?,'now','now')", (time.time() + 30,))
        api, _ = self.start("idle")
        invoke = api.invoke

        def changed_context(name, arguments):
            result = invoke(name, arguments)
            if name == "work_context":
                with self.store.connect() as db:
                    db.execute("UPDATE standby_bindings SET revision=revision+1 WHERE member='idle'")
            return result

        with patch.object(api, "invoke", side_effect=changed_context), self.assertRaisesRegex(ValueError, "current readiness"):
            api.call("onboard", {})
        current = duty.current(self.store, "idle")
        self.assertEqual("starting", current["status"])
        self.assertIsNone(current["onboarding_revision"])
        self.assertEqual("ready", self.report(api)["status"])

    def test_unbound_external_console_creates_no_readiness_request(self):
        self.assertIsNone(duty.current(self.store, "owner"))
        self.assertNotIn("readiness", API(self.state, "owner").call("onboard", {}))
