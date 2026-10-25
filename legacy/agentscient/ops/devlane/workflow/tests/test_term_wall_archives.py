"""Captured-data exemptions. Contract: CONTRACT-term-wall.md §Captured data."""

import unittest
from pathlib import Path

import test_term_wall as _term_wall

from ops.testkit import World

PLANT = _term_wall.PLANT
WIP = ".dev/wip"
DISPATCH = ".dev/records/dispatches"


class TermWallArchiveTest(_term_wall.TermWallFixture, unittest.TestCase):
    WORLD = World

    def _plant(self, relative, content):
        path = self._track(relative, content)
        self.assertEqual(content, path.read_text(encoding="utf-8"))
        return path

    def test_wip_contents_exempt(self):
        self._plant(f"{WIP}/note.txt", f"captured {PLANT}\n")
        self._plant(f"{WIP}/run/out.log", f"log {PLANT}\n")
        self.assertClean(self._run("--root", self.root))

    def test_archive_bytes_unchanged(self):
        path = self._plant(f"{WIP}/note.txt", f"captured {PLANT} verbatim\n")
        before = path.read_bytes()
        self._run("--root", self.root)
        self.assertEqual(before, path.read_bytes())

    def test_wip_readme_still_hit(self):
        self._plant(f"{WIP}/README.md", f"authored {PLANT}\n")
        self.assertHit(self._run("--root", self.root), "content")

    def test_code_outside_still_hit(self):
        self._plant("src/tool.py", f"value = '{PLANT}'\n")
        self.assertHit(self._run("--root", self.root), "content")

    def test_direct_dispatch_json_exempt(self):
        self._plant(f"{DISPATCH}/run.json", f'{{"id": "{PLANT}"}}\n')
        self.assertClean(self._run("--root", self.root))

    def test_nested_dispatch_json_not_exempt(self):
        self._plant(f"{DISPATCH}/nested/run.json", f'{{"id": "{PLANT}"}}\n')
        self.assertHit(self._run("--root", self.root), "content")

    def test_non_json_dispatch_not_exempt(self):
        self._plant(f"{DISPATCH}/run.txt", f"id={PLANT}\n")
        self.assertHit(self._run("--root", self.root), "content")

    def test_forbidden_archive_path_still_hit(self):
        relative = f"{WIP}/{PLANT}.txt"
        path = self._track(relative, "ordinary captured text\n")
        self.assertEqual(Path(relative), path.relative_to(self.root))
        self.assertHit(self._run("--root", self.root), "path")

    def test_explicit_wip_path_exempt(self):
        rel = f"{WIP}/note.txt"
        self._plant(rel, f"captured {PLANT}\n")
        self.assertClean(self._run("--root", self.root, rel))

    def test_explicit_dispatch_json_exempt(self):
        rel = f"{DISPATCH}/run.json"
        self._plant(rel, f'{{"id": "{PLANT}"}}\n')
        self.assertClean(self._run("--root", self.root, rel))

    def test_explicit_readme_still_hit(self):
        rel = f"{WIP}/README.md"
        self._plant(rel, f"authored {PLANT}\n")
        self.assertHit(self._run("--root", self.root, rel), "content")

    def test_explicit_nested_still_hit(self):
        rel = f"{DISPATCH}/nested/run.json"
        self._plant(rel, f'{{"id": "{PLANT}"}}\n')
        self.assertHit(self._run("--root", self.root, rel), "content")

    def test_stdin_unchanged(self):
        self.assertHit(
            self._run("--stdin", input_text=f"input with {PLANT}\n"),
            "stdin",
        )


if __name__ == "__main__":
    unittest.main()
