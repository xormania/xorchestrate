"""A minimal home has to start empty, and the controls have to be generated.

`build_home` is the constructor of the thing this app exists to produce.
It took a root and called `mkdir(exist_ok=True)`, so a REUSED directory
kept whatever was in it and the function returned normally — a home
carrying the operator's `hooks.json`, `AGENTS.md` and `skills/` could be
handed straight to a dispatch, through the constructor of the module
written to strip exactly those (Codex, PR #40).
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

HARNESS = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, HARNESS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AMinimalHomeStartsEmpty(unittest.TestCase):
    def setUp(self):
        self.iso = load("isolation")
        self.world = World(prefix="fake-operator-home-")
        self.addCleanup(self.world.close)
        self.world.plant_text(self.world.home / ".codex" / "auth.json", "{}")
        self.env = self.world.env()

    def test_a_reused_root_is_refused(self):
        root = self.world.root / "reused"
        self.world.plant_text(root / "hooks.json", '{"SessionStart": "anything"}')
        with self.assertRaises(self.iso.NotIsolated) as caught:
            self.iso.build_home("codex", root, env=self.env)
        self.assertIn("not empty", str(caught.exception))
        # The reason has to name what it found, or the operator cannot
        # tell a stale scratch directory from a bug in the launcher.
        self.assertIn("hooks.json", str(caught.exception))

    def test_a_fresh_empty_root_holds_only_the_credentials(self):
        root = self.world.root / "fresh"
        root.mkdir()
        home = Path(self.iso.build_home("codex", root, env=self.env))
        self.assertEqual(sorted(p.name for p in home.iterdir()),
                         ["auth.json"])

    def test_a_root_that_does_not_exist_yet_is_fine(self):
        # The launcher names a path under a temp dir before creating it;
        # refusing that would make the guard unusable and it would be
        # removed, which protects nothing.
        parent = self.world.root / "parent"
        parent.mkdir()
        root = parent / "not-yet"
        home = Path(self.iso.build_home("codex", root, env=self.env))
        self.assertEqual(sorted(p.name for p in home.iterdir()),
                         ["auth.json"])

    def test_a_root_that_is_a_file_is_refused(self):
        path = self.world.plant_text("occupied", "not a directory")
        with self.assertRaises(self.iso.NotIsolated):
            self.iso.build_home("codex", path, env=self.env)


class AnExplicitEnvironmentIsNotQuietlyCompleted(unittest.TestCase):
    """Passing an env is how a caller says "this, and nothing of mine".

    `_real_home` filled a missing HOME from `Path.home()`, so a caller
    that controlled the environment and forgot HOME silently read the
    OPERATOR's home and linked their live credential into a "minimal"
    one. Found by hand while diagnosing an independent author's tests —
    one of theirs passed on this machine and would have failed on a
    clean one — and it survived a mutation run afterwards, because
    their tests set the harness's own home variable and never take this
    path. So the pin is written here by the person who found it, and
    that is worth saying rather than leaving the coverage looking
    accidental.
    """

    def setUp(self):
        self.iso = load("isolation")
        self.world = World(prefix="explicit-env-")
        self.world.enter()
        self.addCleanup(self.world.close)

    def test_an_explicit_env_naming_no_home_at_all_is_refused(self):
        with self.assertRaises(self.iso.NotIsolated) as caught:
            self.iso.build_home("codex", self.world.root / "fresh",
                                env={"PATH": "/usr/bin"})
        reason = str(caught.exception)
        self.assertIn("CODEX_HOME", reason)
        self.assertIn("HOME", reason)
        # The refusal must not be mistaken for the credential one: they
        # send a reader to entirely different places.
        self.assertNotIn("credential auth.json is absent", reason)

    def test_the_operator_home_is_never_the_answer_to_an_explicit_env(self):
        for harness in ("codex", "grok"):
            with self.subTest(harness=harness), \
                    self.assertRaises(self.iso.NotIsolated):
                self.iso._real_home(harness, {"PATH": "/usr/bin"}, given=True)

    def test_an_empty_home_is_absent_not_a_relative_path(self):
        # Requested by the reviewer who found it, and written to their
        # recipe: HOME="" with a planted ./.codex/auth.json under the
        # working directory. `Path("") / ".codex"` is the RELATIVE path
        # `.codex`, so the lookup reached ambient project state — the
        # one thing an explicit environment exists to exclude.
        work = self.world.cwd
        planted = work / ".codex" / "auth.json"
        self.world.plant_text(planted, "a credential that is not ours\n")
        self.assertTrue(planted.exists(), "the plant did not land")

        here = os.getcwd()
        os.chdir(work)
        try:
            with self.assertRaises(self.iso.NotIsolated):
                self.iso.build_home("codex",
                                    self.world.root / "fresh-empty",
                                    env={"HOME": ""})
            with self.assertRaises(self.iso.NotIsolated):
                self.iso._real_home("codex", {"HOME": ""}, given=True)
        finally:
            os.chdir(here)
        # The plant is still there: the refusal must be inert, or a
        # guard that also deletes is worse than the hole.
        self.assertTrue(planted.exists())

    def test_an_empty_home_is_absent_when_dispatching_the_environment(self):
        # The same shape one function along. `home=""` would emit
        # CODEX_HOME="", which the harness reads as UNSET and answers by
        # loading the operator's real home — the leak, spelled with an
        # empty string instead of a missing key.
        for empty in ("", None):
            with self.subTest(home=empty):
                with self.assertRaises(self.iso.NotIsolated):
                    self.iso.dispatch_env("codex", home=empty, env={})

    def test_a_credential_link_that_does_not_resolve_is_refused(self):
        # Surfaced while reproducing the empty-HOME finding: a relative
        # source made `symlink_to` point inside the minimal home, and
        # `build_home` returned a home containing a DANGLING auth.json.
        # An entry with the right name is not the credential.
        operator = self.world.root / "operator"
        self.world.plant_text(operator / ".codex" / "auth.json", "{}")
        root = self.world.root / "fresh-link"
        root.mkdir()
        home = Path(self.iso.build_home("codex", root,
                                        env={"HOME": str(operator)}))
        link = home / "auth.json"
        self.assertTrue(link.is_symlink())
        self.assertTrue(link.resolve().exists(),
                        "the credential link must resolve to a real file")
        self.assertEqual(link.read_text(encoding="utf-8"), "{}")

    def test_a_relative_home_variable_still_links_to_the_real_file(self):
        # The remaining way `src` can be relative, now that an empty
        # HOME is refused. `symlink_to` resolves a relative source
        # against the LINK's directory, not the caller's, so the home
        # ended up holding a DANGLING auth.json pointing inside itself —
        # complete-looking, and an authentication failure at dispatch.
        work = self.world.cwd
        self.world.plant_text(work / "elsewhere" / "auth.json", "{}")
        here = os.getcwd()
        os.chdir(work)
        try:
            root = self.world.root / "relative-home"
            home = Path(self.iso.build_home(
                "codex", root, env={"CODEX_HOME": "elsewhere"}))
            link = home / "auth.json"
            self.assertTrue(link.is_symlink())
            self.assertTrue(link.resolve().exists(),
                            f"{link} dangles: -> {link.resolve()}")
            self.assertEqual((work / "elsewhere" / "auth.json").resolve(),
                             link.resolve())
        finally:
            os.chdir(here)

    def test_no_env_at_all_still_falls_back_to_the_real_home(self):
        # The twin. Refusing here would break every ordinary call, and a
        # guard that breaks the normal path gets removed. Only the SHAPE
        # is asserted -- reading the operator's actual files would make
        # this test a fact about one machine.
        where = self.iso._real_home("codex", {}, given=False)
        self.assertEqual(".codex", where.name)
        self.assertEqual(Path.home(), where.parent)


class TheControlsAreGeneratedNotEdited(unittest.TestCase):
    """A control edited by hand is how the positive one came to encode
    five untruths about the module it describes. `build.py --check`
    regenerates and compares, so drift is a failure rather than a
    surprise."""

    def test_every_control_matches_what_build_py_generates(self):
        with World(prefix="controls-") as world:
            result = run(
                [sys.executable, str(HARNESS / "controls" / "build.py"), "--check"],
                cwd=Path.cwd(), env=world.env(), timeout=60,
            )
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
