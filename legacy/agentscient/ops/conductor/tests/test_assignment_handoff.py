"""Binding a dispatched worker and offering its work are one core operation."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from test_coordination import BRANCH, LEAD, _Scratch

from ops.testkit import World  # noqa: F401 - _Scratch owns the world


class AssignmentHandoff(_Scratch):
    def setUp(self):
        super().setUp()
        self.ok("bind", "--roles", "lead,dispatcher")
        self.lock()

    def draft(self):
        return self.ok("draft", "a repair", "--outcome", "submitted evidence",
                       "--revision", "a1b2c3d").split()[-1]

    def bind_worker(self, aid, session="worker-one", *extra):
        return self.run_tool("bind-for", "--session", session, "--roles", "worker",
                             "--for", aid, *extra)

    def assignment(self, aid):
        with sqlite3.connect(self.store) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute(
                "SELECT a.*, s.place FROM assignments a JOIN subjects s ON s.id=a.id "
                "WHERE a.id=?", (int(aid[2:]),)).fetchone())

    def state(self):
        with sqlite3.connect(self.store) as conn:
            return {table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                    for table in ("participants", "assignments", "subjects", "marking")}

    def claim(self, aid, session="worker-one"):
        return self.ok("claim", aid, "--branch", BRANCH,
                       "--generation", self.gen(aid), session=session)

    def test_drafted_work_can_be_claimed_and_submitted_without_a_hand_offer(self):
        aid = self.draft()
        bound = self.bind_worker(aid)
        self.assertEqual(bound.exit, 0, bound.stderr)
        row = self.assignment(aid)
        self.assertEqual((row["place"], row["recipient"], row["generation"]),
                         ("offered", "PT-2", 2))
        self.claim(aid)
        self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session="worker-one")
        row = self.assignment(aid)
        self.assertEqual(row["place"], "submitted")
        self.assertEqual(row["submitted_by"], 2)

    def test_a_role_offer_is_addressed_to_the_worker_before_it_runs(self):
        aid = self.draft()
        self.ok("offer", aid, "--to", "worker", "--generation", self.gen(aid))
        bound = self.bind_worker(aid)
        self.assertEqual(bound.exit, 0, bound.stderr)
        row = self.assignment(aid)
        self.assertEqual((row["place"], row["recipient"], row["generation"]),
                         ("offered", "PT-2", 3))
        self.ok("bind", "--roles", "worker", session="unrelated")
        other = self.run_tool("claim", aid, "--branch", BRANCH,
                              "--generation", self.gen(aid), session="unrelated")
        self.assertEqual(other.exit, 2)
        self.assertIn("recipient:", other.stderr)
        self.claim(aid)

    def test_reassign_recovers_an_offered_assignment_using_the_existing_verb(self):
        aid = self.draft()
        self.assertEqual(self.bind_worker(aid).exit, 0)
        self.ok("reassign", aid, "--to", "worker", "--why", "replace failed startup",
                "--generation", self.gen(aid))
        second = self.bind_worker(aid, "worker-two")
        self.assertEqual(second.exit, 0, second.stderr)
        self.assertEqual(self.assignment(aid)["recipient"], "PT-3")
        stale = self.run_tool("claim", aid, "--branch", BRANCH,
                              "--generation", self.gen(aid), session="worker-one")
        self.assertEqual(stale.exit, 2)
        self.claim(aid, "worker-two")

    def test_owned_or_finished_work_does_not_mint_an_unusable_worker(self):
        for place in ("wrong-role", "offered", "held", "submitted"):
            with self.subTest(place=place):
                aid = self.draft()
                owner = "owner-" + place
                self.ok("bind", "--roles", "worker", session=owner)
                with sqlite3.connect(self.store) as conn:
                    pid = conn.execute("SELECT id FROM participants WHERE session_id=?",
                                       (owner,)).fetchone()[0]
                recipient = "verifier" if place == "wrong-role" else f"PT-{pid}"
                self.ok("offer", aid, "--to", recipient, "--generation", self.gen(aid))
                if place in ("held", "submitted"):
                    self.claim(aid, owner)
                if place == "submitted":
                    self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                            "--generation", self.gen(aid), session=owner)
                before = self.state()
                refused = self.bind_worker(aid, "replacement-" + place)
                self.assertEqual(refused.exit, 2, refused.stdout + refused.stderr)
                self.assertEqual(self.state(), before)

    def test_failed_credential_enrollment_rolls_back_the_entire_handoff(self):
        aid = self.draft()
        before = self.state()
        refused = self.bind_worker(aid, "worker-one", "--capability-digest", "invalid")
        self.assertEqual(refused.exit, 2)
        self.assertIn("capability:", refused.stderr)
        self.assertEqual(self.state(), before)

    def test_failed_transition_rolls_back_identity_recipient_and_generation(self):
        aid = self.draft()
        with sqlite3.connect(self.store) as conn:
            conn.execute("""CREATE TRIGGER fail_offer BEFORE INSERT ON marking
                WHEN NEW.transition = 'offer' BEGIN
                SELECT RAISE(ABORT, 'planted marking failure'); END""")
        before = self.state()
        failed = self.bind_worker(aid)
        self.assertNotEqual(failed.exit, 0)
        self.assertIn("planted marking failure", failed.stderr)
        self.assertEqual(self.state(), before)
        with sqlite3.connect(self.store) as conn:
            conn.execute("DROP TRIGGER fail_offer")
        self.assertEqual(self.bind_worker(aid).exit, 0)
        self.claim(aid)

    def test_competing_dispatches_have_one_complete_winner(self):
        aid = self.draft()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.bind_worker, aid, session)
                       for session in ("worker-one", "worker-two")]
            results = [future.result(timeout=60) for future in futures]
        self.assertEqual(sorted(result.exit for result in results), [0, 2], results)
        with sqlite3.connect(self.store) as conn:
            workers = conn.execute("SELECT session_id FROM participants WHERE bound_for=?",
                                   (int(aid[2:]),)).fetchall()
        self.assertEqual(len(workers), 1)
        self.claim(aid, workers[0][0])

    def test_dispatcher_can_handoff_without_acquiring_the_coordinator_role(self):
        aid = self.draft()
        self.ok("bind", "--roles", "worker", session="dispatcher")
        self.ok("grant", "PT-2", "--roles", "dispatcher", session=LEAD)
        bound = self.run_tool("bind-for", "--session", "worker-one", "--roles", "worker",
                              "--for", aid, session="dispatcher")
        self.assertEqual(bound.exit, 0, bound.stderr)
        self.claim(aid)
