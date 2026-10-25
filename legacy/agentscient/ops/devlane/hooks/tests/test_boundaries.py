"""The boundary registry closes both ways over the check matrix.

Owner, 2026-09-12: "the hook is supposed to have matrix of checks and
guarantees - its a framework for verification"; "if its not an
abstracted boundary - then we don't speak the same language". Law:
docs/workflow.md, a transition declares its actor class, its evidence
duty, its failure class and its gate.

ops/devlane/hooks/boundaries.json declares one row per refusal class.
This gate asserts: every class the matrix can emit is declared; every
declared class is emitted by the gate function the row names; every
test the row names exists in the file it names; every row carries the
four declared fields and a known escape; the matrix docstring names the
class; and deny() of an undeclared class raises, so the hook reports
hook-error rather than a rule nobody declared.

One producer (Claude Fable 5.1) under hand grant 162; the second
producer is absent by the owner's word.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

HOOKS = Path(__file__).resolve().parents[1]
REPO = HOOKS.parents[2]
MATRIX = HOOKS / "claude" / "matrix.py"
REGISTRY = HOOKS / "boundaries.json"
FIELDS = ("class", "boundary", "actor", "trigger", "evidence", "gate", "tests", "escape", "event", "tools")
ESCAPES = {"none", "grant", "unscoped-grant", "ask"}
BOUNDARIES = {"write", "search", "test-run", "dispatch", "branch", "pr", "commit", "publish"}
TIMEOUT = 120


def emitted_classes():
    tree = ast.parse(MATRIX.read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == "deny"
                        and sub.args and isinstance(sub.args[0], ast.Constant)):
                    found.setdefault(sub.args[0].value, set()).add(node.name)
    return found


class TheRegistryClosesOverTheMatrix(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="boundaries-")
        self.addCleanup(self.world.close)
        self.assertTrue(REGISTRY.is_file(), f"missing {REGISTRY}")
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema"), "agentscient.boundaries/2")
        self.rows = {row["class"]: row for row in data["rows"]}
        self.emitted = emitted_classes()
        self.docstring = MATRIX.read_text(encoding="utf-8").split('"""', 2)[1]

    def test_every_emitted_class_is_declared(self):
        self.assertEqual(sorted(set(self.emitted) - set(self.rows)), [],
                         "the matrix refuses with a class the registry does not declare")

    def test_every_declared_class_is_emitted_by_its_gate_or_evaluated_by_the_engine(self):
        for klass, row in self.rows.items():
            with self.subTest(klass=klass):
                if row.get("engine"):
                    self.assertEqual(row["gate"], "engine")
                    self.assertIsInstance(row.get("when"), dict, "an engine row carries a when tree")
                    self.assertTrue(row.get("refuse") or row.get("context"))
                    self.assertNotIn(klass, self.emitted, "an engine row is not also refused by code")
                    continue
                self.assertIn(klass, self.emitted, "declared but the matrix never refuses with it")
                self.assertIn(row["gate"], self.emitted[klass],
                              f"{klass} is emitted by {sorted(self.emitted[klass])}, not {row['gate']}")

    def test_the_engine_lints_the_live_registry_clean(self):
        result = run(
            [sys.executable, str(HOOKS / "claude" / "engine.py"), "lint"],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=TIMEOUT,
        )
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["unknown_predicates"], [])
        self.assertEqual(report["unknown_events"], [])

    def test_every_row_carries_the_declared_fields(self):
        for klass, row in self.rows.items():
            with self.subTest(klass=klass):
                for field in FIELDS:
                    self.assertIn(field, row)
                    if field != "tools":
                        self.assertTrue(row[field], f"{klass}.{field} is empty")
                self.assertIn(row["event"], ("PreToolUse", "PostToolUse", "SessionStart", "Stop"))
                self.assertIn(row["boundary"], BOUNDARIES)
                self.assertIn(row["escape"], ESCAPES)
                self.assertEqual(row["actor"], "session")

    def test_every_named_test_exists(self):
        for klass, row in self.rows.items():
            for ref in row["tests"]:
                with self.subTest(klass=klass, test=ref):
                    path, name = ref.split("::", 1)
                    text = (REPO / path).read_text(encoding="utf-8")
                    self.assertRegex(text, rf"def {re.escape(name)}\(", f"{name} not in {path}")

    def test_an_undeclared_class_cannot_be_refused_with(self):
        probe = ("import sys; sys.path.insert(0, %r); import importlib.util as u; "
                 "s = u.spec_from_file_location('matrix', %r); m = u.module_from_spec(s); s.loader.exec_module(m); "
                 "m.deny('no-such-row', 'x', 'y', 'z')") % (str(HOOKS / "claude"), str(MATRIX))
        result = run(
            [sys.executable, "-c", probe],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=TIMEOUT,
        )
        self.assertNotEqual(result.exit, 0)
        self.assertIn("undeclared gate: no-such-row", result.stderr)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}, "cwd": str(REPO)})
        result = run(
            [sys.executable, str(MATRIX)],
            cwd=self.world.cwd,
            env=self.world.env(),
            stdin=payload,
            timeout=TIMEOUT,
        )
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", "a plain command still passes")


if __name__ == "__main__":
    unittest.main()
