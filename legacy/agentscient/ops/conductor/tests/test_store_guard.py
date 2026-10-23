"""Synthetic live-store comparison tests for concurrent launcher logs.

Contract (only): `ops/conductor/test-store-guard.md`. The helper
`_without_new_job_dirs` in `test_onboard_lever.py` is the subject and
is not edited here. Teardown-shaped comparison is both snapshots
through that helper, matching the contract's both-sides normalize.

Scratch directories only. The live dispatch store is not written.
Worlds, plants and the hermetic root go through ops/testkit.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from test_onboard_lever import _store_snap, _without_new_job_dirs

from ops.testkit import World, plant_bytes

# Measured concurrent name from the contract (suite failure
# `.launcher.iCO9sU` at 16f870930c373b8e266343af222f985898be6303).
LAUNCHER_INCIDENT = ".launcher.iCO9sU"
LAUNCHER_DIGITS = ".launcher.000000"
LAUNCHER_LETTERS = ".launcher.abcdef"
LAUNCHER_MIXED = ".launcher.Ab12Cd"
LAUNCHER_BODY = b"LAUNCHER_LOG_PLANT_iCO9sU\n"

# Canonical job directory shape documented on the helper: stamp-stage-
# harness-hash. Distinctive so a substring of another planted name
# cannot satisfy a pin.
JOB_ORIGINAL = "20260909T120000Z-tests-grok-aaaaaa"
JOB_NEW = "20260910T012738Z-tests-grok-9b1041"

NEAR_FIVE = ".launcher.iCO9s"
NEAR_SEVEN = ".launcher.iCO9sUx"
NEAR_HYPHEN = ".launcher.iCO9-U"
NEAR_UNDERSCORE = ".launcher.iCO9_U"
NEAR_CASE = ".Launcher.iCO9sU"
NEAR_NO_DOT = "launcher.iCO9sU"
NEAR_EMPTY = ".launcher."
ORDINARY = "notes.txt"
ORDINARY_BODY = b"ORDINARY_FILE_PLANT_notes\n"
ORDINARY_HIDDEN = ".env"


class StoreGuardTests(unittest.TestCase):
    """Planted names on scratch stores, then the teardown comparison."""

    def setUp(self):
        self.world = World(prefix="store-guard-")
        self.root = self.world.root
        self.store = self.root / "dispatch"
        self.store.mkdir()
        self.key = str(self.store)
        self.assertTrue(self.store.is_dir(), "plant: scratch store must exist")
        self.assertEqual(
            _store_snap(self.store),
            ("dir", ()),
            "plant: scratch store must start empty",
        )

    def tearDown(self):
        self.world.close()

    def plant_file(self, name: str, body: bytes, store: Path | None = None) -> Path:
        target = (store or self.store) / name
        plant_bytes(target, body)
        self.assertTrue(target.is_file(), f"plant: {name!r} must be a file")
        return target

    def plant_job(self, name: str, store: Path | None = None) -> Path:
        target = (store or self.store) / name
        target.mkdir()
        self.assertTrue(target.is_dir(), f"plant: {name!r} must be a directory")
        self.assertEqual(target.name, name)
        return target

    def assert_planted(self, *names: str, store: Path | None = None) -> None:
        kind, found = _store_snap(store or self.store)
        self.assertEqual(kind, "dir")
        self.assertEqual(found, tuple(sorted(names)))

    def snapshot(self, *stores: Path) -> dict:
        paths = stores or (self.store,)
        return {str(path): _store_snap(path) for path in paths}

    def assert_guard_holds(self, before, after) -> None:
        self.assertEqual(
            _without_new_job_dirs(before, after),
            _without_new_job_dirs(before, before),
        )

    def assert_guard_fails(self, before, after) -> None:
        self.assertNotEqual(
            _without_new_job_dirs(before, after),
            _without_new_job_dirs(before, before),
        )

    def test_new_canonical_launcher_log_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_INCIDENT)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_removed_canonical_launcher_log_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        planted = self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_INCIDENT)

        planted.unlink()
        self.assertFalse(planted.exists(), "plant: launcher log must be gone")
        self.assert_planted(JOB_ORIGINAL)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_unchanged_canonical_launcher_log_with_new_job_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_INCIDENT)

        self.plant_job(JOB_NEW)
        self.assert_planted(JOB_ORIGINAL, JOB_NEW, LAUNCHER_INCIDENT)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_new_canonical_job_directory_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_job(JOB_NEW)
        self.assert_planted(JOB_ORIGINAL, JOB_NEW)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_removed_original_job_directory_still_fails(self):
        planted = self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        planted.rmdir()
        self.assertFalse(planted.exists(), "plant: original job must be gone")
        self.assert_planted()
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertNotIn(JOB_ORIGINAL, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_five_char_suffix_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_FIVE, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_FIVE)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_FIVE, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_seven_char_suffix_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_SEVEN, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_SEVEN)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_SEVEN, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_hyphen_in_suffix_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_HYPHEN, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_HYPHEN)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_HYPHEN, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_underscore_in_suffix_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_UNDERSCORE, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_UNDERSCORE)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_UNDERSCORE, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_wrong_prefix_case_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_CASE, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_CASE)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_CASE, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_missing_leading_dot_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_NO_DOT, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_NO_DOT)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_NO_DOT, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_launcher_empty_suffix_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(NEAR_EMPTY, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, NEAR_EMPTY)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(NEAR_EMPTY, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_ordinary_added_file_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(ORDINARY, ORDINARY_BODY)
        self.assert_planted(JOB_ORIGINAL, ORDINARY)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(ORDINARY, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_ordinary_removed_file_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        planted = self.plant_file(ORDINARY, ORDINARY_BODY)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL, ORDINARY)

        planted.unlink()
        self.assertFalse(planted.exists(), "plant: ordinary file must be gone")
        self.assert_planted(JOB_ORIGINAL)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertNotIn(ORDINARY, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_hidden_ordinary_file_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(ORDINARY_HIDDEN, ORDINARY_BODY)
        self.assert_planted(JOB_ORIGINAL, ORDINARY_HIDDEN)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(ORDINARY_HIDDEN, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_missing_store_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        shutil.rmtree(self.store)
        self.assertFalse(self.store.exists(), "plant: store must be absent")
        after = self.snapshot()
        self.assertEqual(after[self.key], ("absent", ()))

        result = _without_new_job_dirs(before, after)
        self.assertEqual(result[self.key][0], "absent")
        self.assert_guard_fails(before, after)

    def test_store_type_change_still_fails(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        shutil.rmtree(self.store)
        plant_bytes(self.store, b"STORE_NOW_A_FILE\n")
        self.assertTrue(self.store.is_file(), "plant: store must become a file")
        after = self.snapshot()
        self.assertEqual(after[self.key], ("other", ()))

        result = _without_new_job_dirs(before, after)
        self.assertEqual(result[self.key][0], "other")
        self.assert_guard_fails(before, after)

    def test_new_launcher_does_not_mask_ordinary_file(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        self.plant_file(ORDINARY, ORDINARY_BODY)
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_INCIDENT, ORDINARY)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertIn(ORDINARY, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_new_launcher_does_not_mask_removed_job(self):
        planted = self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        planted.rmdir()
        self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        self.assert_planted(LAUNCHER_INCIDENT)
        after = self.snapshot()

        result = _without_new_job_dirs(before, after)
        self.assertNotIn(JOB_ORIGINAL, result[self.key][1])
        self.assert_guard_fails(before, after)

    def test_new_job_and_new_launcher_together_are_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_job(JOB_NEW)
        self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, JOB_NEW, LAUNCHER_INCIDENT)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_digits_only_six_char_launcher_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(LAUNCHER_DIGITS, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_DIGITS)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_letters_only_six_char_launcher_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(LAUNCHER_LETTERS, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_LETTERS)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_mixed_alnum_six_char_launcher_is_tolerated(self):
        self.plant_job(JOB_ORIGINAL)
        before = self.snapshot()
        self.assert_planted(JOB_ORIGINAL)

        self.plant_file(LAUNCHER_MIXED, LAUNCHER_BODY)
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_MIXED)
        after = self.snapshot()

        self.assert_guard_holds(before, after)

    def test_second_store_ordinary_file_is_not_masked(self):
        other = self.root / "xdg-dispatch"
        other.mkdir()
        self.plant_job(JOB_ORIGINAL)
        self.plant_job(JOB_ORIGINAL, store=other)
        before = self.snapshot(self.store, other)
        self.assert_planted(JOB_ORIGINAL)
        self.assert_planted(JOB_ORIGINAL, store=other)

        self.plant_file(LAUNCHER_INCIDENT, LAUNCHER_BODY)
        self.plant_file(ORDINARY, ORDINARY_BODY, store=other)
        self.assert_planted(JOB_ORIGINAL, LAUNCHER_INCIDENT)
        self.assert_planted(JOB_ORIGINAL, ORDINARY, store=other)
        after = self.snapshot(self.store, other)

        result = _without_new_job_dirs(before, after)
        self.assertIn(ORDINARY, result[str(other)][1])
        self.assert_guard_fails(before, after)


if __name__ == "__main__":
    unittest.main()
