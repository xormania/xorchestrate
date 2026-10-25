"""Moved corpus for unsafe-command.py: refuse, stay silent, see an amend."""

import importlib.util
import sys
import unittest
from pathlib import Path

import corpus

from ops.testkit import World

HOOK = Path(__file__).resolve().parents[1] / "claude" / "unsafe-command.py"


def load_staged(world):
    staged = world.stage(HOOK, siblings=("command_shape.py",))
    shape_spec = importlib.util.spec_from_file_location(
        "command_shape", staged.with_name("command_shape.py")
    )
    shape = importlib.util.module_from_spec(shape_spec)
    previous = sys.modules.get("command_shape")
    sys.modules["command_shape"] = shape
    shape_spec.loader.exec_module(shape)
    spec = importlib.util.spec_from_file_location("unsafe_command_testkit", staged)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, previous


class UnsafeCommandCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(prefix="unsafe-command-")
        cls.world.enter()
        cls.mod, cls.previous_shape = load_staged(cls.world)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("unsafe_command_testkit", None)
        if cls.previous_shape is None:
            sys.modules.pop("command_shape", None)
        else:
            sys.modules["command_shape"] = cls.previous_shape
        cls.world.close()

    def test_the_moved_tables_are_the_tables_that_were_there(self):
        self.assertEqual(len(corpus.UNSAFE_BLOCK), corpus.COUNTS["UNSAFE_BLOCK"])
        self.assertEqual(len(corpus.UNSAFE_PASS), corpus.COUNTS["UNSAFE_PASS"])
        self.assertEqual(len(corpus.UNSAFE_AMEND), corpus.COUNTS["UNSAFE_AMEND"])
        self.assertEqual(corpus.COUNTS["UNSAFE_BLOCK"], 21)
        self.assertEqual(corpus.COUNTS["UNSAFE_PASS"], 55)
        self.assertEqual(corpus.COUNTS["UNSAFE_AMEND"], 15)

    def test_refuses_each_block_row(self):
        self.assertGreater(len(corpus.UNSAFE_BLOCK), 0)
        for cmd in corpus.UNSAFE_BLOCK:
            with self.subTest(cmd=cmd[:70]):
                hit = self.mod.check(cmd)
                self.assertTrue(
                    hit,
                    f"should refuse, stayed silent: {cmd!r}",
                )

    def test_stays_silent_on_each_pass_row(self):
        self.assertGreater(len(corpus.UNSAFE_PASS), 0)
        for cmd in corpus.UNSAFE_PASS:
            with self.subTest(cmd=cmd[:70]):
                hit = self.mod.check(cmd)
                self.assertFalse(
                    hit,
                    f"false refusal {hit!r} on {cmd!r}",
                )

    def test_amend_detection(self):
        self.assertGreater(len(corpus.UNSAFE_AMEND), 0)
        for cmd, want in corpus.UNSAFE_AMEND:
            with self.subTest(cmd=cmd[:70], want=want):
                self.assertEqual(
                    self.mod.is_amend(cmd), want,
                    f"is_amend({cmd!r}) wanted {want}",
                )

    def test_clipped_sweep_skips_tail(self):
        """Keep | head. Skip | tail. A multi-alternation grep clipped by tail is PASS."""
        tail_10 = (
            'grep -n "record\\|write_text\\|0o600\\|chmod\\|touch(" '
            "scripts/live_test_client_setup.py | tail -10"
        )
        measured = "grep -E 'a|b|c' ops/lane.conf | tail -3"
        self.assertIn(tail_10, corpus.UNSAFE_PASS)
        self.assertNotIn(tail_10, corpus.UNSAFE_BLOCK)
        rows = (tail_10, measured)
        self.assertGreater(len(rows), 0)
        for cmd in rows:
            with self.subTest(cmd=cmd[:70]):
                hit = self.mod.check(cmd)
                self.assertFalse(
                    hit,
                    f"clipped-sweep must skip tail; found {hit!r} on {cmd!r}",
                )

    def test_plan_d3_writing_a_dot_sh_file_is_not_running_it(self):
        """cat > x.sh and tee notes.sh were refused because \\bsh\\b matched the name."""
        rows = [
            """cat > setup.sh <<'EOF'
s = p.read_text()
p.write_text(s.replace(old, new))
EOF""",
            """tee notes.sh <<'EOF'
s = p.read_text()
p.write_text(s.replace(old, new))
EOF""",
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd.splitlines()[0]):
                self.assertIn(cmd, corpus.UNSAFE_PASS)
                self.assertFalse(
                    self.mod.check(cmd),
                    f"writing a .sh file was refused: {cmd!r}",
                )
