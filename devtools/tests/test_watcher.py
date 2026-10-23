import json
import signal
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fixtures.github import GitHubFixture
from support import fixture_environment, require_process_identity, until

from devtools import processes, watcher


class WatcherTests(unittest.TestCase):
    setUp = GitHubFixture.setUp
    client = GitHubFixture.client
    work = GitHubFixture.work

    @classmethod
    def setUpClass(cls):
        require_process_identity()

    def command(self, action):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("github_fixture.py")), "watch",
             "--state", str(self.directory), "--action", action],
            env=fixture_environment(self.directory, {"FIXTURE_GITHUB_ROOT": self.provider.url}),
            capture_output=True, text=True, timeout=10, check=True,
        )
        return json.loads(result.stdout)

    def test_singleton_survives_session_and_recovers_interrupted_poll_without_duplicate_notices(self):
        client = self.client()
        self.work(client)
        client.invoke("pr_open")
        self.provider.run.update(status="in_progress", conclusion=None)
        watch = client.invoke("ci_watch", run=10)["watch"]
        until(lambda: self.store.inbox("alpha")["messages"])
        current = watcher.status(self.store)
        family = processes.family([processes.identity(client.process.pid)])
        self.assertNotIn(current["pid"], [r["pid"] for r in family])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.command, ["start", "start"]))
        self.assertEqual({current["pid"]}, {r["pid"] for r in results})
        client.close()
        self.assertTrue(watcher.status(self.store)["running"])
        saved = processes.identity(current["pid"])
        processes.signal_owned([saved], signal.SIGKILL)
        until(lambda: not watcher.status(self.store)["running"])
        with self.store.connect() as db:
            db.execute("UPDATE github_watches SET poll_pid=?,next_poll=? WHERE id=?",
                       (saved["pid"], time.time() + 600, watch))
        self.provider.run.update(status="completed", conclusion="success")
        restarted = self.command("start")
        self.assertNotEqual(current["pid"], restarted["pid"])
        messages = until(lambda: rows if len(rows := self.store.inbox("alpha")["messages"]) == 2 else None)
        self.assertIn("passed", messages[-1]["body"])
        self.assertFalse(self.command("stop")["running"])
        self.command("start")
        self.assertEqual(2, len(self.store.inbox("alpha")["messages"]))


if __name__ == "__main__":
    unittest.main()
