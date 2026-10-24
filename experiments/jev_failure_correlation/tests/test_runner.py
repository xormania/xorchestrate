"""Experiment correctness checks; mocked transport outputs are never measurement."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.jev_failure_correlation import runner as r


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.rubric = r.read(r.ROOT / "rubric.json")
        self.packet = {"observations": [{"event": "setup", "error": "ModuleNotFoundError"}],
                       "probe_options": {"p4": "Execute isolated assertion with a valid control", "none": "No useful probe", "insufficient": "Insufficient information"}}
        self.expected = {"cases": {"c1": {"family": "f", "split": "heldout", "kind": "environment_failure", "mechanism": ["infrastructure"], "probe": ["p4"]}}, "relationships": {}}

    def typed(self, body, chosen=None):
        answers = {}
        for name, q in body["questions"].items():
            option = (chosen or {}).get(name, next(iter(q["criteria"])))
            probabilities = {c: float(c == option) for c in q["criteria"]}
            answers[name] = {"type": "choice", "choice": option, "probabilities": probabilities, "confidence": 1.0}
        return {"model": r.MODEL, "answers": answers, "usage": {"input_tokens": 12, "output_tokens": 6}}

    def test_baseline_only_sees_visible_packet(self):
        self.assertEqual(r.baseline_case(self.packet), {"mechanism": "infrastructure", "probe": "p4"})
        missing = {"observations": [], "probe_options": self.packet["probe_options"]}
        self.assertEqual(r.baseline_case(missing)["mechanism"], "insufficient")

    def test_no_unrelated_failure_detection_credit(self):
        predictions = {"cases": {"c1": {"mechanism": "assertion_proxy", "probe": "p4"}}}
        scored = r.score_predictions(predictions, self.expected)
        self.assertFalse(scored["cases"]["c1"]["mechanism"]["correct"])
        self.assertEqual(scored["by_split"]["heldout"]["unrelated_failures_miscredited"], 1)

    def test_missing_distinct_from_abstention(self):
        scored = r.score_predictions({"cases": {}}, self.expected)
        self.assertIsNone(scored["cases"]["c1"]["mechanism"]["correct"])
        self.assertEqual(scored["by_split"]["heldout"]["mechanism_unrun"], 1)
        self.assertNotIn("mechanism_abstentions", scored["by_split"]["heldout"])

    def test_valid_control_false_alarm_denominator(self):
        expected = copy.deepcopy(self.expected)
        expected["cases"]["c1"].update(kind="valid_control", mechanism=["legitimate"])
        scored = r.score_predictions({"cases": {"c1": {"mechanism": "obsolete_result", "probe": "p4"}}}, expected)
        self.assertEqual(scored["by_split"]["heldout"]["valid_control_denominator"], 1)
        self.assertEqual(scored["by_split"]["heldout"]["false_alarms"], 1)

    def test_identity_changes_with_material_input_model_rubric_source(self):
        body = r.request_body(self.packet, self.rubric)
        first = r.identity(body, self.rubric, {"source": "1"})
        modified = copy.deepcopy(body)
        modified["state"]["observations"].append({"event": "later", "status": "unknown"})
        self.assertNotEqual(first, r.identity(modified, self.rubric, {"source": "1"}))
        modified = copy.deepcopy(body)
        modified["model"] = "jev-1.14.0"
        self.assertNotEqual(first, r.identity(modified, self.rubric, {"source": "1"}))
        self.assertNotEqual(first, r.identity(body, {**self.rubric, "version": "2"}, {"source": "1"}))
        self.assertNotEqual(first, r.identity(body, self.rubric, {"source": "2"}))

    def test_leakage_rejected(self):
        for contamination in [{"finding": "case"}, {"event": "F07"}, {"defect_reproduced": True}, {"source_path": "secret"}]:
            with self.subTest(contamination=contamination), self.assertRaises(ValueError):
                r.assert_visible({"observations": [contamination]})

    def test_response_validation_rejects_wrong_model_unknown_choice_nan_missing(self):
        body = r.request_body(self.packet, self.rubric)
        good = self.typed(body)
        self.assertEqual(r.checked_response(good, body["questions"], r.MODEL)["model"], r.MODEL)
        for change in ("model", "choice", "probability", "missing"):
            value = copy.deepcopy(good)
            if change == "model":
                value["model"] = "jev-latest"
            elif change == "choice":
                value["answers"]["mechanism"]["choice"] = "invented"
            elif change == "probability":
                value["answers"]["mechanism"]["probabilities"]["legitimate"] = float("nan")
            else:
                del value["answers"]["mechanism"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                r.checked_response(value, body["questions"], r.MODEL)

    def test_replay_checks_exact_inputs_and_typed_response(self):
        body = r.request_body(self.packet, self.rubric)
        hashes = {"source": "1"}
        aid = r.identity(body, self.rubric, hashes)
        smoke_body = {"model": r.MODEL, "state": {}, "questions": {"transport": {"type": "choice", "instructions": "Select alpha", "criteria": {"alpha": "alpha", "beta": "beta"}}}}
        smoke_id = r.identity(smoke_body, self.rubric, hashes)
        smoke_record = {"id": "smoke", "assessment_id": smoke_id, "request_sha256": r.hashlib.sha256(r.canonical(smoke_body)).hexdigest(), "attempt": 1, "requested_model": r.MODEL, "outcome": "valid", "raw_response_sanitized": json.dumps(self.typed(smoke_body))}
        manifest = {"source_hashes": hashes, "corpus_binding": "fixed", "requests": [{"id": "smoke", "kind": "smoke", "assessment_id": smoke_id}, {"id": "c1", "kind": "case", "assessment_id": aid}]}
        record = {"id": "c1", "assessment_id": aid, "request_sha256": r.hashlib.sha256(r.canonical(body)).hexdigest(),
                  "attempt": 1, "requested_model": r.MODEL, "outcome": "valid", "raw_response_sanitized": json.dumps(self.typed(body))}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            r.write(out / "requests/c1.json", body)
            r.write(out / "requests/smoke.json", smoke_body)
            (out / "attempts.jsonl").write_text(json.dumps(record) + "\n")
            with self.assertRaisesRegex(ValueError, "smoke"):
                r.replay_attempts(out, manifest, self.rubric)
            (out / "attempts.jsonl").write_text(json.dumps(smoke_record) + "\n" + json.dumps(record) + "\n")
            self.assertIn("c1", r.replay_attempts(out, manifest, self.rubric)["cases"])
            changed = copy.deepcopy(body)
            changed["state"]["observations"].append({"status": "unknown"})
            r.write(out / "requests/c1.json", changed)
            with self.assertRaisesRegex(ValueError, "identity"):
                r.replay_attempts(out, manifest, self.rubric)
            r.write(out / "requests/c1.json", body)
            record["raw_response_sanitized"] = json.dumps({**self.typed(body), "model": "jev-latest"})
            (out / "attempts.jsonl").write_text(json.dumps(smoke_record) + "\n" + json.dumps(record) + "\n")
            with self.assertRaises(ValueError):
                r.replay_attempts(out, manifest, self.rubric)

    def test_redaction_preserves_usage_and_removes_credentials(self):
        body = json.dumps({"authorization": "Bearer pretend", "value": "test-only-secret-value", "usage": {"input_tokens": 4, "output_tokens": 2}}).encode()
        safe = r.sanitize_response(body, "test-only-secret-value")
        self.assertNotIn("test-only-secret-value", safe)
        self.assertNotIn("pretend", safe)
        self.assertEqual(json.loads(safe)["usage"]["input_tokens"], 4)

    def test_sources_ignore_generated_results(self):
        before = r.sources()
        with tempfile.TemporaryDirectory(dir=r.ROOT) as tmp:
            Path(tmp, "matrix.json").write_text('{"generated":true}')
            self.assertEqual(before, r.sources())

    def test_missing_credentials_retains_unrun_and_nonzero_status(self):
        manifest = {"corpus_binding": "x"}
        rows = [{"id": "smoke", "kind": "smoke"}, {"id": "c1", "kind": "case"}]
        with tempfile.TemporaryDirectory() as tmp, patch.object(r, "prepare", return_value=(manifest, rows)), patch.dict(r.os.environ, {}, clear=True), patch.object(r, "read", return_value=self.expected):
            self.assertEqual(r.live(tmp), 1)
            status = json.loads(Path(tmp, "live-status.json").read_text())
            self.assertEqual(status["calls"], 0)
            self.assertEqual(status["unrun"], ["smoke", "c1"])
            self.assertEqual(status["execution"], "incomplete")


if __name__ == "__main__":
    unittest.main()
