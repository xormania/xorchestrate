"""Native investigators release their process before a persisted continuation runs."""

import json
import subprocess
import sys
import unittest

from fixtures.decision_gates import CompletionDecisionFixture

from devtools.decisions import Decisions
from devtools.failures import Failure, failure_info


class DecisionResumeTests(unittest.TestCase):
    setUp = CompletionDecisionFixture.setUp
    configure = CompletionDecisionFixture.configure
    report = CompletionDecisionFixture.report
    operation = CompletionDecisionFixture.operation
    assignment = CompletionDecisionFixture.assignment
    ambiguous = CompletionDecisionFixture.ambiguous
    resolve = CompletionDecisionFixture.resolve

    def investigate(self):
        proof = self.report()
        self.provider.side_effect = self.ambiguous
        with self.assertRaises(Failure) as blocked:
            self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Accept verified work"})
        self.assertEqual("decision_pending", failure_info(blocked.exception)["reason"])
        with self.store.connect() as db:
            pending = dict(db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone())
        # A real process remains alive until this test releases it. No native
        # harness or authenticated provider is started by this fixture.
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        self.addCleanup(process.wait)
        self.addCleanup(process.terminate)
        self.addCleanup(process.stdin.close)
        team = json.loads((self.state / "team.json").read_text())
        member = next(row for row in team["members"] if row["name"] == pending["arbitrator"])
        member.update(harness="claude", binary=sys.executable)
        (self.state / "team.json").write_text(json.dumps(team))
        with self.store.connect() as db:
            db.execute("UPDATE members SET harness='claude',state='active',desired='running',pid=? WHERE name=?",
                       (process.pid, pending["arbitrator"]))
        return pending, proof, process

    def release_process(self, pending, process):
        process.stdin.close()
        process.wait(timeout=5)
        self.store.session(pending["arbitrator"], state="stopped", desired="stopped", pid=None)

    def test_live_native_arbitrator_waits_then_tick_resumes_exactly_once(self):
        pending, proof, process = self.investigate()
        result = self.resolve(pending, proof)
        self.assertEqual("waiting_runtime", result["continuation"]["state"])
        self.assertIsNone(process.poll())
        Decisions(self.store, "xo").tick()
        with self.store.connect() as db:
            self.assertNotEqual("done", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])
            self.assertEqual("waiting_runtime", json.loads(db.execute("SELECT resumed FROM decisions WHERE id=?", (pending["id"],)).fetchone()[0])["state"])
        self.release_process(pending, process)
        self.xo.commands.tick()
        self.xo.commands.tick()
        with self.store.connect() as db:
            self.assertEqual("done", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM deliveries WHERE job=?", (self.job,)).fetchone()[0])
            self.assertEqual("completed", json.loads(db.execute("SELECT resumed FROM decisions WHERE id=?", (pending["id"],)).fetchone()[0])["state"])
        applied = self.store.events(kind="decision_applied")["events"]
        self.assertEqual(1, sum(row["data"]["decision"] == pending["id"] for row in applied))

    def test_changed_work_during_native_shutdown_blocks_persisted_resume(self):
        pending, proof, process = self.investigate()
        self.assertEqual("waiting_runtime", self.resolve(pending, proof)["continuation"]["state"])
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET objective='Changed while investigator stopped' WHERE id=?", (self.job,))
        self.release_process(pending, process)
        Decisions(self.store, "xo").tick()
        Decisions(self.store, "xo").tick()
        with self.store.connect() as db:
            self.assertNotEqual("done", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])
            outcome = json.loads(db.execute("SELECT resumed FROM decisions WHERE id=?", (pending["id"],)).fetchone()[0])
            self.assertEqual("blocked", outcome["state"])
            self.assertEqual("decision_stale", outcome["failure"]["reason"])
        self.assertEqual(1, len(self.store.events(kind="decision_applied")["events"]))


if __name__ == "__main__":
    unittest.main()
