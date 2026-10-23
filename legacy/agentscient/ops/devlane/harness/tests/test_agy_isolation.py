"""Bounded agy isolation tests, from the public isolation contract.

Pinned from isolation.py's module contract, dispatch/CONTRACT.md's
isolation seam, the 2026-09-08 host observation, and the isolation-test
rulings: HOME isolation, `.gemini/antigravity-cli` credential base,
`brain` session root, only the `antigravity-oauth-token` leaf, no
settings/history copy, no real-HOME fallback. Auth-file symlink is
allowed; necessary parent directories must be real directories.
Discovery isolation remains unresolved in existing measured metadata.
HOME-only sufficiency and empty dispatch_flags are not pinned.
Synthetic fixtures only. These tests were authored before implementation.
"""

from __future__ import annotations

import os
from pathlib import Path
import unittest

from ops.testkit import World, plant_text

import support  # noqa: F401  (puts the harness dir on sys.path)

import isolation


AUTH_REL = ".gemini/antigravity-cli/antigravity-oauth-token"
AUTH_PARENTS = (".gemini", ".gemini/antigravity-cli")
SETTINGS_REL = ".gemini/antigravity-cli/settings.json"
HISTORY_REL = ".gemini/antigravity-cli/history"
SKILLS_REL = ".gemini/antigravity-cli/skills"
SESSION_REL = ".gemini/antigravity-cli/brain"
SYNTHETIC_TOKEN = "synthetic-agy-oauth-token\n"
SETTINGS_CANARY = "settings-canary\n"
HISTORY_CANARY = "history-canary\n"
EXPECTED_TREE = [*AUTH_PARENTS, AUTH_REL]


