"""Driver-written custody file for tests-fix, item 4.

Written from contract items 4 and 7 of
`.dev/ladder-inputs/scope-item31-20260911.md`. The tool is the subject
and is not read here. Tests reuse _LadderFixture.

Item 4: tests-fix receives a regular file the driver wrote under the
ladder inputs directory from the validated envelope, recorded with
path and digest; a symlink or a path outside is class custody.
Item 7: the refusal is one stderr line, exit 2, stdout empty.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unittest
from pathlib import Path

from test_ladder_lever import (
    SKEPTIC_BODY,
    SKEPTIC_FINDINGS,
    WELL_FORMED_SKEPTIC_CLAIM,
    WELL_FORMED_SKEPTIC_COUNTS,
    WELL_FORMED_SKEPTIC_FINDING,
    _LadderFixture,
)

from ops.testkit import World, run  # noqa: F401 - inherited fixture uses both

SENTINEL = "CUSTODY_TARGET_UNTOUCHED_aa32\n"


def _finding():
    return dict(WELL_FORMED_SKEPTIC_FINDING)


class _CustodyFixture(_LadderFixture):
    """Valid check-tests envelope, optional SKEPTIC.md trap on disk."""

    def plant_valid_changes(self, **extra):
        envelope = {
            "verdict": "changes",
            "findings": [_finding()],
            "counts": dict(WELL_FORMED_SKEPTIC_COUNTS),
            "artifacts": {},
        }
        self.patch_plant(
            verdicts={"check-tests": "changes"},
            skeptic_md=True,
            skeptic_text=SKEPTIC_BODY,
            skeptic_envelope=envelope,
            **extra,
        )
        planted = self.load_plant()
        self.assertTrue(planted["skeptic_md"])
        self.assertEqual(planted["skeptic_envelope"]["artifacts"], {})

    def assert_custody_refusal(self, proc) -> str:
        line = self.assert_refusal(proc, "custody")
        self.assertNotIn("tests-fix", self.dispatch_names())
        record = self.ladder_record()
        stopped = record.get("stopped") or {}
        reasons = [stopped.get("reason")]
        for row in record.get("stages") or []:
            reasons.append(row.get("reason"))
        self.assertIn("custody", reasons, record)
        return line


class TestsFixReceivesTheCustodyFile(_CustodyFixture):
    """Item 4 — regular file under the inputs directory, path and digest."""

    def test_tests_fix_argv_names_a_driver_written_file_not_skeptic_md(self):
        self.plant_valid_changes()
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        trap = self.repo / "SKEPTIC.md"
        self.assertTrue(trap.is_file(), "plant: SKEPTIC.md must exist on disk")
        self.assertEqual(trap.read_text(encoding="utf-8"), SKEPTIC_BODY)
        paths = self.input_paths_for_tests_fix()
        names = [path.name for path in paths]
        self.assertNotIn("SKEPTIC.md", names, paths)
        path = self.driver_written_envelope_input()
        self.assertTrue(
            any(candidate.resolve() == path.resolve() for candidate in paths),
            paths,
        )
        try:
            path.resolve().relative_to(self.ladder_inputs_dir().resolve())
        except ValueError:
            self.fail(
                "item 4: the driver-written file must lie under the "
                f"ladder inputs directory; found {path}"
            )

    def test_the_custody_file_is_a_regular_file_with_the_envelope(self):
        self.plant_valid_changes()
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        path = self.driver_written_envelope_input()
        self.assertTrue(path.is_file(), path)
        self.assertFalse(path.is_symlink(), path)
        self.assertTrue(stat.S_ISREG(path.lstat().st_mode), path)
        text = path.read_text(encoding="utf-8")
        self.assertIn(WELL_FORMED_SKEPTIC_CLAIM, text)
        self.assertNotEqual(text, SKEPTIC_BODY)
        rec_id = self.record_id_for("check-tests")
        dispatch = Path(self.load_plant()["records"][rec_id]["path"])
        envelope = json.loads(dispatch.read_text(encoding="utf-8"))["result"][
            "envelope"
        ]
        self.assertIn(envelope["findings"][0]["claim"], text)
        self.assertIn(str(envelope["verdict"]), text)

    def test_the_tests_fix_row_records_path_and_digest(self):
        self.plant_valid_changes()
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        path = self.driver_written_envelope_input()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row = self.stage_row(self.ladder_record(), "tests-fix")
        inp = row.get("input") or {}
        self.assertIsInstance(inp, dict, row)
        self.assertEqual(inp.get("sha256"), digest, row)
        recorded = Path(str(inp.get("path") or ""))
        if not recorded.is_absolute():
            recorded = self.repo / recorded
        self.assertEqual(recorded.resolve(), path.resolve(), row)

    def test_the_custody_file_resolves_inside_the_inputs_directory(self):
        self.plant_valid_changes()
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        path = self.driver_written_envelope_input()
        inputs_dir = self.ladder_inputs_dir().resolve()
        resolved = path.resolve()
        self.assertTrue(
            stat.S_ISREG(path.lstat().st_mode),
            path,
        )
        try:
            resolved.relative_to(inputs_dir)
        except ValueError:
            self.fail(
                "item 4: resolved custody path must stay under the "
                f"inputs directory; found {resolved} vs {inputs_dir}"
            )


class CustodyRefusesASymlink(_CustodyFixture):
    """Item 4 / item 7 — symlink target and inputs dir are not followed."""

    def _stop_after_tests(self) -> str:
        self.plant_valid_changes(trip_stages=["tests"])
        first = self.run_ladder()
        rec_id = self.record_id_for("tests")
        self.assert_stopped(
            first, stage="tests", record=rec_id, reason="tripped",
        )
        self.close_tripped_record()
        return self.ladder_id()

    def test_a_planted_symlink_at_the_custody_name_is_refused(self):
        ladder_id = self._stop_after_tests()
        inputs_dir = self.ladder_inputs_dir(ladder_id)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        target = self.root / "outside-skeptic-aa32.md"
        self.world.plant_text(target, SENTINEL)
        self.assertEqual(target.read_text(encoding="utf-8"), SENTINEL)
        link = inputs_dir / SKEPTIC_FINDINGS
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        self.assertTrue(link.is_symlink(), link)
        self.assertEqual(os.readlink(link), str(target))
        self.git("add", "-A", "--", ".dev")
        self.git("commit", "-m", "plant custody symlink trap\n")
        self.assertEqual(self.porcelain(), "")
        self.patch_plant(trip_stages=[])
        second = self.invoke(["resume", ladder_id])
        line = self.assert_custody_refusal(second)
        self.assertIn("symlink", line.lower())
        self.assertTrue(link.is_symlink(), "item 4: the symlink stays in place")
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            SENTINEL,
            "item 4: the symlink target is not written",
        )
        try:
            target.resolve().relative_to(inputs_dir.resolve())
            self.fail("plant: symlink target must lie outside the inputs directory")
        except ValueError:
            pass


if __name__ == "__main__":
    unittest.main()
