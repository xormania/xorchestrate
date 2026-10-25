"""The initial roll call reuses doctor under one bounded child-process budget."""

import json
import os
import select
import signal
import subprocess
import time
import unittest
from unittest.mock import patch

from fixtures.commands import CommandFixture
from support import until

from devtools.readiness import doctor, startup_preflight


class StartupPreflightTests(CommandFixture, unittest.TestCase):
    def script(self, source):
        path = self.root / "doctor_fixture.py"
        path.write_text(source)
        return patch("devtools.readiness.ENTRY", path)

    def test_command_only_team_does_not_require_unconfigured_php_or_project(self):
        result = doctor(self.state, startup=True)
        self.assertEqual("preflight_passed", result["status"], result)
        checks = {row["name"]: row for row in result["checks"]}
        self.assertEqual("not_checked", checks["project"]["status"])
        self.assertNotIn("locked PHP platform", checks)
        self.assertNotIn("Serena image", checks)
        self.assertEqual("not established by preflight", result["authenticated_acceptance"])
        wrapped = startup_preflight(self.state, 5)
        self.assertEqual(result, wrapped)

    def test_keeps_failed_checks_and_rejects_malformed_or_failed_worker_output(self):
        failure = {"status": "blocked", "checks": [{"name": "dependency", "status": "failed", "detail": "Unavailable"}]}
        with self.script("import sys\nprint(" + repr(json.dumps(failure)) + ")\nsys.exit(2)\n"):
            self.assertEqual(failure, startup_preflight(self.state, 2))
        for source in ("print('not JSON')", "print('{}')", "import sys\nsys.exit(1)",
                       "print('{\"status\":\"blocked\",\"checks\":[]}')"):
            with self.subTest(source=source), self.script(source):
                result = startup_preflight(self.state, 2)
                self.assertEqual("blocked", result["status"])
                self.assertIn("valid bounded result", result["checks"][0]["detail"])

    def timeout_probe(self, *, detached):
        pid_file = self.root / "probe.pid"
        source = ("import subprocess,sys,time\nfrom pathlib import Path\n"
                  "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session="
                  + repr(detached) + ")\n"
                  "Path(" + repr(str(pid_file)) + ").write_text(str(child.pid))\n"
                  "time.sleep(30)\n")
        launch = subprocess.Popen
        worker = pidfd = started = None

        def synchronized_launch(*args, **kwargs):
            nonlocal worker, pidfd, started
            worker = launch(*args, **kwargs)
            # Begin the short timeout only after the fixture owns its child.
            # Slow CI scheduling must not turn this into a startup-speed test.
            until(lambda: pid_file.is_file() and pid_file.read_text().isdigit())
            pidfd = os.pidfd_open(int(pid_file.read_text()))
            started = time.monotonic()
            return worker

        try:
            with self.script(source), patch("devtools.readiness.subprocess.Popen", side_effect=synchronized_launch):
                result = startup_preflight(self.state, 0.2)
            elapsed = time.monotonic() - started
            child_dead = bool(select.select([pidfd], [], [], 0 if detached else 3)[0])
            return result, elapsed, child_dead, worker.returncode
        finally:
            if worker is not None:
                if worker.poll() is None:
                    os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(timeout=3)
            if pidfd is None and pid_file.is_file() and pid_file.read_text().isdigit():
                try:
                    pidfd = os.pidfd_open(int(pid_file.read_text()))
                except ProcessLookupError:
                    pass
            if pidfd is not None:
                try:
                    try:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.assertTrue(select.select([pidfd], [], [], 3)[0])
                finally:
                    os.close(pidfd)

    def test_deadline_kills_probe_descendants_and_reaps_the_worker(self):
        result, elapsed, child_dead, returncode = self.timeout_probe(detached=False)
        self.assertLess(elapsed, 3)
        self.assertTrue(child_dead)
        self.assertEqual(-signal.SIGKILL, returncode)
        self.assertEqual("blocked", result["status"])
        self.assertIn("exceeded", result["checks"][0]["detail"])

    def test_detached_probe_cannot_hold_timeout_cleanup_open(self):
        result, elapsed, child_dead, returncode = self.timeout_probe(detached=True)
        self.assertLess(elapsed, 3)
        self.assertFalse(child_dead)
        self.assertEqual(-signal.SIGKILL, returncode)
        self.assertEqual("blocked", result["status"])
        self.assertIn("worker was stopped", result["checks"][0]["detail"])

    def test_invalid_budget_does_not_start_a_process(self):
        with patch("devtools.readiness.subprocess.Popen") as launch:
            for timeout in (0, -1, True, float("inf"), float("nan")):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    startup_preflight(self.state, timeout)
            launch.assert_not_called()
