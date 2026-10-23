"""LD-24: work_order_scope resolves from the supplied inputs first.

The resolver swept every ``scope-*.md`` on the ladder-inputs shelf
beside the inputs it was given, so once two work orders sat there
(scope-gate-20260910.md beside scope-item31-20260911.md, 2026-09-11) it
found two references and two candidates and returned None: the cited
read called every disposition uncited and the correction gate printed a
placeholder scope path. The inputs of a dispatch name its work order;
the shelf sweep is the fallback for inputs that name none. findings.py
is the subject and is not read here; it is loaded as a module. One
producer wrote the change and this test (grant 145).
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "findings.py"

WORK_ORDER = (
    "# Work order: {name}\n\n## Contract\n\n"
    "1. **One.** The first item.\n2. **Two.** The second.\n"
)
SENTENCE = (
    "Work order: {name}. The requirements are the two numbered Contract "
    "items of .dev/ladder-inputs/{name}.md; read it WHOLE.\n"
)
DISCIPLINE = "# Scope discipline\n\n1. The requirements are the numbered items.\n"
PLAN = "# Plan: {name}\n\nInputs read whole: in/{name}.md, in/scope-discipline.md.\n"
SLICE_FILES = ("plan-scope-beta.md", "scope-beta.md", "scope-discipline.md")


def _load():
    spec = importlib.util.spec_from_file_location("findings_ld24", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["findings_ld24"] = module
    spec.loader.exec_module(module)
    return module


class ShelfAmbiguity(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="ld-twentyfour-")
        self.addCleanup(self.world.close)
        self.root = self.world.cwd
        self.shelf = self.root / ".dev" / "ladder-inputs"
        self.shelf.mkdir(parents=True)
        self.jobs = self.world.path("jobs")
        self.jobs.mkdir(exist_ok=True)
        self.world.plant_text(self.shelf / "scope-discipline.md", DISCIPLINE)
        self.resolver = _load().work_order_scope

    def plant(self, name: str) -> Path:
        order = self.shelf / f"{name}.md"
        self.world.plant_text(order, WORK_ORDER.format(name=name))
        self.world.plant_text(
            self.shelf / f"{name}.scope.md",
            SENTENCE.format(name=name),
        )
        self.world.plant_text(
            self.shelf / f"plan-{name}.md",
            PLAN.format(name=name),
        )
        self.assertRegex(order.read_text(encoding="utf-8"), r"(?m)^1\. ")
        return order

    def test_two_work_orders_and_inputs_naming_one_resolve_that_one(self):
        self.plant("scope-alpha")
        self.plant("scope-beta")
        inputs = [str(self.shelf / name) for name in SLICE_FILES]
        self.assertEqual(
            self.resolver(inputs, self.root),
            ".dev/ladder-inputs/scope-beta.md",
        )

    def test_job_directory_inputs_resolve_by_shelf_name(self):
        """A closed record names inputs under the job directory, outside the root."""
        self.plant("scope-alpha")
        self.plant("scope-beta")
        job_in = self.jobs / "in"
        job_in.mkdir()
        for name in SLICE_FILES:
            self.world.plant_text(
                job_in / name,
                (self.shelf / name).read_text(encoding="utf-8"),
            )
        inputs = [str(job_in / name) for name in SLICE_FILES]
        self.assertEqual(
            self.resolver(inputs, self.root),
            ".dev/ladder-inputs/scope-beta.md",
        )

    def test_one_work_order_on_the_shelf_still_resolves_with_no_inputs(self):
        self.plant("scope-alpha")
        self.assertEqual(
            self.resolver([], self.root),
            ".dev/ladder-inputs/scope-alpha.md",
        )

    def test_two_work_orders_and_no_inputs_declare_the_ambiguity(self):
        self.plant("scope-alpha")
        self.plant("scope-beta")
        self.assertIsNone(self.resolver([], self.root))

    def test_inputs_naming_only_the_discipline_fall_back_to_a_lone_shelf(self):
        self.plant("scope-alpha")
        self.assertEqual(
            self.resolver([str(self.shelf / "scope-discipline.md")], self.root),
            ".dev/ladder-inputs/scope-alpha.md",
        )


if __name__ == "__main__":
    unittest.main()
