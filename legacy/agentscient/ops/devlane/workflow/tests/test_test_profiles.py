"""Profile boundaries and inventory failures; reuse: testkit.world."""

import json
import unittest
from unittest.mock import patch

from ops.devlane.workflow.checks import ci, lane
from ops.testkit import World


class Profiles(unittest.TestCase):
    def test_fast_is_a_subset_of_local_and_rehearsals_are_explicit(self):
        data = lane.inventory(lane.ROOT)
        plans = {p: {c.name for c in lane.plan(lane.ROOT, profile=p)} for p in lane.PROFILES}
        self.assertLess(plans["ci"], plans["local"])
        self.assertLess(plans["local"], plans["rehearsal"])
        for path in data["rehearsal"]:
            directory, filename = path.rsplit("/", 1)
            self.assertFalse(any(n.startswith(directory + ":" + filename) for n in plans["local"]))
        self.assertTrue(any("test_coordination.py" in n for n in plans["rehearsal"]))
        self.assertTrue(any("test_record.py" in n for n in plans["ci"]))

    def test_unclassified_deleted_and_duplicate_modules_refuse(self):
        with World() as world, patch.object(lane, "FAMILIES", ("tests",)):
            world.plant_text("cwd/tests/test_one.py", "fixture")
            original = {"ci": ["tests/test_one.py"], "local": [], "rehearsal": []}
            for change in ("unknown", "absent", "duplicate"):
                data = json.loads(json.dumps(original))
                if change == "unknown":
                    data["ci"] = []
                elif change == "absent":
                    data["local"] = ["tests/test_missing.py"]
                else:
                    data["local"] = data["ci"]
                world.plant_text("cwd/" + lane.INVENTORY, json.dumps(data))
                with self.assertRaisesRegex(ValueError, "classified once"):
                    lane.inventory(world.cwd)

    def test_fast_toolchain_needs_neither_tmux_nor_namespaces(self):
        with World(host_path=False) as world:
            for name in ("git", "python3", "bash", "ruff", "rg", "cue", "jq"):
                world.tools.plant(name, stdout="fixture version\n")
            tools = ci.toolchain(world.cwd, world.env(), "ci")["tools"]
            self.assertNotIn("tmux", tools)
            self.assertNotIn("bwrap", tools)
            with self.assertRaisesRegex(ValueError, "bwrap"):
                ci.toolchain(world.cwd, world.env(), "local")