def _tree_paths(root: Path) -> list[str]:
    """Relative files and directories under root, not following links."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        relative_dir = current.relative_to(root).as_posix()
        if relative_dir != ".":
            paths.append(relative_dir)
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                paths.append(child.relative_to(root).as_posix())
        for name in filenames:
            paths.append((current / name).relative_to(root).as_posix())
    paths.sort()
    return paths


def _plant_operator_home(world: World, *, token: bool) -> Path:
    home = world.root / "operator-home"
    plant_text(home / SETTINGS_REL, SETTINGS_CANARY)
    plant_text(home / HISTORY_REL, HISTORY_CANARY)
    skills = home / SKILLS_REL
    skills.mkdir()
    if token:
        plant_text(home / AUTH_REL, SYNTHETIC_TOKEN)
    if not skills.is_dir() or skills.is_symlink():
        raise AssertionError("skills directory plant did not land")
    return home


class AgyIsolationTests(unittest.TestCase):
    def _assert_measured_agy_home(self, home: Path, operator_auth: Path) -> None:
        self.assertEqual(EXPECTED_TREE, _tree_paths(home))
        for relative in AUTH_PARENTS:
            parent = home / relative
            self.assertTrue(parent.is_dir(), f"{relative} must exist as a directory")
            self.assertFalse(parent.is_symlink(), f"{relative} must not be a symlink")
        for dirpath, dirnames, _filenames in os.walk(home, followlinks=False):
            current = Path(dirpath)
            self.assertFalse(current.is_symlink())
            for name in dirnames:
                self.assertFalse(
                    (current / name).is_symlink(),
                    f"directory symlink not allowed: {name}",
                )
        linked = home / AUTH_REL
        self.assertTrue(linked.is_symlink())
        self.assertEqual(operator_auth.resolve(), linked.resolve())
        self.assertEqual(SYNTHETIC_TOKEN, linked.read_text(encoding="utf-8"))
        self.assertFalse((home / SETTINGS_REL).exists())
        self.assertFalse((home / HISTORY_REL).exists())
        self.assertFalse((home / SKILLS_REL).exists())

    def test_agy_registry_declares_measured_home_isolation(self):
        self.assertIn("agy", isolation.HARNESSES)
        agy = isolation.HARNESSES["agy"]
        self.assertEqual("home", agy.get("mechanism"))
        self.assertEqual("HOME", agy.get("home_env"))
        self.assertIn("flags", agy)
        self.assertIsInstance(agy.get("flags"), list)
        auth_files = agy.get("auth_files")
        self.assertEqual(1, len(auth_files))
        self.assertEqual([AUTH_REL], auth_files)
        self.assertEqual(
            {"under": "minimal", "path": SESSION_REL},
            agy.get("sessions"),
        )
        self.assertIn("probe_phrase", agy)
        self.assertIsNone(agy.get("probe_phrase"))
        measured = agy.get("measured")
        self.assertIsInstance(measured, dict)
        self.assertEqual("2026-09-08", measured.get("on"))
        self.assertEqual("1.1.27", measured.get("version"))
        self.assertNotIn("probe_default", measured)
        self.assertNotIn("probe_isolated", measured)
        note = measured.get("note")
        self.assertIsInstance(note, str)
        self.assertTrue(note.strip())
        unresolved = measured.get("unresolved")
        self.assertIsInstance(unresolved, str)
        self.assertTrue(unresolved.strip())

    def test_build_home_links_only_the_oauth_token_and_excludes_canaries(self):
        with World(prefix="agy-iso-") as world:
            operator = _plant_operator_home(world, token=True)
            root = world.root / "scratch"
            root.mkdir()
            self.assertEqual(SYNTHETIC_TOKEN, (operator / AUTH_REL).read_text(encoding="utf-8"))
            self.assertEqual(SETTINGS_CANARY, (operator / SETTINGS_REL).read_text(encoding="utf-8"))
            self.assertEqual(HISTORY_CANARY, (operator / HISTORY_REL).read_text(encoding="utf-8"))
            self.assertTrue((operator / SKILLS_REL).is_dir())
            self.assertFalse((operator / SKILLS_REL).is_symlink())

            try:
                home = Path(
                    isolation.build_home("agy", root, env={"HOME": str(operator)})
                )
            except isolation.NotIsolated as exc:
                self.fail(f"expected an isolated agy home, refused: {exc}")

            self._assert_measured_agy_home(home, operator / AUTH_REL)
            self.assertEqual(SETTINGS_CANARY, (operator / SETTINGS_REL).read_text(encoding="utf-8"))
            self.assertEqual(HISTORY_CANARY, (operator / HISTORY_REL).read_text(encoding="utf-8"))
            self.assertTrue((operator / SKILLS_REL).is_dir())

    def test_isolated_homes_for_two_jobs_are_fresh_and_independent(self):
        with World(prefix="agy-iso-") as world:
            operator = _plant_operator_home(world, token=True)
            env = {"HOME": str(operator)}
            root_a = world.root / "job-a"
            root_b = world.root / "job-b"
            root_a.mkdir()
            root_b.mkdir()

            try:
                env_a, flags_a = isolation.isolated("agy", root_a, env=env)
                env_b, flags_b = isolation.isolated("agy", root_b, env=env)
            except isolation.NotIsolated as exc:
                self.fail(f"expected two isolated agy homes, refused: {exc}")

            self.assertIsInstance(flags_a, list)
            self.assertIsInstance(flags_b, list)
            self.assertIn("HOME", env_a)
            self.assertIn("HOME", env_b)
            home_a = Path(env_a["HOME"])
            home_b = Path(env_b["HOME"])
            self.assertNotEqual(home_a.resolve(), home_b.resolve())
            self.assertTrue(home_a == root_a or root_a in home_a.parents)
            self.assertTrue(home_b == root_b or root_b in home_b.parents)
            self._assert_measured_agy_home(home_a, operator / AUTH_REL)
            self._assert_measured_agy_home(home_b, operator / AUTH_REL)
            marker = home_a / "job-marker"
            plant_text(marker, "job-a-only\n")
            self.assertEqual("job-a-only\n", marker.read_text(encoding="utf-8"))
            self.assertFalse((home_b / "job-marker").exists())
            self._assert_measured_agy_home(home_b, operator / AUTH_REL)

    def test_build_home_refuses_missing_auth_and_leaves_precreated_root_empty(self):
        with World(prefix="agy-iso-") as world:
            operator = _plant_operator_home(world, token=False)
            root = world.root / "scratch"
            root.mkdir()
            before = list(root.iterdir())
            self.assertEqual(0, len(before))
            self.assertFalse((operator / AUTH_REL).exists())
            self.assertEqual(
                SETTINGS_CANARY,
                (operator / SETTINGS_REL).read_text(encoding="utf-8"),
            )

            with self.assertRaises(isolation.NotIsolated) as raised:
                isolation.build_home("agy", root, env={"HOME": str(operator)})

            self.assertIn("antigravity-oauth-token", str(raised.exception))
            self.assertEqual(before, list(root.iterdir()))
            self.assertEqual(0, len(list(root.iterdir())))
            self.assertEqual(
                SETTINGS_CANARY,
                (operator / SETTINGS_REL).read_text(encoding="utf-8"),
            )

    def test_explicit_env_does_not_fall_back_to_the_real_home(self):
        with World(prefix="agy-iso-") as world:
            root = world.root / "scratch"
            root.mkdir()
            with self.assertRaises(isolation.NotIsolated) as raised:
                isolation.build_home("agy", root, env={"PATH": "/usr/bin"})
            reason = str(raised.exception)
            self.assertIn("HOME", reason)
            self.assertNotIn("antigravity-oauth-token", reason)
            self.assertEqual(0, len(list(root.iterdir())))

            with self.assertRaises(isolation.NotIsolated):
                isolation.build_home("agy", root, env={"HOME": ""})
            with self.assertRaises(isolation.NotIsolated):
                isolation.dispatch_env("agy", home="", env={})
            with self.assertRaises(isolation.NotIsolated):
                isolation.dispatch_env("agy", home=None, env={})

    def test_dispatch_env_sets_home(self):
        home = "/tmp/recognisable-agy-minimal-home"
        try:
            actual = isolation.dispatch_env(
                "agy", home=home, env={"KEEP": "caller"}
            )
        except isolation.NotIsolated as exc:
            self.fail(f"expected agy dispatch_env overrides, refused: {exc}")
        self.assertIn("HOME", actual)
        self.assertEqual(home, actual["HOME"])

    def test_isolated_returns_home_env_under_root(self):
        with World(prefix="agy-iso-") as world:
            operator = _plant_operator_home(world, token=True)
            root = world.root / "scratch"
            root.mkdir()
            try:
                env, flags = isolation.isolated(
                    "agy", root, env={"HOME": str(operator)}
                )
            except isolation.NotIsolated as exc:
                self.fail(f"expected isolated agy configuration, refused: {exc}")
            self.assertIsInstance(flags, list)
            self.assertIn("HOME", env)
            home = Path(env["HOME"])
            self.assertTrue(home == root or root in home.parents)
            self._assert_measured_agy_home(home, operator / AUTH_REL)


if __name__ == "__main__":
    unittest.main()
