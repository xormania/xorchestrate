"""Root-tree pins for fixture-apps slice 1, written from the contract.

The batch must exclude `ops/devlane/fixtures/apps` from root ruff (with
the planted-fault reason), keep linting the lane tests under
`ops/devlane/fixtures/tests/`, and name the apps directory and the
`fixture.sh` lever in `ops/README.md`. Those files exist today without
the rows; the missing exclusion and README lines are the intended red.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[4]
RUFF = REPO / "ruff.toml"
README = REPO / "ops" / "README.md"
APPS_EXCLUDE = "ops/devlane/fixtures/apps"
TESTS_PATH = "ops/devlane/fixtures/tests"


class _FixtureRoot(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="fixture-root-")
        self.addCleanup(self.world.close)
        self.assertTrue(RUFF.is_file(), f"{RUFF} is missing")
        self.assertTrue(README.is_file(), f"{README} is missing")
        self.ruff = self.world.stage(RUFF, "ruff.toml")
        self.readme = self.world.stage(README, "README.md")


class RuffExclusion(_FixtureRoot):
    def test_ruff_toml_excludes_fixture_apps_with_reason(self):
        self.assertTrue(self.ruff.is_file(), f"{self.ruff} is missing")
        text = self.ruff.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        exclude = [str(item).rstrip("/") for item in data.get("extend-exclude", [])]
        self.assertIn(
            APPS_EXCLUDE,
            exclude,
            f"{self.ruff} extend-exclude must name {APPS_EXCLUDE}",
        )
        lines = text.splitlines()
        hits = [index for index, line in enumerate(lines) if APPS_EXCLUDE in line]
        self.assertTrue(hits, f"{APPS_EXCLUDE} not found as text in {self.ruff}")
        idx = hits[0]
        window = "\n".join(lines[max(0, idx - 8) : idx + 3]).lower()
        self.assertTrue(
            "planted" in window and ("k1" in window or "k4" in window or "s family" in window or "family" in window),
            f"the exclusion must state why planted faults survive root ruff: {window!r}",
        )
        self.assertTrue(
            "prove" in window or "check" in window,
            f"the exclusion must say apps are linted through prove/check: {window!r}",
        )

    def test_ruff_toml_still_lints_lane_fixture_tests(self):
        self.assertTrue(self.ruff.is_file(), f"{self.ruff} is missing")
        data = tomllib.loads(self.ruff.read_text(encoding="utf-8"))
        exclude = [str(item).rstrip("/") for item in data.get("extend-exclude", [])]
        self.assertNotIn(
            TESTS_PATH,
            exclude,
            "lane tests under ops/devlane/fixtures/tests/ stay linted",
        )
        self.assertFalse(
            any(item.startswith(TESTS_PATH) for item in exclude),
            exclude,
        )


class ReadmeLayout(_FixtureRoot):
    def test_ops_readme_names_the_apps_directory(self):
        self.assertTrue(self.readme.is_file(), f"{self.readme} is missing")
        text = self.readme.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"\|\s*`ops/devlane/fixtures/apps/`\s*\|",
            "ops/README.md layout table must have a row for ops/devlane/fixtures/apps/",
        )

    def test_ops_readme_names_the_fixture_lever(self):
        self.assertTrue(self.readme.is_file(), f"{self.readme} is missing")
        text = self.readme.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"\|\s*`ops/bin/fixture\.sh`\s*\|",
            "ops/README.md levers table must have a row for ops/bin/fixture.sh",
        )


if __name__ == "__main__":
    unittest.main()
