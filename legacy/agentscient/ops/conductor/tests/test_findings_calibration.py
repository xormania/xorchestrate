"""Findings-ledger `calibrate` tests, written from the slice-1 contract.

`ops/conductor/tools/findings.py` is the subject and is not read here.
Tests invoke it as a subprocess with HOME pointed at a temp dir.

The live store (`~/.local/state/agentscient/findings.db` at import-time
HOME) must stay untouched — asserted in tearDown.

`calibrate` is a new writing verb: it upserts grader rows keyed on
record, app, fault id and finding key, is idempotent, refuses
`identity` and `calibration` before acting, leaves `export`
byte-identical to today's empty findings export, writes
`ops/conductor/findings/calibration.jsonl` via explicit `export --calibration --write`,
and makes that export deterministic across insertion order. Today the
verb is unknown (class `arguments`); the exit-0 assertion is the
intended red.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, Result, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "findings.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")

_IMPORT_XDG = os.environ.get("XDG_STATE_HOME")
LIVE_STORES = []
if _IMPORT_XDG:
    LIVE_STORES.append(Path(_IMPORT_XDG) / "agentscient" / "findings.db")
LIVE_STORES.append(Path.home() / ".local" / "state" / "agentscient" / "findings.db")

AGENT = "Calibration Tester <calibration-tester@example.invalid>"
MALFORMED_AGENT = "JustAName"
RUN_CAP = 30

ROW = {
    "record": "20260904T000000Z-review-grok-synth01",
    "app": "synth",
    "fault_id": "SYN-01",
    "finding_key": "20260904T000000Z-review-grok-synth01#1",
    "class": "caught",
    "harness": "grok",
    "model": "grok-4.6",
    "claim": "CAUGHT_CLAIM_SYN",
}

PRESENT_KEYS = ("record", "app", "class", "harness", "model", "claim")


def _live_snapshot() -> list[tuple[str, str, bytes | None]]:
    snaps = []
    for path in LIVE_STORES:
        if path.is_file():
            snaps.append((str(path), "file", path.read_bytes()))
        elif path.exists():
            snaps.append((str(path), "other", None))
        else:
            snaps.append((str(path), "absent", None))
    return snaps


class _CalibrateFixture(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), f"{TOOL} is missing")
        self._live_before = _live_snapshot()
        self.world = World(prefix="findings-calibrate-")
        self.addCleanup(self.world.close)
        root = self.world.root
        self.home = self.world.home
        self.repo = root / "stage" / "repo"
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under HOME is the one asserted.
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='agentscient'\n")
        self.store = self.home / ".local" / "state" / "agentscient" / "findings.db"
        self.assertFalse(self.store.exists(), "isolated store must start absent")
        self.staged = self.world.stage(
            TOOL,
            "repo/ops/conductor/tools/findings.py",
            siblings=SIBLINGS,
        )
        self.calibration_jsonl = (
            self.repo / "ops" / "conductor" / "findings" / "calibration.jsonl"
        )
        self.calibration_jsonl.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            self.assertEqual(
                _live_snapshot(),
                self._live_before,
                "live findings store mutated",
            )
        finally:
            self.world.close()

    def env(self, *, agent: str | None = AGENT) -> dict[str, str]:
        extra = {"LANE_CONF": str(self.conf)}
        if agent is not None:
            extra["WF_AGENT"] = agent
        return self.world.env(extra)

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = AGENT,
        stdin: str = "",
    ):
        env = self.env(agent=agent)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=self.repo,
            env=env,
            stdin=stdin,
            timeout=RUN_CAP,
        )

    def assert_refusal(self, proc, class_name: str) -> None:
        token = class_name.lower().replace("_", "-")
        refusal_wire(RefusalCase(f"{token}-refusal", (), token), proc)

    def jsonl(self, rows: list[dict]) -> str:
        return "".join(json.dumps(row) + "\n" for row in rows)

    def parse_jsonl(self, text: str) -> list[dict]:
        rows = []
        for line in text.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def calibrate(self, rows: list[dict], **kwargs):
        return self.invoke(["calibrate"], stdin=self.jsonl(rows), **kwargs)

    def export_findings(self):
        return self.invoke(["export"], agent=None)

    def export_calibration(self):
        return self.invoke(["export", "--calibration"], agent=None)

    def exported_rows(self) -> list[dict]:
        exported = self.export_calibration()
        self.assertEqual(exported.exit, 0, exported.stderr)
        return self.parse_jsonl(exported.stdout)


class RefusalShape(_CalibrateFixture):
    def test_embedded_class_token_is_not_a_refusal(self):
        proc = Result(
            argv=("findings.py",),
            exit=2,
            stdout="",
            stderr="not-identity garbage expected x; found y; needed z\n",
            duration=0.0,
            cwd=str(self.world.cwd),
        )
        with self.assertRaises(AssertionError):
            self.assert_refusal(proc, "identity")


class CalibrateWrites(_CalibrateFixture):
    def test_calibrate_upserts_a_row_under_temp_home(self):
        proc = self.calibrate([ROW])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        rows = self.exported_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["record"], ROW["record"])
        self.assertEqual(rows[0]["app"], ROW["app"])
        self.assertEqual(rows[0]["fault_id"], ROW["fault_id"])
        self.assertEqual(rows[0]["finding_key"], ROW["finding_key"])
        self.assertEqual(rows[0]["class"], "caught")
        self.assertEqual(rows[0]["claim"], ROW["claim"])
        self.assertEqual(rows[0]["harness"], ROW["harness"])
        self.assertEqual(rows[0]["model"], ROW["model"])

    def test_calibrate_is_idempotent_on_the_same_row(self):
        first = self.calibrate([ROW])
        self.assertEqual(first.exit, 0, first.stderr)
        second = self.calibrate([ROW])
        self.assertEqual(second.exit, 0, second.stderr)
        rows = self.exported_rows()
        self.assertEqual(len(rows), 1, rows)

    def test_calibrate_upserts_non_key_fields_on_the_same_key(self):
        first = self.calibrate([ROW])
        self.assertEqual(first.exit, 0, first.stderr)
        updated = dict(ROW)
        updated["class"] = "missed"
        updated["claim"] = "UPDATED_CLAIM_SYN"
        updated["harness"] = "codex"
        updated["model"] = "gpt-5.6"
        second = self.calibrate([updated])
        self.assertEqual(second.exit, 0, second.stderr)
        rows = self.exported_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["class"], "missed")
        self.assertEqual(rows[0]["claim"], "UPDATED_CLAIM_SYN")
        self.assertEqual(rows[0]["harness"], "codex")
        self.assertEqual(rows[0]["model"], "gpt-5.6")

    def test_calibrate_discriminates_each_part_of_the_composite_key(self):
        variants = [
            ROW,
            {**ROW, "record": "other-record"},
            {**ROW, "app": "other-app"},
            {**ROW, "fault_id": "SYN-02"},
            {**ROW, "finding_key": f"{ROW['record']}#2"},
        ]
        proc = self.calibrate(variants)
        self.assertEqual(proc.exit, 0, proc.stderr)
        rows = self.exported_rows()
        self.assertEqual(len(rows), 5, rows)
        keys = {
            (row["record"], row["app"], row["fault_id"], row["finding_key"])
            for row in rows
        }
        self.assertEqual(len(keys), 5, keys)
        updated = dict(ROW)
        updated["claim"] = "ONLY_DEFAULT_KEY_UPDATED"
        second = self.calibrate([updated])
        self.assertEqual(second.exit, 0, second.stderr)
        after = self.exported_rows()
        self.assertEqual(len(after), 5, after)
        matched = [
            row
            for row in after
            if (row["record"], row["app"], row["fault_id"], row["finding_key"])
            == (ROW["record"], ROW["app"], ROW["fault_id"], ROW["finding_key"])
        ]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["claim"], "ONLY_DEFAULT_KEY_UPDATED")
        others = [
            row
            for row in after
            if (row["record"], row["app"], row["fault_id"], row["finding_key"])
            != (ROW["record"], ROW["app"], ROW["fault_id"], ROW["finding_key"])
        ]
        self.assertEqual(len(others), 4)
        self.assertEqual({row["claim"] for row in others}, {ROW["claim"]})

    def test_calibrate_null_keys_are_distinct_rows(self):
        first = dict(ROW)
        first["fault_id"] = None
        first["finding_key"] = None
        first["class"] = "unmatched"
        first["claim"] = "NULL_A"
        second = dict(first)
        second["record"] = "other-record"
        second["claim"] = "NULL_B"
        third = dict(ROW)
        third["fault_id"] = None
        third["finding_key"] = f"{ROW['record']}#9"
        third["class"] = "unlocated"
        third["claim"] = "NULL_FAULT"
        fourth = dict(ROW)
        fourth["fault_id"] = "SYN-09"
        fourth["finding_key"] = None
        fourth["class"] = "missed"
        fourth["claim"] = "NULL_FINDING"
        proc = self.calibrate([first, second, third, fourth])
        self.assertEqual(proc.exit, 0, proc.stderr)
        rows = self.exported_rows()
        self.assertEqual(len(rows), 4, rows)
        keys = {
            (row["record"], row["app"], row["fault_id"], row["finding_key"])
            for row in rows
        }
        self.assertEqual(len(keys), 4, keys)
        again = self.calibrate([first])
        self.assertEqual(again.exit, 0, again.stderr)
        after = self.exported_rows()
        self.assertEqual(len(after), 4, after)


class CalibrateRefuses(_CalibrateFixture):
    def test_calibrate_refuses_identity_without_wf_agent(self):
        proc = self.calibrate([ROW], agent=None)
        self.assert_refusal(proc, "identity")

    def test_calibrate_refuses_identity_on_a_malformed_agent(self):
        proc = self.calibrate([ROW], agent=MALFORMED_AGENT)
        self.assert_refusal(proc, "identity")

    def test_calibrate_refuses_calibration_on_empty_stdin(self):
        proc = self.invoke(["calibrate"], stdin="")
        self.assert_refusal(proc, "calibration")

    def test_calibrate_refuses_calibration_on_non_json(self):
        proc = self.invoke(["calibrate"], stdin="not-json\n")
        self.assert_refusal(proc, "calibration")

    def test_calibrate_refuses_calibration_on_each_missing_key(self):
        for key in PRESENT_KEYS:
            with self.subTest(missing=key):
                row = dict(ROW)
                del row[key]
                proc = self.calibrate([row])
                self.assert_refusal(proc, "calibration")
                self.assertFalse(self.store.exists())

    def test_calibrate_refuses_calibration_on_an_unknown_class(self):
        row = dict(ROW)
        row["class"] = "not-a-class"
        proc = self.calibrate([row])
        self.assert_refusal(proc, "calibration")

    def test_calibrate_refuses_before_acting_on_mixed_valid_and_invalid(self):
        bad = dict(ROW)
        del bad["app"]
        proc = self.calibrate([ROW, bad])
        self.assert_refusal(proc, "calibration")
        self.assertFalse(self.store.exists())
        exported = self.export_calibration()
        if exported.exit == 0:
            self.assertEqual(exported.stdout.strip(), "")
        self.assertFalse(self.calibration_jsonl.exists())


class ExportStaysPut(_CalibrateFixture):
    def test_findings_export_is_byte_identical_to_today_after_calibrate(self):
        absent = self.export_findings()
        self.assert_refusal(absent, "store")
        self.assertFalse(self.store.exists())
        self.assertEqual(self.invoke(["import"]).exit, 0)
        before = self.export_findings()
        self.assertEqual(before.exit, 0, before.stderr)
        self.assertEqual(
            before.stdout,
            "",
            "empty-store findings export is empty today",
        )
        proc = self.calibrate([ROW])
        self.assertEqual(proc.exit, 0, proc.stderr)
        after = self.export_findings()
        self.assertEqual(after.exit, 0, after.stderr)
        self.assertEqual(after.stdout, before.stdout)
        self.assertEqual(after.stdout, "")

    def test_export_calibration_writes_tracked_jsonl_deterministically(self):
        other = dict(ROW)
        other["fault_id"] = "SYN-02"
        other["finding_key"] = None
        other["class"] = "missed"
        other["claim"] = "MISSED_CLAIM_SYN"
        proc = self.calibrate([ROW, other])
        self.assertEqual(proc.exit, 0, proc.stderr)
        first = self.export_calibration()
        self.assertEqual(first.exit, 0, first.stderr)
        self.assertTrue(first.stdout.strip(), "calibration export is empty")
        self.assertFalse(self.calibration_jsonl.exists(), "observation must not export to a file")
        denied = self.invoke(["export", "--calibration", "--write"], agent=None)
        self.assert_refusal(denied, "identity")
        self.assertFalse(self.calibration_jsonl.exists())
        written = self.invoke(["export", "--calibration", "--write"])
        self.assertEqual(written.exit, 0, written.stderr)
        self.assertTrue(
            self.calibration_jsonl.is_file(),
            "only explicit --write creates ops/conductor/findings/calibration.jsonl",
        )
        self.assertEqual(
            self.calibration_jsonl.read_text(encoding="utf-8"),
            first.stdout,
        )
        stamp = self.calibration_jsonl.stat().st_mtime_ns
        second = self.export_calibration()
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(self.calibration_jsonl.stat().st_mtime_ns, stamp)
        if self.store.is_file():
            self.store.unlink()
        self.calibration_jsonl.unlink()
        reversed_rows = self.calibrate([other, ROW])
        self.assertEqual(reversed_rows.exit, 0, reversed_rows.stderr)
        third = self.export_calibration()
        self.assertEqual(third.exit, 0, third.stderr)
        self.assertEqual(third.stdout, first.stdout)
        self.assertFalse(self.calibration_jsonl.exists())
        self.assertEqual(self.invoke(["export", "--calibration", "--write"]).exit, 0)
        self.assertEqual(
            self.calibration_jsonl.read_text(encoding="utf-8"),
            first.stdout,
        )


if __name__ == "__main__":
    unittest.main()
