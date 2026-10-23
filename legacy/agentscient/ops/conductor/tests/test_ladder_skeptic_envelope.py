"""Driver stops on a malformed check-tests envelope, item 3.

Written from contract items 2, 3 and 7 of
`.dev/ladder-inputs/scope-item31-20260911.md`. The tool is the subject
and is not read here. Tests reuse _LadderFixture.

Item 2: each finding is a quotation pair; verdict is changes exactly
when findings is non-empty. Item 3: the driver validates at the
check-tests stop and refuses class envelope naming the field; never a
flake. Item 7: the refusal is one stderr line, exit 2, stdout empty.
"""

from __future__ import annotations

import unittest

from test_ladder_lever import (
    WELL_FORMED_SKEPTIC_COUNTS,
    WELL_FORMED_SKEPTIC_FINDING,
    _LadderFixture,
)

from ops.testkit import World

ZERO_COUNTS = {"p1": 0, "p2": 0, "p3": 0, "opinions": 0}
PROSE_CLAIM = "Contract item 9 requires a fresh check"
EMPTY_PAIR_CLAIM = (
    "declared (ops/devlane/task/CONTRACT.md): ; "
    "practiced: ; misses: x"
)


def _finding(**overrides):
    row = dict(WELL_FORMED_SKEPTIC_FINDING)
    row.update(overrides)
    return row


class _EnvelopeFixture(_LadderFixture):
    """Plant a check-tests envelope and assert the envelope refusal."""

    def plant_check_tests(self, **envelope):
        self.assertIsInstance(self.world, World)
        self.patch_plant(
            verdicts={"check-tests": envelope.get("verdict", "changes")},
            skeptic_envelope=envelope,
        )
        planted = self.load_plant()["skeptic_envelope"]
        self.assertEqual(planted, envelope)

    def assert_envelope_refusal(
        self, proc, field: str, *, dispatches: int = 1,
    ) -> str:
        line = self.assert_refusal(proc, "envelope")
        self.assertIn(field, line, line)
        self.assertNotIn("tests-fix", self.dispatch_names())
        verbs = [row[0] if row else "" for row in self.findings_argvs()]
        self.assertNotIn(
            "import",
            verbs,
            "item 3: a malformed envelope is never imported",
        )
        rec_id = self.record_id_for("check-tests")
        row = self.stage_row(self.ladder_record(), "check-tests")
        self.assertEqual(row.get("reason"), "envelope", row)
        stopped = (self.ladder_record().get("stopped") or {})
        self.assertEqual(
            stopped.get("reason") or row.get("reason"),
            "envelope",
            self.ladder_record(),
        )
        self.assertEqual(
            self.dispatch_names().count("check-tests"),
            dispatches,
            self.dispatch_names(),
        )
        self.assertEqual(rec_id, row.get("record") or rec_id)
        return line


class WellFormedChangesAdvances(_EnvelopeFixture):
    """Item 3 — a well-formed changes envelope advances to tests-fix."""

    def test_a_well_formed_changes_envelope_advances_to_tests_fix(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding()],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertNotIn("reason=envelope", self.combined(proc))
        names = self.dispatch_names()
        self.assertIn("tests-fix", names, names)
        verbs = [row[0] if row else "" for row in self.findings_argvs()]
        self.assertIn("import", verbs, self.findings_argvs())
        path = self.driver_written_envelope_input()
        self.assertTrue(
            path.is_file(),
            "item 4: the driver writes a regular file under the ladder "
            f"inputs directory from the validated envelope; missing {path}",
        )
        self.assertFalse(path.is_symlink(), path)


