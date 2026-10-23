"""The kit is domain free: no lane, ladder, finding, dispatch or
telemetry concept lives in it (docs/tests.md §The kit). The proof parses
every module of the kit, never its tests: an absent kit is not domain
free, it is absent."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from ops.testkit import World  # noqa: F401 - the import rule (docs/tests.md rule 1)

KIT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("lane", "ladder", "finding", "dispatch", "telemetry")
IMPORTS_FORBIDDEN = ("ops.conductor", "ops.devlane")


def modules() -> list[Path]:
    return sorted(p for p in KIT.glob("*.py"))


class DomainFree(unittest.TestCase):
    def test_the_kit_is_present(self):
        self.assertTrue(modules(), f"no modules under {KIT}: an absent kit is not domain free")

    def test_no_kit_module_imports_the_domain(self):
        for path in modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    with self.subTest(module=path.name, imported=name):
                        self.assertFalse(name.startswith(IMPORTS_FORBIDDEN), f"{path.name} imports {name}")

    def test_no_identifier_or_literal_carries_a_domain_word(self):
        for path in modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                text = None
                if isinstance(node, ast.Name):
                    text = node.id
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    text = node.name
                elif isinstance(node, ast.Attribute):
                    text = node.attr
                elif isinstance(node, ast.arg):
                    text = node.arg
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = node.value
                if text is None:
                    continue
                lower = text.lower()
                for word in FORBIDDEN:
                    with self.subTest(module=path.name, line=getattr(node, "lineno", 0), word=word):
                        self.assertNotIn(word, lower, f"{path.name}:{getattr(node, 'lineno', 0)} carries {word!r}: {text[:80]!r}")
