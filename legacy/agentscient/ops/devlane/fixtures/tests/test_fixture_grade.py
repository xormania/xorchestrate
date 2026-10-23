"""Grade-verb tests, written from the slice-1 contract.

Pins `fixture.sh grade` over a synthetic dispatch record (envelope
findings, `where` in the measured `path:start-end` forms including `;`
and `↔` joins) and a synthetic manifest: caught, missed,
false-positive, unmatched, unlocated; rates from those counts; a
non-review job refuses `record`; calibrate rows carry app and fault
ids.

The lever is the subject and is not read here. A missing lever is
covered by `test_fixture_lever.py`; this file stages the same
copy-or-stub so each assertion goes red on the grading promise.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from ops.testkit import Result, World, make_executable, run

REPO = Path(__file__).resolve().parents[4]
LEVER = REPO / "ops" / "bin" / "fixture.sh"

APP = "synth"
REC_ID = "20260904T000000Z-review-grok-synth01"
AGENT = "Fixture Grade Tester <fixture-grade@example.invalid>"
HARNESS = "grok"
MODEL = "grok-4.6"
RUN_CAP = 30

COUNTS_RE = re.compile(
    r"caught=(?P<caught>\d+)\s+missed=(?P<missed>\d+)\s+"
    r"false-positive=(?P<fp>\d+)\s+unmatched=(?P<unmatched>\d+)\s+"
    r"unlocated=(?P<unlocated>\d+)"
)
RATE_RE = re.compile(
    r"caught/faults=(?P<caught>0(?:\.\d+)?|1(?:\.0+)?)\s+"
    r"false-positive/neighbours=(?P<fp>0(?:\.\d+)?|1(?:\.0+)?)"
)

def _finding(severity: str, where: str, claim: str) -> dict:
    return {
        "severity": severity,
        "where": where,
        "claim": claim,
        "reproduce": "true",
    }


class _GradeFixture(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="fixture-grade-")
        self.addCleanup(self.world.close)
        root = self.world.root
        self.root = root
        self.repo = self.world.cwd
        self.staged = self.repo / "ops" / "bin" / "fixture.sh"
        self.capture = self.repo / "calibrate"
        self._stage_lever()
        self._plant_findings_stub()
        self._plant_manifest()

    def _stage_lever(self) -> None:
        staged = self.world.stage(LEVER, "fixture.sh")
        data = staged.read_bytes()
        self.world.plant_bytes(self.staged, data)
        make_executable(self.staged)
        self.assertEqual(self.staged.read_bytes(), data)

    def _plant_findings_stub(self) -> None:
        path = self.repo / "ops" / "conductor" / "tools" / "findings.py"
        wrapped = self.world.tools.wrap("tee")
        self.world.plant_bytes(path, wrapped.read_bytes())
        make_executable(path)

    def _plant_manifest(self) -> None:
        app = self.repo / "ops" / "devlane" / "fixtures" / "apps" / APP
        manifest = {
            "name": APP,
            "check": "python3 -c 'print(0)'",
            "faults": [
                {
                    "id": "SYN-01",
                    "kind": "K1",
                    "severity": "p1",
                    "location": {"path": "src/a.py", "lines": [10, 12]},
                    "declared": {
                        "text": "Input is passed as arguments.",
                        "path": "README.md",
                        "lines": [3, 3],
                    },
                    "practiced": {
                        "text": "os.system(name)",
                        "path": "src/a.py",
                        "lines": [10, 10],
                    },
                    "finding": "untrusted name reaches a shell",
                    "neighbour": {
                        "path": "src/a.py",
                        "lines": [20, 22],
                        "why_clean": "argv form",
                    },
                    "probe": "sh probes/syn-01.sh",
                    "fix": "fixes/syn-01.patch",
                    "visible_to": "audit",
                },
                {
                    "id": "SYN-02",
                    "kind": "K5",
                    "severity": "p2",
                    "location": {"path": "src/b.py", "lines": [5, 8]},
                    "declared": {
                        "text": "Failures are reported.",
                        "path": "README.md",
                        "lines": [5, 5],
                    },
                    "practiced": {
                        "text": "except Exception: return []",
                        "path": "src/b.py",
                        "lines": [5, 5],
                    },
                    "finding": "errors are swallowed",
                    "neighbour": {
                        "path": "src/b.py",
                        "lines": [15, 17],
                        "why_clean": "strict loader",
                    },
                    "probe": "sh probes/syn-02.sh",
                    "fix": "fixes/syn-02.patch",
                    "visible_to": "test",
                },
            ],
        }
        planted = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        self.world.plant_bytes(app / "FAULTS.json", planted)

    def record_payload(
        self,
        *,
        job: str = "security-audit",
        findings: list[dict] | None = None,
        rec_id: str = REC_ID,
    ) -> dict:
        if findings is None:
            findings = [
                _finding("p1", "src/a.py:10-12", "CAUGHT_CLAIM_SYN"),
                _finding("p2", "src/a.py:20-21", "FP_CLAIM_SYN"),
                _finding("p3", "src/unrelated.py:1-2", "UNMATCHED_CLAIM_SYN"),
                _finding("p3", "the CLI help text", "UNLOCATED_CLAIM_SYN"),
            ]
        return {
            "id": rec_id,
            "job": job,
            "harness": {"name": HARNESS},
            "model": {"ran": MODEL, "requested": MODEL},
            "result": {"envelope": {"findings": findings}},
        }

    def plant_record(
        self,
        payload: dict | None = None,
        *,
        under_dispatches: bool = False,
    ) -> Path:
        body = payload if payload is not None else self.record_payload()
        raw = (json.dumps(body, indent=2) + "\n").encode("utf-8")
        if under_dispatches:
            path = (
                self.repo
                / ".dev"
                / "records"
                / "dispatches"
                / f"{body['id']}.json"
            )
        else:
            path = self.root / f"{body['id']}.json"
        self.world.plant_bytes(path, raw)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["id"], body["id"])
        return path

    def env(self, *, agent: str | None = AGENT) -> dict[str, str]:
        extra = {}
        if agent is not None:
            extra["WF_AGENT"] = agent
        return self.world.env(extra)

    def invoke(self, argv: list[str], *, agent: str | None = AGENT):
        env = self.env(agent=agent)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        return run(
            [str(self.staged), *argv],
            cwd=self.repo,
            env=env,
            timeout=RUN_CAP,
        )

    def grade(self, record: str, app: str = APP, extra: list[str] | None = None, **kwargs):
        argv = ["grade", record, app]
        if extra:
            argv.extend(extra)
        return self.invoke(argv, **kwargs)

    def assert_refusal(self, proc, class_name: str) -> None:
        refusal = proc.refusal()
        token = class_name.lower().replace("_", "-")
        self.assertEqual(
            refusal.cls,
            token,
            f"refusal class must be the leading token {token!r}: {refusal.line!r}",
        )

    def assert_grade_stdout_only_report(self, stdout: str) -> None:
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        self.assertGreaterEqual(len(lines), 2, stdout)
        self.assertLessEqual(
            len(lines),
            4,
            f"grade prints reviewer, counts and rates, nothing else: {stdout!r}",
        )
        self.assertNotIn("{", stdout)
        self.parse_counts(stdout)
        self.parse_rates(stdout)

    def parse_counts(self, stdout: str) -> dict[str, int]:
        match = COUNTS_RE.search(stdout)
        self.assertIsNotNone(
            match,
            f"grade prints caught/missed/false-positive/unmatched/"
            f"unlocated counts: {stdout!r}",
        )
        raw = match.groupdict()
        return {
            "caught": int(raw["caught"]),
            "missed": int(raw["missed"]),
            "false-positive": int(raw["fp"]),
            "unmatched": int(raw["unmatched"]),
            "unlocated": int(raw["unlocated"]),
        }

    def parse_rates(self, stdout: str) -> dict[str, float]:
        match = RATE_RE.search(stdout)
        self.assertIsNotNone(
            match,
            f"grade prints caught/faults and false-positive/neighbours: "
            f"{stdout!r}",
        )
        return {key: float(value) for key, value in match.groupdict().items()}

    def parse_rows(self) -> list[dict]:
        self.assertTrue(
            self.capture.is_file(),
            "grade writes calibrate stdin through findings.py",
        )
        text = self.capture.read_text(encoding="utf-8").strip()
        self.assertTrue(text, "calibrate stdin is empty")
        rows = []
        for line in text.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        self.assertTrue(rows, "calibrate stdin has no objects")
        return rows


class RefusalShape(_GradeFixture):
    def test_embedded_class_token_is_not_a_refusal(self):
        proc = Result(
            argv=("fixture.sh",),
            exit=2,
            stdout="",
            stderr="not-identity garbage expected x; found y; needed z\n",
            duration=0.0,
            cwd=str(self.repo),
        )
        with self.assertRaises(AssertionError):
            self.assert_refusal(proc, "identity")


class GradeClassifies(_GradeFixture):
    def test_grade_counts_caught_missed_false_positive_unmatched_unlocated(self):
        rec = self.plant_record()
        before = rec.read_bytes()
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        self.assertEqual(rec.read_bytes(), before, "grade writes nothing to the record")
        out = proc.stdout
        self.assertIn(HARNESS, out)
        self.assertIn(MODEL, out)
        self.assert_grade_stdout_only_report(out)
        counts = self.parse_counts(out)
        self.assertEqual(
            counts,
            {
                "caught": 1,
                "missed": 1,
                "false-positive": 1,
                "unmatched": 1,
                "unlocated": 1,
            },
        )
        rates = self.parse_rates(out)
        self.assertEqual(rates["caught"], 0.5)
        self.assertEqual(rates["fp"], 0.5)

    def test_grade_rates_are_computed_from_the_counts(self):
        rec = self.plant_record()
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        rates = self.parse_rates(proc.stdout)
        self.assertEqual(rates["caught"], counts["caught"] / 2)
        self.assertEqual(rates["fp"], counts["false-positive"] / 2)

    def test_a_span_overlapping_location_and_neighbour_is_caught_not_fp(self):
        findings = [
            _finding("p1", "src/a.py:10-12,20-22", "BOTH_SPANS_CLAIM_SYN"),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 1)
        self.assertEqual(counts["false-positive"], 0)
        self.assertEqual(counts["missed"], 1)

    def test_semicolon_join_catches_when_any_span_overlaps_a_fault(self):
        findings = [
            _finding(
                "p1",
                "src/unrelated.py:1-1; src/a.py:11-11",
                "SEMI_JOIN_CLAIM_SYN",
            ),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 1)
        self.assertEqual(counts["unmatched"], 0)

    def test_arrow_join_false_positive_when_only_a_neighbour_overlaps(self):
        findings = [
            _finding(
                "p2",
                "src/a.py:20-21 ↔ src/unrelated.py:1-1",
                "ARROW_JOIN_CLAIM_SYN",
            ),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["false-positive"], 1)
        self.assertEqual(counts["caught"], 0)

    def test_comma_join_of_two_paths_catches_the_overlapping_span(self):
        findings = [
            _finding(
                "p1",
                "src/a.py:10-12, src/unrelated.py:8-9",
                "COMMA_JOIN_CLAIM_SYN",
            ),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 1)

    def test_grade_resolves_a_record_id_under_dispatches(self):
        self.plant_record(under_dispatches=True)
        proc = self.grade(REC_ID)
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 1)
        self.assertEqual(counts["missed"], 1)

    def test_two_findings_on_one_fault_count_as_one_caught_fault(self):
        findings = [
            _finding("p1", "src/a.py:10-12", "DUP_A_CLAIM_SYN"),
            _finding("p1", "src/a.py:11-11", "DUP_B_CLAIM_SYN"),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 1)
        self.assertEqual(counts["missed"], 1)
        rates = self.parse_rates(proc.stdout)
        self.assertEqual(rates["caught"], 0.5)
        rows = self.parse_rows()
        caught = [row for row in rows if row["class"] == "caught"]
        self.assertEqual({row["fault_id"] for row in caught}, {"SYN-01"})
        self.assertEqual(
            {row["finding_key"] for row in caught},
            {f"{REC_ID}#1", f"{REC_ID}#2"},
        )
        self.assertEqual(
            {row["claim"] for row in caught},
            {"DUP_A_CLAIM_SYN", "DUP_B_CLAIM_SYN"},
        )

    def test_one_finding_overlapping_two_faults_catches_both(self):
        findings = [
            _finding(
                "p1",
                "src/a.py:10-12, src/b.py:5-8",
                "MULTI_FAULT_CLAIM_SYN",
            ),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 2)
        self.assertEqual(counts["missed"], 0)
        rates = self.parse_rates(proc.stdout)
        self.assertEqual(rates["caught"], 1.0)
        rows = self.parse_rows()
        caught = [row for row in rows if row["class"] == "caught"]
        self.assertEqual({row["fault_id"] for row in caught}, {"SYN-01", "SYN-02"})
        self.assertEqual({row["finding_key"] for row in caught}, {f"{REC_ID}#1"})
        self.assertEqual(
            {row["claim"] for row in caught},
            {"MULTI_FAULT_CLAIM_SYN"},
        )

    def test_closed_span_endpoints_that_touch_overlap(self):
        findings = [
            _finding("p1", "src/a.py:8-10", "ENDPOINT_TOUCH_CLAIM_SYN"),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 1)
        self.assertEqual(counts["missed"], 1)
        self.assertEqual(counts["unmatched"], 0)

    def test_closed_spans_that_do_not_share_a_line_do_not_overlap(self):
        findings = [
            _finding("p1", "src/a.py:8-9", "NO_TOUCH_CLAIM_SYN"),
        ]
        rec = self.plant_record(self.record_payload(findings=findings))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["caught"], 0)
        self.assertEqual(counts["unmatched"], 1)
        self.assertEqual(counts["missed"], 2)

    def test_grade_resolves_a_record_id_from_repo_flag(self):
        other = self.root / "other-repo"
        payload = self.record_payload()
        path = (
            other
            / ".dev"
            / "records"
            / "dispatches"
            / f"{payload['id']}.json"
        )
        raw = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        self.world.plant_bytes(path, raw)
        proc = self.grade(REC_ID, extra=["--repo", str(other)])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["unlocated"], 1)


class GradeRefuses(_GradeFixture):
    def test_grade_refuses_record_when_the_job_is_not_a_review_job(self):
        rec = self.plant_record(self.record_payload(job="plan"))
        proc = self.grade(str(rec))
        self.assert_refusal(proc, "record")
        self.assertFalse(self.capture.exists())

    def test_check_tests_is_a_review_job(self):
        rec = self.plant_record(self.record_payload(job="check-tests"))
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        self.parse_counts(proc.stdout)

    def test_adversarial_review_is_a_review_job(self):
        rec = self.plant_record(
            self.record_payload(job="adversarial-review")
        )
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        self.parse_counts(proc.stdout)


class GradeWritesCalibrate(_GradeFixture):
    def test_calibrate_rows_carry_app_and_fault_ids(self):
        rec = self.plant_record()
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        rows = self.parse_rows()
        for row in rows:
            self.assertEqual(row["app"], APP)
            self.assertEqual(row["record"], REC_ID)
            self.assertIn("fault_id", row)
            self.assertIn("finding_key", row)
            self.assertIn("class", row)
            self.assertIn("harness", row)
            self.assertIn("model", row)
            self.assertIn("claim", row)
            self.assertEqual(row["harness"], HARNESS)
            self.assertEqual(row["model"], MODEL)
        classes = {row["class"] for row in rows}
        self.assertIn("caught", classes)
        self.assertIn("missed", classes)
        self.assertIn("false-positive", classes)
        self.assertIn("unmatched", classes)
        self.assertIn("unlocated", classes)
        caught = [row for row in rows if row["class"] == "caught"]
        self.assertEqual(len(caught), 1)
        self.assertEqual(caught[0]["fault_id"], "SYN-01")
        self.assertEqual(caught[0]["finding_key"], f"{REC_ID}#1")
        self.assertEqual(caught[0]["claim"], "CAUGHT_CLAIM_SYN")
        missed = [row for row in rows if row["class"] == "missed"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["fault_id"], "SYN-02")
        self.assertIsNone(missed[0]["finding_key"])
        unlocated = [row for row in rows if row["class"] == "unlocated"]
        self.assertEqual(len(unlocated), 1)
        self.assertIsNone(unlocated[0]["fault_id"])
        self.assertEqual(unlocated[0]["finding_key"], f"{REC_ID}#4")
        self.assertEqual(unlocated[0]["claim"], "UNLOCATED_CLAIM_SYN")
        false_positive = [row for row in rows if row["class"] == "false-positive"]
        self.assertEqual(len(false_positive), 1)
        self.assertEqual(false_positive[0]["fault_id"], "SYN-01")
        self.assertEqual(false_positive[0]["finding_key"], f"{REC_ID}#2")
        self.assertEqual(false_positive[0]["claim"], "FP_CLAIM_SYN")
        unmatched = [row for row in rows if row["class"] == "unmatched"]
        self.assertEqual(len(unmatched), 1)
        self.assertIsNone(unmatched[0]["fault_id"])
        self.assertEqual(unmatched[0]["finding_key"], f"{REC_ID}#3")
        self.assertEqual(unmatched[0]["claim"], "UNMATCHED_CLAIM_SYN")

    def test_grade_invokes_the_repo_findings_py_not_a_path_lookup(self):
        rec = self.plant_record()
        proc = self.grade(str(rec))
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertTrue(self.capture.is_file())
        self.assertTrue(
            (self.repo / "ops" / "conductor" / "tools" / "findings.py").is_file()
        )
        # The capture file is only written by the stub planted in the
        # invoking repository, so a PATH-only lookup would leave it absent.
        self.assertGreater(self.capture.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
