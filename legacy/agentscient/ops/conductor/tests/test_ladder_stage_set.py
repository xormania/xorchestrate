"""The stage set is chosen per work order (scope-stage-set-20260912.md).

Written from contract items 1 to 7 and 9. ladder.py is the subject and is
not read here; the ladder runs on _LadderFixture, whose seeded repository
carries the seed table. One producer wrote the driver change and this file
(owner, 2026-09-12: "hand grant - whatever - get it done"); the second
producer is absent and said so in the record.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from test_ladder_lever import AGENT, RUN_CAP, _LadderFixture

from ops.testkit import World, run

NINE = ("tests", "check-tests", "tests-fix", "code", "security", "adversarial",
        "fix-tests", "fix-code", "security-re")
CONTRACT_SET = ("tests", "check-tests", "tests-fix", "code", "adversarial")


def _plan(cls: str | None, name: str = "slice-x") -> str:
    tail = f"\t{cls}" if cls else ""
    return (
        "# plan\n\n"
        f"slice\t{name}\tS{tail}\n"
        f"bucket\t{name}\tS\tops/conductor/tools/ladder.py\n"
    )


class _StageSetFixture(_LadderFixture):
    def table_path(self) -> Path:
        return self.repo / "ops" / "conductor" / "stage-sets.json"

    def plant_table(self, table: dict | None) -> None:
        rel = "ops/conductor/stage-sets.json"
        if table is None:
            self.git("rm", "-q", rel)
            self.git("commit", "-m", "remove table\n")
            self.assertFalse(self.table_path().exists())
            return
        text = json.dumps(table, indent=2) + "\n"
        self.world.plant_text(self.table_path(), text)
        self.git("add", rel)
        self.git("commit", "-m", "plant table\n")
        self.assertEqual(
            json.loads(self.table_path().read_text(encoding="utf-8")), table,
        )
        self.assertIsInstance(self.world, World)

    def skipped_by_set(self, record: dict, cls: str) -> list[str]:
        return [
            row["name"] for row in record["stages"]
            if row.get("outcome") == "skipped"
            and row.get("condition") == f"not in stage set {cls}"
        ]


class SeedTable(_StageSetFixture):
    def test_seed_table_is_planted_with_both_classes(self):
        table = json.loads(self.table_path().read_text(encoding="utf-8"))
        self.assertEqual(tuple(table["lane"]), NINE)
        self.assertEqual(tuple(table["contract"]), CONTRACT_SET)


class DefaultAndPlanClass(_StageSetFixture):
    def test_planless_ladder_runs_lane_by_declared_default(self):
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        record = self.ladder_record()
        chosen = record["stage_set"]
        self.assertEqual(chosen["name"], "lane")
        self.assertEqual(chosen["source"], "default")
        self.assertEqual(tuple(chosen["stages"]), NINE)
        self.assertEqual(record["stage_sets"]["path"], str(self.table_path()))
        self.assertRegex(record["stage_sets"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.skipped_by_set(record, "lane"), [])
        self.assertIn("security", self.dispatch_names())
        self.assertIn("adversarial", self.dispatch_names())

    def test_contract_class_skips_security_and_the_fix_round(self):
        self.plant_plan(_plan("contract"))
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        record = self.ladder_record()
        self.assertEqual(record["stage_set"]["name"], "contract")
        self.assertEqual(record["stage_set"]["source"], "plan")
        names = self.dispatch_names()
        self.assertIn("adversarial", names)
        self.assertNotIn("security", names)
        self.assertNotIn("fix-tests", names)
        self.assertEqual(
            self.skipped_by_set(record, "contract"),
            ["security", "fix-tests", "fix-code", "security-re"],
        )
        self.assertEqual(record["calibration"][0]["stage_set"], "contract")

    def test_unknown_plan_class_refuses_plan(self):
        self.plant_plan(_plan("nope"))
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 2, self.combined(proc))
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)
        self.assertTrue(proc.stderr.startswith("plan: expected "), proc.stderr)
        self.assertIn("nope", proc.stderr)
        self.assertEqual(self.dispatch_names(), [])


class Override(_StageSetFixture):
    def test_override_without_because_refuses_arguments(self):
        proc = self.run_ladder("--stage-set", "contract")
        self.assertEqual(proc.returncode, 2, self.combined(proc))
        self.assertTrue(proc.stderr.startswith("arguments: expected "), proc.stderr)
        self.assertEqual(self.dispatch_names(), [])

    def test_override_is_recorded_with_its_reason(self):
        proc = self.run_ladder("--stage-set", "contract", "--because", "docs only today")
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        chosen = self.ladder_record()["stage_set"]
        self.assertEqual(chosen["name"], "contract")
        self.assertEqual(chosen["source"], "override")
        self.assertEqual(chosen["because"], "docs only today")
        self.assertEqual(chosen["by"], AGENT)
        self.assertRegex(chosen["at"], r"^\d{8}T\d{6}Z$")
        self.assertNotIn("security", self.dispatch_names())

    def test_override_with_unknown_class_refuses_stage_set(self):
        proc = self.run_ladder("--stage-set", "nope", "--because", "x")
        self.assertEqual(proc.returncode, 2, self.combined(proc))
        self.assertTrue(proc.stderr.startswith("stage-set: expected "), proc.stderr)
        self.assertIn("nope", proc.stderr)


class TheFloor(_StageSetFixture):
    def _refused(self, table: dict, needle: str) -> None:
        self.plant_table(table)
        self.plant_plan(_plan("bad"))
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 2, self.combined(proc))
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("stage-set: expected "), proc.stderr)
        self.assertIn(needle, proc.stderr)
        self.assertEqual(self.dispatch_names(), [])

    def test_a_set_without_check_tests_is_refused(self):
        self._refused({"bad": ["tests", "code", "adversarial"], "lane": list(NINE)}, "check-tests")

    def test_code_without_a_review_is_refused(self):
        self._refused({"bad": ["tests", "check-tests", "code"], "lane": list(NINE)}, "security or adversarial")

    def test_a_partial_fix_round_is_refused(self):
        self._refused({"bad": ["tests", "check-tests", "code", "adversarial", "fix-tests"], "lane": list(NINE)}, "fix-tests and fix-code")

    def test_security_re_without_the_fix_round_is_refused(self):
        self._refused({"bad": ["tests", "check-tests", "code", "adversarial", "security-re"], "lane": list(NINE)}, "security-re")

    def test_an_unknown_stage_name_is_refused(self):
        self._refused({"bad": ["tests", "check-tests", "code", "adversarial", "deploy"], "lane": list(NINE)}, "deploy")

    def test_an_absent_table_refuses(self):
        self.plant_table(None)
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 2, self.combined(proc))
        self.assertTrue(proc.stderr.startswith("stage-set: expected "), proc.stderr)
        self.assertEqual(self.dispatch_names(), [])


class NoFixRoundInTheSet(_StageSetFixture):
    def test_adversarial_changes_stops_at_the_audit_with_exit_one(self):
        self.plant_plan(_plan("contract"))
        self.patch_plant(verdicts={"adversarial": "changes"})
        proc = self.run_ladder()
        record_id = self.record_id_for("adversarial")
        self.assert_stopped(proc, stage="adversarial", record=record_id, verdict="changes", exit_code=1)
        self.assertNotIn("fix-tests", self.dispatch_names())
        self.assertNotIn("fix-code", self.dispatch_names())


class TheVerb(_StageSetFixture):
    def _verb(self, path: str):
        return run(
            [sys.executable, str(self.staged), "stage-sets", path],
            cwd=str(self.invoke_cwd),
            env=self.env(),
            timeout=RUN_CAP,
        )

    def test_the_verb_prints_a_valid_table(self):
        proc = self._verb(str(self.table_path()))
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(tuple(json.loads(proc.stdout)["contract"]), CONTRACT_SET)

    def test_the_verb_refuses_an_absent_table_on_the_wire(self):
        proc = self._verb(str(self.repo / "nowhere" / "stage-sets.json"))
        self.assertEqual(proc.exit, 2, proc.stdout)
        self.assertEqual(proc.stdout, "")
        lines = proc.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertRegex(lines[0], r"^stage-set: expected .+; found .+; needed .+$")


if __name__ == "__main__":
    unittest.main()