class MalformedFieldStops(_EnvelopeFixture):
    """Item 3 / item 7 — each malformed field is class envelope."""

    def test_non_empty_artifacts_stops_naming_artifacts(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding()],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={"skeptic": "SKEPTIC.md"},
        )
        planted = self.load_plant()["skeptic_envelope"]
        self.assertEqual(planted["artifacts"], {"skeptic": "SKEPTIC.md"})
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "artifacts")

    def test_findings_not_a_list_stops_naming_findings(self):
        self.plant_check_tests(
            verdict="changes",
            findings={"claim": PROSE_CLAIM},
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "findings")

    def test_a_fifth_field_on_a_finding_stops_naming_the_finding(self):
        row = _finding()
        row["extra"] = "not-a-field"
        self.assertEqual(len(row), 5)
        self.plant_check_tests(
            verdict="changes",
            findings=[row],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        line = self.assert_envelope_refusal(proc, "findings[0]")
        self.assertIn("extra", line)

    def plant_one(self, row):
        self.plant_check_tests(
            verdict="changes",
            findings=[row],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        return self.run_ladder()

    def test_a_finding_labelled_practical_advances(self):
        proc = self.plant_one(_finding(practical="yes", failing="python3 ops/bin/x --y"))
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertIn("tests-fix", self.dispatch_names())

    def test_a_finding_labelled_not_practical_advances(self):
        proc = self.plant_one(_finding(practical="no", failing=None))
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertIn("tests-fix", self.dispatch_names())

    def test_practical_without_its_failing_stops_naming_the_finding(self):
        row = _finding(practical="yes")
        proc = self.plant_one(row)
        line = self.assert_envelope_refusal(proc, "findings[0]")
        self.assertIn("failing", line)

    def test_practical_yes_with_no_failing_invocation_stops_naming_failing(self):
        proc = self.plant_one(_finding(practical="yes", failing=None))
        self.assert_envelope_refusal(proc, "findings[0].failing")

    def test_practical_no_with_a_failing_invocation_stops_naming_failing(self):
        proc = self.plant_one(_finding(practical="no", failing="python3 x"))
        self.assert_envelope_refusal(proc, "findings[0].failing")

    def test_a_practical_outside_yes_or_no_stops_naming_practical(self):
        proc = self.plant_one(_finding(practical="maybe", failing="python3 x"))
        self.assert_envelope_refusal(proc, "findings[0].practical")

    def test_bad_severity_stops_naming_severity(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(severity="high")],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "findings[0].severity")

    def test_an_empty_where_stops_naming_where(self):
        planted_where = ""
        self.assertEqual(planted_where, "")
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(where=planted_where)],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "findings[0].where")

    def test_a_prose_claim_stops_naming_claim(self):
        self.assertNotIn("declared (", PROSE_CLAIM)
        self.assertNotIn("practiced:", PROSE_CLAIM)
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(claim=PROSE_CLAIM)],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        line = self.assert_envelope_refusal(proc, "findings[0].claim")
        self.assertIn(PROSE_CLAIM, line)

    def test_a_claim_with_empty_quote_halves_stops_naming_claim(self):
        claim = EMPTY_PAIR_CLAIM
        self.assertIn("; practiced:", claim)
        self.assertIn("; misses:", claim)
        self.assertRegex(claim, r"declared \([^)]+\):\s*;")
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(claim=claim)],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "findings[0].claim")

    def test_null_reproduce_stops_naming_reproduce(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(reproduce=None)],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "findings[0].reproduce")

    def test_counts_off_by_one_stops_naming_counts(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding()],
            counts=dict(ZERO_COUNTS),
            artifacts={},
        )
        planted = self.load_plant()["skeptic_envelope"]
        self.assertEqual(planted["counts"], ZERO_COUNTS)
        self.assertEqual(len(planted["findings"]), 1)
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "counts")

    def test_approve_with_a_finding_stops_naming_verdict(self):
        self.plant_check_tests(
            verdict="approve",
            findings=[_finding()],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "verdict")

    def test_changes_with_no_findings_stops_naming_verdict(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[],
            counts=dict(ZERO_COUNTS),
            artifacts={},
        )
        planted = self.load_plant()["skeptic_envelope"]
        self.assertEqual(planted["findings"], [])
        self.assertEqual(planted["verdict"], "changes")
        proc = self.run_ladder()
        self.assert_envelope_refusal(proc, "verdict")


class EnvelopeStopIsNeverAFlake(_EnvelopeFixture):
    """Item 3 — resume does not relaunch; --retry is the only further attempt."""

    def test_plain_resume_does_not_relaunch_an_envelope_stop(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(claim=PROSE_CLAIM)],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        first = self.run_ladder()
        self.assert_envelope_refusal(first, "findings[0].claim")
        self.assertEqual(self.dispatch_names().count("check-tests"), 1)
        ladder_id = self.ladder_id()
        second = self.invoke(["resume", ladder_id])
        self.assert_envelope_refusal(second, "findings[0].claim")
        self.assertEqual(self.dispatch_names().count("check-tests"), 1)

    def test_retry_check_tests_relaunches_once(self):
        self.plant_check_tests(
            verdict="changes",
            findings=[_finding(claim=PROSE_CLAIM)],
            counts=dict(WELL_FORMED_SKEPTIC_COUNTS),
            artifacts={},
        )
        first = self.run_ladder()
        self.assert_envelope_refusal(first, "findings[0].claim")
        ladder_id = self.ladder_id()
        second = self.invoke(
            [
                "resume",
                ladder_id,
                "--retry",
                "check-tests",
                "--because",
                "the worker returned a prose claim",
            ]
        )
        self.assert_envelope_refusal(
            second, "findings[0].claim", dispatches=2,
        )


if __name__ == "__main__":
    unittest.main()
