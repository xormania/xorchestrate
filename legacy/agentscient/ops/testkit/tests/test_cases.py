"""cases: the refusal-wire assertion, tables by id, mutants with an
anchor guard, calibrate and sidebyside (docs/tests.md rules 2 and 3)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import support

from ops.testkit import Mutant, RefusalCase, World, refusal_wire, run, run_table
from ops.testkit.cases import AnchorMissing, apply, calibrate, main, sidebyside

REFUSER_REL = "ops/testkit/tests/fixtures/refuser.py"

# The refuser's mutants: each breaks one rule of the wire.
WIRE_MUTANTS = [
    Mutant("M-wire-stdout", REFUSER_REL, "sys.stderr.write(\n", "sys.stdout.write(\n", "the refusal on stdout"),
    Mutant("M-wire-two-lines", REFUSER_REL, '"; needed the verb and its one argument\\n"', '"; needed the verb and its one argument\\nsecond line\\n"', "a second stderr line"),
    Mutant("M-wire-order", REFUSER_REL, '"arguments: expected serve NAME; found "', '"arguments: found serve NAME; expected "', "found before expected"),
]
ROWS = [
    RefusalCase("bare", (), "arguments", named=("serve NAME",)),
    RefusalCase("wrong-verb", ("bogus",), "arguments", named=("bogus",), origin="fixture"),
    RefusalCase("too-many", ("serve", "a", "b"), "arguments"),
]


def write_mutants(path: Path, mutants) -> Path:
    body = "from ops.testkit import Mutant\nMUTANTS = [\n" + "".join(
        f"    Mutant({m.id!r}, {m.path!r}, {m.anchor!r}, {m.replacement!r}, {m.note!r}),\n" for m in mutants
    ) + "]\n"
    path.write_text(body, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == body
    return path


class TheWireAssertion(unittest.TestCase):
    def invoke(self, world):
        def go(row):
            return run([sys.executable, str(support.REFUSER), *row.argv], cwd=world.cwd, env=world.env(), stdin=row.stdin)
        return go

    def test_refusal_wire_catches_the_three_wire_mutants_and_passes_the_clean_refuser(self):
        with World() as world:
            run_table(self, ROWS, self.invoke(world))
            for mutant in WIRE_MUTANTS:
                with self.subTest(mutant=mutant.id):
                    copy = world.root / "copies" / mutant.id / REFUSER_REL
                    copy.parent.mkdir(parents=True)
                    copy.write_bytes(support.REFUSER.read_bytes())
                    apply(mutant, world.root / "copies" / mutant.id)
                    self.assertNotEqual(copy.read_bytes(), support.REFUSER.read_bytes())
                    result = run([sys.executable, str(copy), "bogus"], cwd=world.cwd, env=world.env())
                    with self.assertRaises(AssertionError):
                        refusal_wire(ROWS[1], result)

    def test_run_table_refuses_duplicate_ids_and_an_empty_table(self):
        with World() as world:
            with self.assertRaises(AssertionError):
                run_table(self, [ROWS[0], ROWS[0]], self.invoke(world))
            with self.assertRaises(AssertionError):
                run_table(self, [], self.invoke(world))


class Mutants(unittest.TestCase):
    def test_mutant_with_absent_anchor_refuses_anchor(self):
        with World() as world:
            copy = world.root / REFUSER_REL
            copy.parent.mkdir(parents=True)
            copy.write_bytes(support.REFUSER.read_bytes())
            with self.assertRaises(AnchorMissing):
                apply(Mutant("M-absent", REFUSER_REL, "no such anchor text", "x"), world.root)
            with self.assertRaises(AnchorMissing):
                apply(Mutant("M-twice", REFUSER_REL, "sys", "x"), world.root)
            self.assertEqual(copy.read_bytes(), support.REFUSER.read_bytes(), "a refused mutant changes nothing")
            mutants = write_mutants(world.root / "faults.py", [Mutant("M-absent", REFUSER_REL, "no such anchor text", "x")])
            proc = run(
                [sys.executable, str(support.KIT / "cases.py"), "calibrate", "--subject", REFUSER_REL,
                 "--mutants", str(mutants), "--command", "true", "--repo", str(support.REPO)],
                cwd=support.REPO, env=world.env(), timeout=600,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertRegex(proc.stderr.strip(), r"^anchor: expected .+; found .+; needed .+$")
            self.assertEqual(len(proc.stderr.strip().splitlines()), 1)

    def test_main_refuses_arguments_on_the_wire(self):
        for argv in ([], ["bogus"], ["calibrate", "--subject"], ["sidebyside", "--subject", "x"]):
            with self.subTest(argv=argv), World() as world:
                proc = run([sys.executable, str(support.KIT / "cases.py"), *argv],
                           cwd=support.REPO, env=world.env(), timeout=60)
                self.assertEqual(proc.returncode, 2)
                self.assertRegex(proc.stderr.strip(), r"^arguments: expected .+; found .+; needed .+$")
                self.assertEqual(proc.stdout, "")


@unittest.skipUnless((support.REPO / ".git").exists(), "the verbs archive HEAD; no repository here")
class Verbs(unittest.TestCase):
    """The verbs archive HEAD, so the refuser and its rows must be committed
    for these to read anything; a fresh checkout of this tree qualifies."""

    COMMAND = f"{sys.executable} -m unittest discover -s ops/testkit/tests -p test_cases.py -k TheWireAssertion"

    def test_calibrate_reports_caught_and_quiet_per_mutant_and_exits_one_on_quiet(self):
        with World() as world:
            quiet = Mutant("M-comment", REFUSER_REL, "Its mutants are declared", "Its mutants are listed", "a docstring change no test sees")
            mutants = write_mutants(world.root / "faults.py", [*WIRE_MUTANTS, quiet])
            report = calibrate(REFUSER_REL, mutants, self.COMMAND, repo=support.REPO)
            self.assertEqual(report["clean"], "green", report)
            self.assertEqual({k: v for k, v in report["mutants"].items() if k != "M-comment"},
                             {m.id: "caught" for m in WIRE_MUTANTS})
            self.assertEqual(report["mutants"]["M-comment"], "quiet")
            self.assertEqual(report["quiet"], ["M-comment"])
            self.assertFalse(report["ok"])
            code = main(["calibrate", "--subject", REFUSER_REL, "--mutants", str(mutants), "--command", self.COMMAND,
                         "--repo", str(support.REPO)])
            self.assertEqual(code, 1)

    def test_sidebyside_writes_the_record_and_never_touches_the_tree(self):
        with World() as world:
            watch = world.watch(support.REPO / "ops" / "testkit", support.FIXTURES)
            before = support.REFUSER.read_bytes()
            mutants = write_mutants(world.root / "faults.py", WIRE_MUTANTS)
            out = world.root / "record.json"
            # Origin: LD-72, PR #241. Host true is not on a bare world's PATH.
            new = str(world.tools.plant("true"))
            record = sidebyside(REFUSER_REL, mutants, self.COMMAND, new, repo=support.REPO)
            self.assertEqual(record["clean"], {"old": "green", "new": "green"})
            self.assertEqual(record["quiet_in_new"], sorted(m.id for m in WIRE_MUTANTS))
            self.assertFalse(record["ok"])
            code = main(["sidebyside", "--subject", REFUSER_REL, "--mutants", str(mutants), "--old", self.COMMAND,
                         "--new", self.COMMAND, "--out", str(out), "--repo", str(support.REPO)])
            self.assertEqual(code, 0)
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["quiet_in_new"], [])
            self.assertTrue(written["ok"])
            self.assertEqual(set(written["mutants"]), {m.id for m in WIRE_MUTANTS})
            watch.assert_unchanged(self)
            self.assertEqual(support.REFUSER.read_bytes(), before)
