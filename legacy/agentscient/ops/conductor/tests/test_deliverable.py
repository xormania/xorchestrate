"""The deliverable lock lever: set, show, clear; identity for writes; the wire."""

from __future__ import annotations

import contextlib
import fcntl
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_instrument import load

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
LEVER = ROOT / "ops" / "conductor" / "tools" / "deliverable.py"
OWNER = "Owner <owner@example.test>"


class TheLockLever(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="deliverable-")
        self.addCleanup(self.world.close)
        self._measures = 0
        conf = self.world.plant_text(
            self.world.path("lane.conf"), "LANE_NAME='testlane'\n",
        )
        self.conf = conf
        self.lock = self.world.xdg_state / "testlane" / "deliverable.json"

    def _env(self, identity=True, agent=OWNER):
        extra = {"LANE_CONF": str(self.conf)}
        if identity:
            extra["WF_AGENT"] = agent
        return self.world.env(extra=extra)

    def run_lever(self, *args, identity=True, agent=OWNER):
        return run(
            [sys.executable, str(LEVER), *args],
            cwd=self.world.cwd,
            env=self._env(identity=identity, agent=agent),
            timeout=60,
        )

    def test_set_writes_the_lock_and_show_reads_it(self):
        proc = self.run_lever("set", "lane/a", "lane/b", "--why", "the slices")
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = json.loads(self.lock.read_text())
        self.assertEqual(data["branches"], ["lane/a", "lane/b"])
        self.assertEqual(data["why"], "the slices")
        self.assertEqual(data["by"], "Owner <owner@example.test>")
        self.assertRegex(data["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        shown = self.run_lever("show", identity=False)
        self.assertEqual(shown.exit, 0)
        self.assertIn("lane/a", shown.stdout)

    def test_remove_lifts_one_session_branch_and_leaves_the_others(self):
        """Owner, 2026-09-13 ("minimize parallel work conflicts"; LD-28): a
        session that lands removes its own branch; the lock kept naming
        every landed branch because clear was the only exit and clear wipes
        every session's entry at once."""
        self.run_lever("set", "lane/session-a", "--why", "session a")
        self.run_lever("add", "lane/session-b", "--why", "session b")
        proc = self.run_lever("remove", "lane/session-a")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "removed lane/session-a; locked lane/session-b")
        data = json.loads(self.lock.read_text())
        self.assertEqual(data["branches"], ["lane/session-b"])
        self.assertIn("session b", data["why"])
        again = self.run_lever("remove", "lane/session-a")
        self.assertEqual(again.exit, 2, again.stdout)
        self.assertRegex(again.stderr.strip(), r"^absent: expected .+; found .+; needed .+$")
        last = self.run_lever("remove", "lane/session-b")
        self.assertEqual(last.exit, 0, last.stderr)
        self.assertEqual(last.stdout.strip(), "removed lane/session-b; no lock")
        self.assertFalse(self.lock.exists(), "the last branch removed lifts the lock")
        self.assertEqual(self.run_lever("show", identity=False).stdout.strip(), "no lock")
        no_identity = self.run_lever("remove", "lane/session-b", identity=False)
        self.assertEqual(no_identity.exit, 2)
        self.assertRegex(no_identity.stderr.strip(), r"^identity: ")

    def test_add_names_another_session_branch_beside_the_first(self):
        """Owner, 2026-09-12: "as long you account for the fact that i'll
        have multiple sessions running soon". Two sessions on one host share
        this lock; the second adds its branch, it does not replace the
        first's."""
        self.run_lever("set", "lane/session-a", "--why", "session a")
        proc = self.run_lever("add", "lane/session-b", "--why", "session b")
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = json.loads(self.lock.read_text())
        self.assertEqual(data["branches"], ["lane/session-a", "lane/session-b"])
        self.assertIn("session a", data["why"])
        self.assertIn("session b", data["why"])
        again = self.run_lever("add", "lane/session-b", "--why", "session b")
        self.assertEqual(again.exit, 0, again.stderr)
        self.assertEqual(json.loads(self.lock.read_text())["branches"],
                         ["lane/session-a", "lane/session-b"], "add is idempotent")
        fresh = self.run_lever("clear")
        self.assertEqual(fresh.exit, 0)
        proc = self.run_lever("add", "lane/session-c", "--why", "session c")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(json.loads(self.lock.read_text())["branches"], ["lane/session-c"],
                         "add on no lock sets it")
        self.assertEqual(self.run_lever("add", "--why", "x").exit, 2)
        self.assertEqual(self.run_lever("add", "lane/d").exit, 2)

    def test_writers_wait_for_the_lock_file_so_parallel_sessions_lose_no_entry(self):
        """Code review 20260913T105222Z-review-grok-00b782 (Grok), finding 2 on
        PR #197: set, add, remove and clear read, change and write one
        host-wide file with no lock, and a concurrent remove and add lost
        lane/b in 3 of 60 trials at bdf8cf0c. Every writer holds an exclusive
        flock on deliverable.json.lock beside the lock while it reads and
        writes, so a writer that starts while another holds it waits."""
        self.run_lever("set", "lane/a", "--why", "a")
        before = self.lock.read_text()
        guard = self.lock.with_name("deliverable.json.lock")
        env = self._env(identity=True)
        for args in (("add", "lane/b", "--why", "b"), ("remove", "lane/a")):
            with self.subTest(args=args):
                held = open(guard, "a+")  # noqa: SIM115 - held across the child's start
                fcntl.flock(held, fcntl.LOCK_EX)
                child = subprocess.Popen(
                    [sys.executable, str(LEVER), *args],
                    env=env,
                    cwd=str(self.world.cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.world.own(child)
                try:
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        child.wait(timeout=1.0)
                    waited = child.poll() is None
                    unchanged = self.lock.read_text() == before
                finally:
                    held.close()
                    _out, err = child.communicate(timeout=30)
                self.assertTrue(waited, "the writer went ahead while another process held the lock")
                self.assertTrue(unchanged, "the lock changed while another process held it")
                self.assertEqual(child.returncode, 0, err)
                before = self.lock.read_text()
        self.assertEqual(json.loads(self.lock.read_text())["branches"], ["lane/b"])

    def measure_tool(self, text: str) -> str:
        """A measure command: prints ``text`` and exits 0."""
        self._measures += 1
        path = self.world.tools.plant(f"measure-{self._measures}", stdout=text, exit=0)
        return f"{sys.executable} {path}"

    def test_recorded_measurement_has_provenance_but_unknown_inputs_never_authorize(self):
        """PR-01: observations display history, not a fresh acceptance decision."""
        measure = self.measure_tool("on_kit=226/226\n")
        self.assertEqual(self.run_lever("add", "lane/x", "--why", "the target",
                         "--measure", measure, "--until", "on_kit=226/226").exit, 0)
        before = self.lock.read_bytes()
        shown = self.run_lever("show", identity=False)
        self.assertEqual(shown.exit, 0, shown.stderr)
        self.assertIn("measurement absent", shown.stdout)
        self.assertEqual(before, self.lock.read_bytes())
        self.assertEqual(self.run_lever("done", "lane/x").exit, 0)
        data = json.loads(self.lock.read_text())
        recorded = data["targets"]["lane/x"]["measurement"]
        self.assertEqual(recorded["outcome"], "met")
        self.assertEqual(recorded["source"], str(ROOT))
        self.assertIn("candidate", recorded)
        self.assertRegex(recorded["at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(recorded["measure"], measure)
        self.assertEqual(recorded["by"], OWNER)
        before = (self.lock.read_bytes(), self.lock.stat().st_mtime_ns)
        for args in ((), ("--unmeasured",)):
            with self.subTest(args=args):
                shown = self.run_lever("show", *args, identity=False)
                self.assertEqual(shown.exit, 0, shown.stderr)
                self.assertIn("reading unmeasured; unmeasured", shown.stdout)
                self.assertIn("applicability unknown", shown.stdout)
                self.assertIn(recorded["at"], shown.stdout)
                self.assertNotRegex(shown.stdout, r"(?m)^target .*; met\b")
                self.assertEqual(before, (self.lock.read_bytes(), self.lock.stat().st_mtime_ns))
        self.assertEqual(len(self.world.tools.calls(f"measure-{self._measures}")), 1)
        # A moved candidate and even a hand-written pass remain non-authoritative.
        data["targets"]["lane/x"]["measurement"]["candidate"] = "0" * 40
        self.lock.write_text(json.dumps(data))
        shown = self.run_lever("show", identity=False)
        self.assertIn("applicability unknown", shown.stdout)
        self.assertNotRegex(shown.stdout, r"(?m)^target .*; met\b")
        data["targets"]["lane/x"]["measurement"]["until"] = "other objective"
        self.lock.write_text(json.dumps(data))
        stale = self.run_lever("show", identity=False)
        self.assertIn("applicability stale", stale.stdout)
        self.assertNotRegex(stale.stdout, r"(?m)^target .*; met\b")
        self.run_lever("set", "lane/x", "--why", "revised target",
                       "--measure", measure, "--until", "different")
        self.assertNotIn("measurement", json.loads(self.lock.read_text())["targets"]["lane/x"])

    def test_absent_and_invalid_locks_are_distinct_and_observation_never_repairs(self):
        self.assertEqual(self.run_lever("show", identity=False).stdout.strip(), "no lock")
        self.assertFalse(self.lock.parent.exists())
        self.lock.parent.mkdir(parents=True)
        for text in ("broken json", "[]", '{"branches": "lane/x"}',
                     '{"branches": ["lane/x"], "targets": {"lane/x": {}}}'):
            with self.subTest(text=text):
                self.lock.write_text(text)
                result = self.run_lever("show", identity=False)
                self.assertEqual(result.refusal().cls, "store")
                self.assertEqual(self.lock.read_text(), text)
        self.lock.unlink()
        self.lock.mkdir()
        result = self.run_lever("show", identity=False)
        self.assertEqual(result.refusal().cls, "store")
        self.assertIn("unreadable", result.stderr)

    def test_a_target_changed_during_measurement_is_not_overwritten(self):
        measure = self.measure_tool("ok\n")
        self.run_lever("add", "lane/x", "--why", "race fixture", "--measure", measure, "--until", "ok")
        module = load(LEVER)

        def concurrent_retarget(_target):
            data = json.loads(self.lock.read_text())
            data["targets"]["lane/x"]["until"] = "new objective"
            self.lock.write_text(json.dumps(data))
            return True, "ok"

        with mock.patch.object(module, "lock_path", return_value=self.lock), \
                mock.patch.object(module, "identity", return_value=OWNER), \
                mock.patch.object(module, "candidate_ref", return_value="a" * 40, create=True), \
                mock.patch.object(module, "reading", side_effect=concurrent_retarget) as reading:
            with self.assertRaises(module.Refusal) as raised:
                module.do_done(["lane/x"])
            self.assertEqual(raised.exception.kind, "target-changed")
            self.assertEqual(reading.call_count, 1)
        target = json.loads(self.lock.read_text())["targets"]["lane/x"]
        self.assertEqual(target["until"], "new objective")
        self.assertNotIn("measurement", target)

    def test_show_unmeasured_runs_no_measure(self):
        """origin: LD-146

        ``show --unmeasured`` prints every target reading unmeasured and
        runs no measure; a plain ``show`` does the same. Only ``done``
        explicitly measures; unknown display options refuse on the wire."""
        measure = self.measure_tool("on_kit=16/226\n")
        name = f"measure-{self._measures}"
        proc = self.run_lever("add", "lane/x", "--why", "w", "--measure", measure, "--until", "on_kit=226/226")
        self.assertEqual(proc.exit, 0, proc.stderr)
        with self.subTest(id="deliverable-show-unmeasured", origin="LD-146"):
            shown = self.run_lever("show", "--unmeasured", identity=False)
            self.assertEqual(shown.exit, 0, shown.stderr)
            self.assertIn("target lane/x: until on_kit=226/226; reading unmeasured; unmeasured", shown.stdout)
            self.assertEqual(self.world.tools.calls(name), [])
        with self.subTest(id="deliverable-show-bogus-refuses", origin="LD-146"):
            bogus = self.run_lever("show", "--bogus", identity=False)
            self.assertEqual(bogus.exit, 2, bogus.stdout)
            self.assertEqual(bogus.stdout, "")
            self.assertEqual(len(bogus.stderr.strip().splitlines()), 1, bogus.stderr)
            self.assertRegex(bogus.stderr.strip(), r"^arguments: expected .+; found .+; needed .+$")
        with self.subTest(id="deliverable-show-cheap", origin="PR-01"):
            shown = self.run_lever("show", identity=False)
            self.assertIn("target lane/x: until on_kit=226/226; reading unmeasured; unmeasured", shown.stdout)
            self.assertEqual(self.world.tools.calls(name), [])
            measured = self.run_lever("done", "lane/x")
            self.assertEqual(measured.refusal().cls, "target")
            self.assertIn("on_kit=16/226", measured.stderr)
            self.assertEqual(len(self.world.tools.calls(name)), 1)
            recorded = json.loads(self.lock.read_text())["targets"]["lane/x"]["measurement"]
            self.assertEqual(recorded["outcome"], "short")

    def test_measure_without_until_or_until_without_measure_refuses_arguments(self):
        for extra in (("--measure", "true"), ("--until", "x")):
            with self.subTest(extra=extra):
                proc = self.run_lever("add", "lane/x", "--why", "w", *extra)
                self.assertEqual(proc.exit, 2, proc.stdout)
                self.assertRegex(proc.stderr.strip(), r"^arguments: expected .+; found .+; needed .+$")

    def test_done_refuses_target_while_the_reading_is_short_and_prints_it_when_met(self):
        measure = self.measure_tool("on_kit=16/226\n")
        self.run_lever("add", "lane/x", "--why", "w", "--measure", measure, "--until", "on_kit=226/226")
        proc = self.run_lever("done", "lane/x")
        self.assertEqual(proc.exit, 2, proc.stdout)
        self.assertEqual(proc.stdout, "")
        line = proc.stderr.strip()
        self.assertRegex(line, r"^target: expected .+; found .+; needed .+$")
        self.assertIn("on_kit=226/226", line)
        self.assertIn("on_kit=16/226", line)
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1)
        met = self.measure_tool("modules on_kit=226/226\nsecond line\n")
        self.run_lever("add", "lane/x", "--why", "w", "--measure", met, "--until", "on_kit=226/226")
        proc = self.run_lever("done", "lane/x")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "done lane/x: modules on_kit=226/226")

    def test_done_on_a_branch_with_no_target_refuses_target_naming_the_flags(self):
        self.run_lever("add", "lane/x", "--why", "the slices")
        proc = self.run_lever("done", "lane/x")
        self.assertEqual(proc.exit, 2, proc.stdout)
        line = proc.stderr.strip()
        self.assertRegex(line, r"^target: expected .+; found .+; needed .+$")
        self.assertIn("--measure", line)
        self.assertIn("--until", line)
        absent = self.run_lever("done", "lane/nobody")
        self.assertEqual(absent.exit, 2)
        self.assertRegex(absent.stderr.strip(), r"^absent: ")

    def test_a_measure_that_cannot_run_is_unreachable_never_met(self):
        self.run_lever("add", "lane/x", "--why", "w", "--measure", f"{sys.executable} {self.world.tmp}/missing.py",
                       "--until", "on_kit=226/226")
        shown = self.run_lever("show", identity=False)
        self.assertEqual(shown.exit, 0, shown.stderr)
        self.assertIn("target lane/x: until on_kit=226/226; reading unmeasured", shown.stdout)
        proc = self.run_lever("done", "lane/x")
        self.assertEqual(proc.exit, 2, proc.stdout)
        self.assertIn("unreachable", proc.stderr)

    def test_remove_refuses_a_short_target_unless_a_reason_is_given(self):
        measure = self.measure_tool("on_kit=16/226\n")
        self.run_lever("add", "lane/x", "--why", "w", "--measure", measure, "--until", "on_kit=226/226")
        proc = self.run_lever("remove", "lane/x")
        self.assertEqual(proc.exit, 2, proc.stdout)
        self.assertRegex(proc.stderr.strip(), r"^target: expected .+; found .+; needed .+$")
        self.assertTrue(self.lock.exists())
        proc = self.run_lever("remove", "lane/x", "--unmet", "owner 2026-09-15: stop here")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "removed lane/x unmet (owner 2026-09-15: stop here); no lock")
        met = self.measure_tool("on_kit=226/226\n")
        self.run_lever("add", "lane/y", "--why", "w", "--measure", met, "--until", "on_kit=226/226")
        proc = self.run_lever("remove", "lane/y")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "removed lane/y; no lock")

    def test_clear_removes_it_and_show_says_no_lock(self):
        self.run_lever("set", "lane/a", "--why", "x")
        proc = self.run_lever("clear")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.run_lever("show", identity=False).stdout.strip(), "no lock")

    def test_writes_need_identity_and_refuse_on_the_wire(self):
        for args in (("set", "lane/a", "--why", "x"), ("add", "lane/a", "--why", "x"), ("clear",)):
            with self.subTest(args=args):
                proc = self.run_lever(*args, identity=False)
                self.assertEqual(proc.exit, 2)
                self.assertEqual(proc.stdout, "")
                self.assertRegex(proc.stderr.strip(), r"^identity: expected .*; found .*; needed .*$")
                self.assertEqual(len(proc.stderr.strip().splitlines()), 1)

    def test_set_needs_branches_and_a_reason(self):
        for args in (("set",), ("set", "lane/a"), ("set", "--why", "x")):
            with self.subTest(args=args):
                proc = self.run_lever(*args)
                self.assertEqual(proc.exit, 2)
                self.assertTrue(proc.stderr.startswith("arguments: expected"), proc.stderr)

    def test_set_retargets_only_the_branch_it_names_and_refuses_a_stranger(self):
        """Origin T-71: a lead session ran `set` for its closing AAR and the
        lock then read one measure against all three branches, so two other
        sessions read short against a target that was never theirs. set
        re-targets what the lock already names; add introduces a branch."""
        first = self.measure_tool("a=1\n")
        second = self.measure_tool("b=1\n")
        self.run_lever("set", "lane/session-a", "--why", "session a",
                       "--measure", first, "--until", "a=1")
        self.run_lever("add", "lane/session-b", "--why", "session b",
                       "--measure", second, "--until", "b=1")

        third = self.measure_tool("a=2\n")
        proc = self.run_lever("set", "lane/session-a", "--why", "session a",
                              "--measure", third, "--until", "a=2")
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = json.loads(self.lock.read_text())
        self.assertEqual(data["branches"],
                         ["lane/session-a", "lane/session-b"],
                         "set dropped a branch it was not asked about")
        self.assertEqual(data["targets"]["lane/session-a"]["until"], "a=2")
        self.assertEqual(data["targets"]["lane/session-b"]["until"], "b=1",
                         "set re-targeted another session's branch")
        self.assertEqual(data["targets"]["lane/session-b"]["measure"], second)

        stranger = self.run_lever("set", "lane/session-z", "--why", "z")
        self.assertEqual(stranger.exit, 2)
        self.assertEqual(stranger.stdout, "")
        self.assertEqual(len(stranger.stderr.strip().splitlines()), 1)
        self.assertRegex(stranger.stderr.strip(),
                         r"^absent: expected .*; found .*; needed .*$")
        self.assertIn("lane/session-z", stranger.stderr)
        self.assertIn("add", stranger.stderr)
        self.assertEqual(json.loads(self.lock.read_text())["branches"],
                         ["lane/session-a", "lane/session-b"],
                         "a refused set still wrote the lock")

    def test_each_target_records_who_added_it_and_when(self):
        """Origin LD-49 (filed at 6fd0ba3b), beside T-71: a lock that stamps
        one `by` at the top cannot say which session added which branch, and
        add overwrote it, so the last writer owned them all. Each target
        carries its own by and at, and add leaves the lock-level by where it
        found it."""
        other = "Claude Opus 5 (1M context) <noreply@anthropic.com>"
        mine = self.measure_tool("a=1\n")
        theirs = self.measure_tool("b=1\n")
        self.run_lever("set", "lane/session-a", "--why", "session a",
                       "--measure", mine, "--until", "a=1")
        proc = self.run_lever("add", "lane/session-b", "--why", "session b",
                              "--measure", theirs, "--until", "b=1",
                              agent=other)
        self.assertEqual(proc.exit, 0, proc.stderr)

        data = json.loads(self.lock.read_text())
        self.assertEqual(data["targets"]["lane/session-a"]["by"], OWNER)
        self.assertEqual(data["targets"]["lane/session-b"]["by"], other)
        for branch in ("lane/session-a", "lane/session-b"):
            self.assertRegex(data["targets"][branch]["at"],
                             r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(data["by"], OWNER, "add overwrote the lock-level by")

        shown = self.run_lever("show", identity=False)
        self.assertIn("target lane/session-b: until b=1", shown.stdout)
        self.assertIn(f"(by {other})", shown.stdout)


if __name__ == "__main__":
    unittest.main()
