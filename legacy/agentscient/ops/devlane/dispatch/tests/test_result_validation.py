"""Every result carrier crosses task's semantic boundary (LD-147)."""

from __future__ import annotations

import json
import unittest

import agy_envelope_support as agy
import launch_support as ls

from ops.testkit import World, run


class ResultValidation(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="result-validation-")
        self.addCleanup(self.world.close)
        self.launch = ls.require_module(self, "launch")

    def results(self, candidate):
        text = json.dumps(candidate)
        carriers = [
            (None, text, None),
            ("claude", json.dumps({"type": "result", "structured_output": candidate}), None),
            ("claude", json.dumps({"type": "result", "result": text}), None),
            ("codex", json.dumps({"item": {"type": "agent_message", "text": text}}), None),
            ("codex", text, None),
            ("grok", text, None),
            ("agy", json.dumps(agy.result_frame(agy.published_result(candidate))), None),
            ("agy", json.dumps(agy.result_frame(agy.published_result(
                "", extra={"structured_output": candidate}))), None),
        ]
        job_dir = self.world.tmp / "job"
        self.world.plant_bytes(job_dir / "out/last-message.json", text.encode())
        carriers.append(("codex", "", job_dir))
        for index, (harness, raw, directory) in enumerate(carriers):
            record = {"session": {}}
            result = self.launch._envelope(
                raw.encode(), "security-audit", agy.LAUNCHER_REF,
                harness=harness, job_dir=directory, rec=record)
            yield index, result, record

    def test_tally_mismatch_cannot_approve_and_keeps_the_reported_finding(self):
        candidate = agy.canonical_envelope(findings=[agy.finding("p1")])
        for carrier, result, record in self.results(candidate):
            with self.subTest(carrier=carrier):
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["verdict"])
                self.assertIn("counts", result["note"])
                self.assertEqual(result["findings"], candidate["findings"])
                self.assertEqual(result["counts"], candidate["counts"])
                self.assertTrue(record.get("_envelope_cause"))

    def test_matching_findings_and_opinions_are_accepted(self):
        candidate = agy.canonical_envelope(
            verdict="changes", findings=[agy.finding("p1", reproduce=None)],
            counts={"p1": 1, "p2": 0, "p3": 0, "opinions": 1})
        for carrier, result, record in self.results(candidate):
            with self.subTest(carrier=carrier):
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["verdict"], "changes")
                self.assertEqual(result["counts"], candidate["counts"])
                self.assertNotIn("_envelope_cause", record)

    def test_clean_results_and_optional_commit_survive_json_member_order(self):
        candidate = agy.canonical_envelope(
            verdict="changes", findings=[dict(reversed(list(agy.finding().items())))],
            counts={"p1": 0, "p2": 1, "p3": 0, "opinions": 0})
        candidate["commit"] = {"body": "original change", "subject": "Repair"}
        candidate = dict(reversed(list(candidate.items())))
        for carrier, result, record in self.results(candidate):
            with self.subTest(carrier=carrier):
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["commit"], candidate["commit"])
                self.assertNotIn("_envelope_cause", record)

    def test_shared_semantic_rules_apply_to_every_carrier(self):
        faults = [
            {"status": "tripped", "verdict": "approve"},
            {"status": "invalid", "verdict": "changes"},
            {"status": "invalid", "verdict": None, "note": None},
            {"artifacts": {"report": "embedded\nreport"}},
            {"artifacts": {"report": ""}},
            {"artifacts": {"report": "x" * 513}},
            {"spend": {"total": -1}},
            {"findings": [agy.finding(reproduce=None)],
             "counts": {"p1": 0, "p2": 1, "p3": 0, "opinions": 0}},
        ]
        for fault in faults:
            with self.subTest(fault=fault):
                for carrier, result, record in self.results(agy.canonical_envelope(**fault)):
                    with self.subTest(carrier=carrier):
                        self.assertEqual(result["status"], "invalid", result)
                        self.assertIsNone(result["verdict"])
                        self.assertTrue(result["note"])
                        self.assertTrue(record.get("_envelope_cause"))

    def test_later_semantic_failure_does_not_select_an_earlier_clean_result(self):
        earlier = agy.canonical_envelope(note="earlier clean result")
        later = agy.canonical_envelope(findings=[agy.finding("p1")])
        raw = (json.dumps(earlier) + "\n" + json.dumps(later)).encode()
        for harness in ("codex", "grok"):
            with self.subTest(harness=harness):
                result = self.launch._envelope(
                    raw, "security-audit", agy.LAUNCHER_REF, harness=harness)
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(result["findings"], later["findings"])
                self.assertIsNone(result["verdict"])

    def test_ladder_stops_on_the_contradictory_review(self):
        candidate = agy.canonical_envelope(findings=[agy.finding("p1")])
        program = '''
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import ladder
driver = SimpleNamespace(branch="work", row=lambda name: {})
pending = SimpleNamespace(name="security", record_id="review", model="model",
                          effort="medium", record_path=Path(sys.argv[2]))
try:
    ladder.Driver.load_envelope(driver, pending)
except ladder.Stopped as error:
    print(error.reason)
else:
    print("accepted")
'''
        script = self.world.plant_bytes(self.world.tmp / "read-review.py", program.encode())
        for label, payload, expected in (
                ("fault", candidate, "envelope"),
                ("control", agy.canonical_envelope(), "accepted")):
            with self.subTest(case=label):
                result = self.launch._envelope(
                    json.dumps({"type": "result", "structured_output": payload}).encode(),
                    "security-audit", agy.LAUNCHER_REF, harness="claude")
                record = {"status": "closed", "result": {"envelope": result},
                          "job": "security-audit", "stage": "review",
                          "harness": {"name": "codex"}, "lineage": {"branch": "work"},
                          "unit": "work", "model": {"requested": "model",
                                                     "effort_requested": "medium"}}
                path = self.world.plant_bytes(
                    self.world.tmp / "record.json", json.dumps(record).encode())
                checked = run(["python3", str(script), str(ls.REPO / "ops/conductor/tools"),
                               str(path)], cwd=self.world.tmp, env=self.world.env(), timeout=10)
                self.assertEqual(checked.exit, 0, checked.stderr)
                self.assertEqual(checked.stdout.strip(), expected)
