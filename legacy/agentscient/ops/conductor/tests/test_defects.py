"""The defect ledger lever: next, add, show, list; one file per row; the wire.

Owner, 2026-09-13 ("fix issue #1 - PR and merge"): lane-defects.md conflicted
on three of five merge-forwards since 2026-09-11 because every session
appended its row at the same line of one table. From LD-36 a row is its own
file under ops/conductor/handoff/lane-defects/, so two sessions' rows merge
as two additions. Until 2026-09-16 the same id minted twice was accepted and
left to conflict as add/add on one file, loudly; that window is closed --
`next` reads every ref, so an id taken on an unmerged branch is not offered
again -- and the conflict remains the backstop for a row written by hand,
where the lever is never asked. One producer (Claude Fable 5.1) under hand
grant 194; the second producer absent, said in the commit. The id reader was
widened by Claude Opus 5 (1M context) under hand grant 293, A-118 row 2, the
second producer absent again and said in that commit.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
LEVER = ROOT / "ops" / "conductor" / "tools" / "defects.py"
PAGE = Path("ops/conductor/handoff/lane-defects.md")
ROWS = Path("ops/conductor/handoff/lane-defects")

HEADER = ("# Lane defects\n\n| id | observed | where | defect | repair | tests |\n"
          "|---|---|---|---|---|---|\n")
ROW = "| LD-{n} | 2026-09-10 | `x.py` | a defect | a repair | a test |\n"


class TheDefectLever(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="defects-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.repo = self.world.cwd
        self.world.git("init", "-q", "-b", "dev", cwd=self.repo)
        self.world.plant_text(
            self.repo / PAGE,
            HEADER + ROW.format(n=1) + ROW.format(n=35),
        )
        self.world.git("add", "-A", cwd=self.repo)
        self.world.git("commit", "-q", "-m", "seed", cwd=self.repo)

    def git(self, *args, cwd=None, expect=0):
        result = self.world.git(*args, cwd=cwd or self.repo, check=False)
        if expect is not None:
            self.assertEqual(result.exit, expect, result.stdout + result.stderr)
        return result

    def run_lever(self, *args, identity=True, cwd=None):
        extra = {}
        if identity:
            extra["WF_AGENT"] = "Owner <owner@example.test>"
        return run(
            [sys.executable, str(LEVER), *args],
            env=self.world.env(extra=extra),
            cwd=str(cwd or self.repo),
            timeout=60,
        )

    def add(self, n_where="`y.py`", **kw):
        return self.run_lever("add", "--where", n_where, "--defect", kw.get("defect", "the defect"),
                              "--repair", kw.get("repair", "the repair"), "--tests", kw.get("tests", "the test"),
                              cwd=kw.get("cwd"), identity=kw.get("identity", True))

    def assert_wire(self, proc, klass):
        self.assertEqual(proc.exit, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "", "a refusal prints nothing on stdout")
        lines = proc.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertRegex(lines[0], rf"^{klass}: expected .+; found .+; needed .+$")

    def test_next_reads_the_table_then_the_rows_directory(self):
        proc = self.run_lever("next", identity=False)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "LD-36", "the table ends at LD-35")
        (self.repo / ROWS).mkdir()
        self.world.plant_text(self.repo / ROWS / "LD-40.md", "# LD-40\n")
        proc = self.run_lever("next", identity=False)
        self.assertEqual(proc.stdout.strip(), "LD-36", "S2 keeps this owner's unfiled reservation")
        self.assertEqual(self.add().stdout.split()[0], "LD-36")
        proc = self.run_lever("next", identity=False)
        self.assertEqual(proc.stdout.strip(), "LD-41", "new allocation counts the later row and the table")

    def test_add_writes_one_file_per_row_and_prints_its_id_and_path(self):
        proc = self.add()
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"LD-36 {ROWS / 'LD-36.md'}")
        text = (self.repo / ROWS / "LD-36.md").read_text()
        self.assertTrue(text.startswith("# LD-36\n"), text)
        for field in ("observed", "where", "defect", "repair", "tests", "by"):
            self.assertRegex(text, rf"(?m)^\| {field} \| .+ \|$", f"field {field}")
        self.assertIn("| by | Owner <owner@example.test> |", text)
        self.assertRegex(text, r"\| observed \| \d{4}-\d{2}-\d{2} \|")
        self.assertIn("| where | `y.py` |", text)
        again = self.add(n_where="`z.py`")
        self.assertEqual(again.stdout.split()[0], "LD-37", "the next add mints the next id")
        self.assertEqual((self.repo / PAGE).read_text(), HEADER + ROW.format(n=1) + ROW.format(n=35),
                         "the page is never written by add")

    def test_add_needs_identity_and_every_field_on_the_wire(self):
        self.assert_wire(self.add(identity=False), "identity")
        proc = self.run_lever("add", "--where", "`y.py`", "--defect", "d", "--repair", "r")
        self.assert_wire(proc, "arguments")
        self.assertIn("--tests", proc.stderr)
        self.assert_wire(self.run_lever("add", "--where", "", "--defect", "d", "--repair", "r", "--tests", "t"),
                         "arguments")
        self.assertFalse((self.repo / ROWS).exists(), "a refused add writes nothing")

    def test_show_and_list_read_the_rows_without_identity(self):
        self.add()
        self.add(n_where="`z.py`", defect="second")
        shown = self.run_lever("show", "LD-37", identity=False)
        self.assertEqual(shown.exit, 0, shown.stderr)
        self.assertIn("| defect | second |", shown.stdout)
        self.assert_wire(self.run_lever("show", "LD-99", identity=False), "absent")
        listed = self.run_lever("list", identity=False)
        self.assertEqual(listed.exit, 0, listed.stderr)
        self.assertEqual([l.split()[0] for l in listed.stdout.strip().splitlines()], ["LD-36", "LD-37"])
        self.assertIn("`z.py`", listed.stdout.splitlines()[1])

    def test_two_sessions_rows_merge_as_two_additions(self):
        """The reason for the shape: rows from two branches never touch.

        Until 2026-09-16 this row asserted the opposite of its first arm:
        that b is handed LD-36 a second time while a's row sits on lane/a
        alone, and that git names the add/add conflict when the branches
        meet. That was the design -- one window left open, with the merge
        as its backstop -- and it failed in practice, because the backstop
        only fires if the two branches meet before the trunk moves. On
        2026-09-16 one session filed LD-81, LD-82 and LD-83 on ids `next`
        called free at 19:25:34Z, the trunk moved 21 commits and landed a
        different LD-81 underneath, and all three were renumbered by hand
        at 20:09:44Z. `next` now reads every ref, so the id is taken and
        the second session is told so at the moment it asks.

        The backstop is still asserted, on the one case no ref can show:
        a row written by hand rather than minted, where the lever is never
        consulted and only the merge can object.
        """
        self.git("checkout", "-q", "-b", "lane/a")
        self.assertEqual(self.add(n_where="`a.py`").exit, 0)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a: LD-36")
        self.git("checkout", "-q", "dev")
        self.git("checkout", "-q", "-b", "lane/b")
        # b mints while a's row sits on lane/a only: the id is taken, and the
        # lever reads the branch that holds it rather than the trunk alone
        proc = self.add(n_where="`b.py`")
        self.assertEqual(proc.stdout.split()[0], "LD-37",
                         "a's row on an unmerged branch is taken, not free")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "b: LD-37")
        self.git("checkout", "-q", "dev")
        self.git("merge", "-q", "--no-edit", "lane/a")
        merge = self.git("merge", "--no-edit", "lane/b", expect=None)
        self.assertEqual(merge.exit, 0, merge.stdout + merge.stderr)
        self.assertEqual(sorted(p.name for p in (self.repo / ROWS).iterdir()), ["LD-36.md", "LD-37.md"])
        # the backstop, for the row the lever never saw: written by hand on two
        # branches, the same id, and the merge is the only thing left to object
        for branch, where in (("lane/c", "`c.py`"), ("lane/d", "`d.py`")):
            self.git("checkout", "-q", "dev")
            self.git("checkout", "-q", "-b", branch)
            self.world.plant_text(
                self.repo / ROWS / "LD-38.md",
                f"# LD-38\n\n| field | value |\n|---|---|\n| observed | 2026-09-16 |\n"
                f"| where | {where} |\n| defect | a defect |\n| repair | a repair |\n"
                f"| tests | none yet |\n| by | Hand <hand@example.test> |\n",
            )
            self.git("add", "-A")
            self.git("commit", "-q", "-m", f"{branch}: LD-38 by hand")
        merge = self.git("merge", "--no-edit", "lane/c", expect=None)
        self.assertEqual(merge.exit, 1, "a hand-written id twice is an add/add conflict, loudly")
        conflicted = self.git("diff", "--name-only", "--diff-filter=U").stdout.split()
        self.assertEqual(conflicted, [str(ROWS / "LD-38.md")], "only the row file, never the page")
        self.git("merge", "--abort")

    def test_outside_a_repository_refuses(self):
        outside = self.world.path("nowhere")
        outside.mkdir()
        self.assert_wire(self.run_lever("next", identity=False, cwd=outside), "repository")


if __name__ == "__main__":
    unittest.main()
