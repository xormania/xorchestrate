"""Concurrent revisions supersede one investigation without serializing other subjects."""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from devtools.api import API
from devtools.decisions import Decisions
from devtools.failures import Failure, failure_info
from devtools.launch import initialize
from devtools.workspaces import Workspaces


class DecisionSerializationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        self.state = root / "state"
        initialize(self.state, workspace, ["owner:external", "xo:external",
                                           "investigator:external", "other:external"])
        team = json.loads((self.state / "team.json").read_text())
        team["project"] = {"configuration_file": "policy.json"}
        (self.state / "team.json").write_text(json.dumps(team))
        self.policy = {"version": 1, "project": {
            "features": {"completion": True, "selection": False},
            "connection": {"credential": {"env": "AGENTSCIENT_TEST_DECISION_KEY"}},
        }}
        (self.state / "policy.json").write_text(json.dumps(self.policy))
        self.operator = API(self.state, "owner", operator=True)
        self.store = self.operator.store
        self.order = self.operator.work.work_order_create(
            title="Decision serialization", objective="Investigate current evidence", done_when="One current decision"
        )["work_order"]["id"]
        self.operator.control.bind("owner", "co", self.order)
        self.operator.control.bind("xo", "xo", self.order, reports_to="owner")
        self.worker = threading.local()

    @staticmethod
    def answer(state, questions, model):
        return {"model": model, "answers": {name: {"type": "noul", "noul": 0.75} for name in questions},
                "usage": {"input_tokens": 1, "output_tokens": 1}}

    def select(self, target, identity):
        self.worker.identity = identity
        return Decisions(self.store, "xo").select(
            "completion", {"target": target, "proof": identity},
            {"complete": {"type": "noul", "instructions": "Is the retained proof complete?"}},
            identity=identity,
        )

    def pending(self, target, identity):
        with self.assertRaises(Failure) as blocked:
            self.select(target, identity)
        self.assertEqual("decision_pending", failure_info(blocked.exception)["reason"])
        return failure_info(blocked.exception)["details"]["decision"]

    def before_lock(self, identity, entered, release):
        original = Workspaces.lock

        @contextmanager
        def lock(workspaces, name, timeout=0):
            if name.startswith("decision-") and getattr(self.worker, "identity", None) == identity:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Decision fixture did not release the waiting request")
            with original(workspaces, name, timeout=timeout):
                yield

        return patch.object(Workspaces, "lock", lock)

    def test_changed_identity_retires_concurrent_obsolete_investigation(self):
        evaluating, finish_evaluation = threading.Event(), threading.Event()
        newer_at_lock, acquire_newer_lock = threading.Event(), threading.Event()

        def evaluate(state, questions, model):
            if state["proof"] == "old":
                evaluating.set()
                if not finish_evaluation.wait(5):
                    raise AssertionError("Decision fixture did not release the first evaluation")
            return self.answer(state, questions, model)

        with patch("devtools.advice.Jev.evaluate", side_effect=evaluate) as provider:
            with self.before_lock("new", newer_at_lock, acquire_newer_lock):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(self.pending, "same-subject", "old")
                    try:
                        self.assertTrue(evaluating.wait(5))
                        newer = pool.submit(self.pending, "same-subject", "new")
                        # Before the fix, the newer request has already pruned here,
                        # while the first request has not yet inserted its decision.
                        self.assertTrue(newer_at_lock.wait(5))
                        finish_evaluation.set()
                        old_id = first.result(timeout=10)
                    finally:
                        finish_evaluation.set()
                        acquire_newer_lock.set()
                    new_id = newer.result(timeout=10)
        self.assertEqual(2, provider.call_count)
        self.assertNotEqual(old_id, new_id)
        with self.store.connect() as db:
            old = db.execute("SELECT * FROM decisions WHERE id=?", (old_id,)).fetchone()
            current = db.execute("SELECT * FROM decisions WHERE id=?", (new_id,)).fetchone()
            self.assertEqual("stale", old["state"])
            self.assertEqual("pending", current["state"])
            self.assertEqual(1, db.execute("SELECT count(*) FROM decisions WHERE state='pending'").fetchone()[0])
            self.assertEqual("cancelled", db.execute("SELECT status FROM role_assignments WHERE id=?", (old["assignment"],)).fetchone()[0])
            self.assertIsNone(db.execute("SELECT 1 FROM member_bindings WHERE assignment_id=?", (old["assignment"],)).fetchone())
            self.assertEqual(1, db.execute("SELECT count(*) FROM member_bindings WHERE role='arbitrator'").fetchone()[0])
            self.assertEqual(current["arbitrator"], db.execute("SELECT member FROM member_bindings WHERE assignment_id=?", (current["assignment"],)).fetchone()[0])

    def test_different_subjects_can_evaluate_concurrently(self):
        evaluating = threading.Barrier(2)

        def evaluate(state, questions, model):
            evaluating.wait(timeout=5)
            return self.answer(state, questions, model)

        with patch("devtools.advice.Jev.evaluate", side_effect=evaluate) as provider:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.pending, "first-subject", "first-proof")
                other = pool.submit(self.pending, "other-subject", "other-proof")
                self.assertNotEqual(first.result(timeout=10), other.result(timeout=10))
        self.assertEqual(2, provider.call_count)
        with self.store.connect() as db:
            self.assertEqual(2, db.execute("SELECT count(*) FROM decisions WHERE state='pending'").fetchone()[0])
            self.assertEqual(2, db.execute("SELECT count(*) FROM member_bindings WHERE role='arbitrator'").fetchone()[0])

    def test_waiting_request_rechecks_context_and_policy_before_provider_call(self):
        for changed in ("context", "policy"):
            with self.subTest(changed=changed):
                at_lock, acquire_lock = threading.Event(), threading.Event()
                with patch("devtools.advice.Jev.evaluate", side_effect=self.answer) as provider:
                    with self.before_lock("waiting", at_lock, acquire_lock):
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            waiting = pool.submit(self.select, "same-subject", "waiting")
                            try:
                                self.assertTrue(at_lock.wait(5))
                                if changed == "context":
                                    with self.store.connect() as db:
                                        db.execute("UPDATE work_orders SET objective=? WHERE id=?", ("Changed objective", self.order))
                                else:
                                    self.policy["project"]["decisions"] = {"failure_mode": "block"}
                                    (self.state / "policy.json").write_text(json.dumps(self.policy))
                            finally:
                                acquire_lock.set()
                            with self.assertRaises(Failure) as rejected:
                                waiting.result(timeout=10)
                            self.assertEqual("decision_stale", failure_info(rejected.exception)["reason"])
                provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
