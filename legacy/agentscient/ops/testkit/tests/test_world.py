"""World: hermetic by default, ambient by name, plants proven, children
reaped, watched paths compared (docs/tests.md §The kit, rules 4 and 6)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from ops.testkit import PlantFailed, World, compare, run


class Environment(unittest.TestCase):
    def test_git_fixture_objects_stay_stationary_while_copied(self):
        with World() as world:
            world.git("init", "-q")
            # A repository setting cannot start background packing while a
            # calibration copies its fixture. Command-scoped defaults win.
            world.git("config", "gc.auto", "1")
            world.git("config", "maintenance.auto", "true")
            self.assertEqual(world.git("config", "--get", "gc.auto").stdout.strip(), "0")
            self.assertEqual(world.git("config", "--get", "maintenance.auto").stdout.strip(), "false")

    def test_env_carries_no_operator_home(self):
        # Poison ambient constants explicitly: equal fixed defaults in nested
        # CI worlds are not evidence that operator values were inherited.
        with patch.dict(os.environ, {"LANG": "host-lang", "LC_ALL": "host-locale",
                                     "PYTHONDONTWRITEBYTECODE": "host-bytecode",
                                     "OPERATOR_CONFIG": "must-not-be-inherited"}), World() as world:
            env = world.env()
            self.assertEqual(env["HOME"], str(world.home))
            self.assertNotEqual(env["HOME"], os.environ.get("HOME"))
            for name in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR"):
                self.assertTrue(env[name].startswith(str(world.root)), name)
            # A leak: the planted fault is an env() that copies os.environ.
            leaked = {k for k in os.environ if k in env and env[k] == os.environ[k]
                      and k != "PATH" and not k.startswith("GIT_")}
            self.assertEqual(leaked, set(), f"host values reached the world: {sorted(leaked)}")
        # Origin: LD-72, PR #241. The strict world's PATH is bin alone.
        # The default carries the host's after it until the sweep lands;
        # test_world_path.py pins both values of host_path.
        with World(host_path=False) as strict:
            self.assertEqual(strict.env()["PATH"], str(strict.bin))

    def test_home_xdg_tmpdir_cwd_are_under_the_root(self):
        with World() as world:
            for path in (world.home, world.xdg_state, world.xdg_config, world.xdg_cache,
                         world.tmp, world.cwd, world.bin):
                self.assertTrue(path.is_dir(), path)
                self.assertEqual(path.resolve().parents[-1], Path("/"))
                self.assertTrue(str(path).startswith(str(world.root)), path)

    def test_ambient_is_explicit_and_named(self):
        os.environ["WORLD_TEST_AMBIENT"] = "from-the-host"
        try:
            with World() as hermetic:
                self.assertNotIn("WORLD_TEST_AMBIENT", hermetic.env())
            with World(ambient=("WORLD_TEST_AMBIENT",)) as world:
                self.assertEqual(world.env()["WORLD_TEST_AMBIENT"], "from-the-host")
                self.assertEqual(world.ambient, ("WORLD_TEST_AMBIENT",))
            with self.assertRaises(PlantFailed):
                World(ambient=("HOME",))
        finally:
            del os.environ["WORLD_TEST_AMBIENT"]

    def test_enter_keeps_a_named_ambient_value_in_process_and_restores_the_host(self):
        # Ruled rank 4 (record 20260915T025600Z-adjudicate-codex-68d3d9): enter
        # cleared os.environ before reading the ambient names from it.
        os.environ["WORLD_TEST_AMBIENT"] = "named-value"
        before = dict(os.environ)
        try:
            world = World(ambient=("WORLD_TEST_AMBIENT",))
            self.assertEqual(world.env()["WORLD_TEST_AMBIENT"], "named-value")
            world.enter()
            try:
                self.assertEqual(os.environ.get("WORLD_TEST_AMBIENT"), "named-value",
                                 "an in-process subject sees the named ambient value")
                self.assertEqual(os.environ["HOME"], str(world.home))
            finally:
                world.close()
            self.assertEqual(dict(os.environ), before)
        finally:
            os.environ.pop("WORLD_TEST_AMBIENT", None)

    def test_enter_restores_os_environ(self):
        before = dict(os.environ)
        world = World()
        world.enter()
        try:
            self.assertEqual(os.environ["HOME"], str(world.home))
            self.assertNotEqual(os.environ.get("HOME"), before.get("HOME"))
        finally:
            world.close()
        self.assertEqual(dict(os.environ), before)


class Plants(unittest.TestCase):
    def test_plant_text_refuses_a_plant_that_did_not_land(self):
        with World() as world:
            planted = world.plant_text("a/b.txt", "hello\n", recognisable="hello")
            self.assertEqual(planted.read_text(encoding="utf-8"), "hello\n")
            with self.assertRaises(PlantFailed):
                world.plant_text("a/c.txt", "hello\n", recognisable="absent marker")

    def test_stage_copies_the_subject_and_stands_in_for_an_absent_one(self):
        with World() as world:
            staged = world.stage(support.REFUSER)
            self.assertEqual(staged.read_bytes(), support.REFUSER.read_bytes())
            self.assertNotEqual(staged.resolve(), support.REFUSER.resolve())
            absent = world.stage(world.root / "nowhere.py", "nowhere.py")
            result = run([sys.executable, str(absent), "anything"], cwd=world.cwd, env=world.env())
            self.assertEqual((result.exit, result.stdout, result.stderr), (0, "", ""))

    def test_require_names_an_absent_tool_as_invalid(self):
        with World() as world:
            self.assertTrue(world.require("python3"))
            with self.assertRaises(PlantFailed):
                world.require("no-such-tool-in-this-world")


class ToolNames(unittest.TestCase):
    """A world places its tools on its own bin and nowhere else.

    Origin: 20260916T122524Z-review-codex-05a9f5#3 at aea65bb9 — a name
    carrying `..` resolved against the caller's directory while the wrapper
    was placed against the world's bin, so an executable landed outside the
    world and survived its close.
    """

    ESCAPES = ("../escaped", "../../escaped", "a/b", "/tmp/escaped", "..", ".")

    def test_a_tool_name_that_leaves_the_world_is_invalid(self):
        for name in self.ESCAPES:
            with self.subTest(name=name, origin="05a9f5#3"):
                with World(host_path=False) as world:
                    with self.assertRaises(PlantFailed):
                        world.ambient_tool(name)
                    with self.assertRaises(PlantFailed):
                        world.tools.plant(name, stdout="x\n")

    def test_a_planted_tool_lands_on_the_worlds_bin(self):
        """The clean neighbour: an ordinary name still works, and lands
        inside the world."""
        with World(host_path=False) as world:
            placed = world.tools.plant("mytool", stdout="x\n")
            self.assertEqual(placed.parent.resolve(), world.bin.resolve())
            self.assertTrue(placed.resolve().is_relative_to(world.root.resolve()))

    def test_an_ambient_tool_lands_on_the_worlds_bin(self):
        with World(host_path=False) as world:
            placed = world.ambient_tool("git")
            self.assertEqual(placed.parent.resolve(), world.bin.resolve())


class Children(unittest.TestCase):
    def test_owned_child_is_reaped_at_close(self):
        world = World()
        child = world.own(subprocess.Popen(support.SLEEPER, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        self.assertIsNone(child.poll())
        world.close()
        self.assertIsNotNone(child.poll(), "the child outlived the world")
        self.assertFalse(world.root.exists())


class Watching(unittest.TestCase):
    def test_watch_reports_a_changed_declared_path_and_ignores_declared_exclusions(self):
        with World() as world:
            store = world.root / "store"
            store.mkdir()
            (store / "kept").write_text("x", encoding="utf-8")
            watch = world.watch(store, ignore=(r"\.tmp\.[a-z]{3}",), tolerate_new=(r"^job-\d+$",))
            (store / ".tmp.abc").write_text("x", encoding="utf-8")
            (store / "job-7").mkdir()
            watch.assert_unchanged(self)
            (store / "intruder").write_text("x", encoding="utf-8")
            with self.assertRaises(AssertionError):
                watch.assert_unchanged(self)
            (store / "intruder").unlink()
            (store / "kept").unlink()
            with self.assertRaises(AssertionError):
                watch.assert_unchanged(self)

    def test_watch_reports_a_rewritten_file_and_a_rewritten_child_of_a_watched_directory(self):
        # Ruled rank 1 (record 20260915T025600Z-adjudicate-codex-68d3d9): a
        # snapshot of names alone let a rewrite in place pass as unchanged.
        with World() as world:
            planted = world.plant_text("watched.txt", "before\n")
            file_watch = world.watch(planted)
            file_watch.assert_unchanged(self)
            planted.write_text("after\n", encoding="utf-8")
            with self.assertRaises(AssertionError):
                file_watch.assert_unchanged(self)
            store = world.root / "store"
            (store / "sub").mkdir(parents=True)
            inner = store / "sub" / "inner.txt"
            inner.write_text("before\n", encoding="utf-8")
            dir_watch = world.watch(store, ignore=(r"\.tmp\.[a-z]{3}",), tolerate_new=(r"^job-\d+$",))
            dir_watch.assert_unchanged(self)
            inner.write_text("after\n", encoding="utf-8")
            with self.assertRaises(AssertionError):
                dir_watch.assert_unchanged(self)
            inner.write_text("before\n", encoding="utf-8")
            dir_watch.assert_unchanged(self)
            # the declared exclusions reach the children too
            (store / "sub" / ".tmp.xyz").write_text("scratch", encoding="utf-8")
            (store / "job-9").mkdir()
            (store / "job-9" / "record.json").write_text("{}", encoding="utf-8")
            dir_watch.assert_unchanged(self)
            (store / "sub" / "added.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(AssertionError):
                dir_watch.assert_unchanged(self)

    def test_compare_drops_a_removed_original_entry_only_from_expected_never_from_actual(self):
        # The planted fault: a compare that ignores a removed original entry.
        before = {"s": ("dir", ("kept", "job-1"))}
        after = {"s": ("dir", ("job-1", "job-2"))}
        expected, actual = compare(before, after, tolerate_new=(r"^job-\d+$",))
        self.assertEqual(expected, {"s": ("dir", ("kept", "job-1"))})
        self.assertEqual(actual, {"s": ("dir", ("job-1",))})
        self.assertNotEqual(expected, actual)


class Reaching(unittest.TestCase):
    def test_import_from_another_cwd_fails_naming_ops_testkit(self):
        world = World(prefix="elsewhere-")
        self.addCleanup(world.close)
        proc = run([sys.executable, "-c", "import ops.testkit"],
                   cwd=world.cwd, env=world.env(), timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ops", proc.stderr)
        here = run([sys.executable, "-c", "import ops.testkit"],
                   cwd=support.REPO, env=world.env(), timeout=60)
        self.assertEqual(here.returncode, 0, here.stderr)
