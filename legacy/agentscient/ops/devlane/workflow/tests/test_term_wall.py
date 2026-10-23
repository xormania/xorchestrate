import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

CHECK = Path(__file__).resolve().parents[1] / "checks" / "term_wall.py"
PATTERN = "zz[q]orblat"
PLANT = "zzqorblat"
MASK = "[forbidden name]"


class TermWallFixture:
    WORLD = World

    def setUp(self):
        self.world = self.WORLD(prefix="term-wall-")
        self.addCleanup(self.world.close)
        self.root = self.world.cwd
        self._git("init", "-q")

    def _git(self, *arguments):
        return run(
            ["git", "-C", str(self.root), *arguments],
            cwd=self.root,
            env=self._environment(PATTERN),
            timeout=60,
            expect=0,
        )

    def _environment(self, pattern=PATTERN):
        extra = {}
        if pattern is not None:
            extra["TERM_WALL"] = pattern
        return self.world.env(extra)

    def _run(self, *arguments, pattern=PATTERN, input_text=None, cwd=None):
        return run(
            [sys.executable, str(CHECK), *map(str, arguments)],
            cwd=cwd or self.root,
            env=self._environment(pattern),
            stdin=input_text,
            timeout=60,
        )

    def _track(self, relative_path, content, binary=False):
        if binary:
            path = self.world.plant_bytes(self.root / relative_path, content)
        else:
            path = self.world.plant_text(self.root / relative_path, content)
        self._git("add", "--", relative_path)
        return path

    def _commit(self, message):
        self._git("commit", "-q", "--allow-empty", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def assertClean(self, result):
        self.assertEqual(0, result.exit, result)
        self.assertEqual("", result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(1, len(lines), result.stdout)
        self.assertTrue(lines[0].strip())

    def assertHit(self, result, surface):
        self.assertEqual(1, result.exit, result)
        self.assertEqual("", result.stderr)
        lines = result.stdout.splitlines()
        self.assertGreaterEqual(len(lines), 1)
        for line in lines:
            self.assertRegex(line, rf"^{re.escape(surface)}: .+: .+$")
            self.assertIn(MASK, line)
        self.assertMasked(result)

    def assertRefusal(self, result, refusal_class):
        self.assertEqual(2, result.exit, result)
        self.assertEqual("", result.stdout)
        lines = result.stderr.splitlines()
        self.assertEqual(1, len(lines), result.stderr)
        self.assertRegex(
            lines[0],
            rf"^{re.escape(refusal_class)}: expected .+; found .+; needed .+$",
        )

    def assertMasked(self, result):
        combined = result.stdout + result.stderr
        self.assertNotIn(PLANT, combined)
        self.assertNotIn(PLANT.upper(), combined)


class TermWallTest(TermWallFixture, unittest.TestCase):
    def test_clean_tree(self):
        self._track("notes.txt", "ordinary text\n")
        self.assertClean(self._run("--root", self.root))

    def test_planted_tracked_content(self):
        self._track("notes.txt", f"before {PLANT} after\n")
        self.assertHit(self._run("--root", self.root), "content")

    def test_planted_tracked_path(self):
        self._track(f"docs/{PLANT}.txt", "ordinary text\n")
        self.assertHit(self._run("--root", self.root), "path")

    def test_message_file_clean(self):
        message = self.world.plant_text(
            self.root / "message.txt", "An ordinary commit message\n"
        )
        self.assertClean(self._run("--message-file", message))

    def test_message_file_planted(self):
        message = self.world.plant_text(
            self.root / "message.txt", f"Mention {PLANT.upper()} here\n"
        )
        self.assertHit(self._run("--message-file", message), "message")

    def test_range_clean(self):
        base = self._commit("base message")
        head = self._commit("ordinary follow-up")
        self.assertClean(self._run("--root", self.root, "--range", f"{base}..{head}"))

    def test_range_planted(self):
        base = self._commit("base message")
        head = self._commit(f"follow-up mentioning {PLANT}")
        self.assertHit(
            self._run("--root", self.root, "--range", f"{base}..{head}"),
            "commit",
        )

    def test_stdin_clean_and_planted(self):
        self.assertClean(self._run("--stdin", input_text="ordinary input\n"))
        self.assertHit(
            self._run("--stdin", input_text=f"input with {PLANT}\n"),
            "stdin",
        )

    def test_missing_message_file_refuses(self):
        result = self._run("--message-file", self.root / "does-not-exist")
        self.assertRefusal(result, "message")

    def test_unresolvable_range_refuses(self):
        result = self._run("--root", self.root, "--range", "missing..also-missing")
        self.assertRefusal(result, "range")

    def test_not_a_git_work_tree_refuses(self):
        directory = self.world.root / "not-a-git-work-tree"
        directory.mkdir()
        result = self._run("--root", directory, cwd=directory)
        self.assertRefusal(result, "root")

    def test_unset_and_empty_pattern_refuse(self):
        for pattern in (None, ""):
            with self.subTest(pattern=pattern):
                result = self._run("--root", self.root, pattern=pattern)
                self.assertRefusal(result, "pattern")
                self.assertEqual(
                    "pattern: expected TERM_WALL set; found empty; needed the org variable "
                    "(CI) or ops/bin/term-wall.conf (local)\n",
                    result.stderr,
                )

    def test_pattern_falls_back_to_conf_file(self):
        self.world.plant_text(
            self.root / "ops" / "bin" / "term-wall.conf", PATTERN + "\n"
        )
        self._track("notes.txt", f"configured match: {PLANT}\n")
        self.assertHit(
            self._run("--root", self.root, pattern=None),
            "content",
        )

    def test_every_match_is_masked(self):
        self._track("notes.txt", f"{PLANT} and {PLANT.upper()}\n")
        result = self._run("--root", self.root)
        self.assertHit(result, "content")
        self.assertEqual(2, result.stdout.count(MASK))

    def test_binary_files_are_skipped(self):
        self._track("payload.bin", b"header\x00" + PLANT.encode("ascii") + b"\n", binary=True)
        self._track("image.png", PLANT.encode("ascii") + b"\n", binary=True)
        self.assertClean(self._run("--root", self.root))


if __name__ == "__main__":
    unittest.main()
