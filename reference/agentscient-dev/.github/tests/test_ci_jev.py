"""Offline wire fixtures and real workflow-shell checks; never measure model accuracy."""

import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_jev", ROOT / ".github/ci_jev.py")
triage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage)

# Choice wire shape checked against docs.typesafe.ai/api on 2026-09-29.
# This fixture chooses the early error over a later cleanup failure.
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "failure_kind": {"type": "choice", "choice": "environment_failure", "confidence": 0.8,
                         "probabilities": {"environment_failure": 0.9, "product_defect": 0.025,
                                           "test_defect": 0.025, "timing_assertion": 0.025, "unknown": 0.025}},
        "causal_excerpt": {"type": "choice", "choice": "excerpt_0", "confidence": 0.8,
                           "probabilities": {"excerpt_0": 0.9, "excerpt_1": 0.05, "none": 0.05}},
    },
    "usage": {"input_tokens": 1200, "output_tokens": 90},
}


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "command.log"
        self.log.write_text("ERROR: connection refused by fixture service\n" + "progress\n" * 70
                            + "ERROR: cleanup failed because the service never started\n")
        self.evidence = self.root / "runtime"
        self.evidence.mkdir()
        self.config = json.loads(triage.DEFAULT.read_text())
        self.config_path = self.root / "config.json"
        self.save_config()
        self.environment = patch.dict(os.environ, {"TYPESAFE_API_KEY": "fixture-key-never-publish",
                                                    "GITHUB_STEP_SUMMARY": "", "GITHUB_SHA": "a" * 40,
                                                    "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config))

    def assess(self, client=None):
        return triage.assess(self.log, self.evidence, "live-mcp", self.config_path, client)

    def test_shared_transport_sends_two_questions_and_retains_exact_selected_text(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(RESPONSE).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch("devtools.jev.urllib.request.build_opener", return_value=opener):
            result = self.assess()
        self.assertEqual("assessed", result["status"], result)
        request = opener.open.call_args.args[0]
        self.assertEqual("https://api.typesafe.ai/v1/systemone", request.full_url)
        self.assertEqual("Bearer fixture-key-never-publish", request.get_header("Authorization"))
        self.assertEqual(result["request"], json.loads(request.data))
        self.assertEqual({"state", "model", "questions"}, result["request"].keys())
        self.assertEqual({"failure_kind", "causal_excerpt"}, result["request"]["questions"].keys())
        selected = result["selected_excerpt"]
        original = self.log.read_text().splitlines()
        self.assertEqual("\n".join(original[selected["line_start"] - 1:selected["line_end"]]), selected["text"])
        self.assertIn("connection refused", selected["text"])
        self.assertNotIn("cleanup failed", selected["text"])
        self.assertEqual(RESPONSE, result["response"])
        self.assertEqual("a" * 40, result["context"]["GITHUB_SHA"])

    def test_no_secret_disabled_or_missing_log_never_calls_provider(self):
        client = Mock()
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            self.assertEqual("missing_credential", self.assess(client)["reason"])
        self.config["enabled"] = False
        self.save_config()
        self.assertEqual("disabled", self.assess(client)["status"])
        self.config["enabled"] = True
        self.save_config()
        self.log.unlink()
        self.assertEqual("unavailable", self.assess(client)["status"])
        self.log.write_text("")
        self.assertEqual("no_log_evidence", self.assess(client)["reason"])
        client.evaluate.assert_not_called()

    def test_canonical_connection_supports_custom_env_and_private_file_without_secret_leak(self):
        policy = triage.defaults()
        key = "file-credential-never-publish"
        secret = self.root / "jev.key"
        secret.write_text(key + "\n")
        secret.chmod(0o600)
        self.log.write_text(self.log.read_text().replace("connection refused", "connection refused " + key))
        policy["connection"]["credential"] = {"file": str(secret)}
        with patch.object(triage, "defaults", return_value=policy):
            result = self.assess(Mock(evaluate=Mock(return_value=RESPONSE)))
        self.assertEqual("assessed", result["status"], result)
        self.assertNotIn(key, json.dumps(result))
        policy["connection"]["credential"] = {"env": "OPERATOR_JEV"}
        with patch.object(triage, "defaults", return_value=policy), \
                patch.dict(os.environ, {"TYPESAFE_API_KEY": "", "OPERATOR_JEV": key}):
            result = self.assess(Mock(evaluate=Mock(return_value=RESPONSE)))
        self.assertEqual("assessed", result["status"], result)
        self.assertNotIn(key, json.dumps(result))

    def test_unknown_choices_bad_model_and_incomplete_results_are_unavailable(self):
        cases = []
        for name, field, value in (("causal_excerpt", "choice", "invented"),
                                    ("failure_kind", "confidence", float("nan"))):
            result = copy.deepcopy(RESPONSE)
            result["answers"][name][field] = value
            cases.append(result)
        cases += [RESPONSE | {"model": "jev-latest"}, RESPONSE | {"answers": {}}]
        for response in cases:
            with self.subTest(response=response):
                result = self.assess(Mock(evaluate=Mock(return_value=response)))
                self.assertEqual("unavailable", result["status"])
                self.assertNotIn("selected_excerpt", result)
                self.assertNotIn("response", result)

    def test_provider_failure_is_a_retained_advisory_result_without_exception_text(self):
        result = self.assess(Mock(evaluate=Mock(side_effect=TimeoutError("secret diagnostic text"))))
        self.assertEqual("unavailable", result["status"])
        self.assertNotIn("secret diagnostic", json.dumps(result))
        with contextlib.redirect_stdout(io.StringIO()):
            triage.publish(result, self.root / "report")
        self.assertTrue((self.root / "report/assessment.json").is_file())
        self.assertIn("failure is unchanged", (self.root / "report/summary.md").read_text())

    def test_low_confidence_keeps_raw_distribution_without_asserting_a_cause(self):
        response = copy.deepcopy(RESPONSE)
        for answer in response["answers"].values():
            answer["confidence"] = 0.2
        result = self.assess(Mock(evaluate=Mock(return_value=response)))
        self.assertEqual("unknown", result["likely_cause"])
        self.assertIsNone(result["selected_excerpt"])
        self.assertEqual(response, result["response"])

    def test_secrets_are_scrubbed_before_request_and_logs_cannot_inject_commands_or_html(self):
        self.log.write_text(self.log.read_text().replace("fixture service", "fixture-key-never-publish\n"
                            "::error::not-a-workflow-command\n<img src=x>\nBearer another-secret"))
        (self.evidence / "result.json").write_text(json.dumps({"password": "private-fixture-value", "exit": 1}))
        result = self.assess(Mock(evaluate=Mock(return_value=RESPONSE)))
        self.assertEqual("assessed", result["status"], result)
        for secret in ("fixture-key-never-publish", "another-secret", "private-fixture-value"):
            self.assertNotIn(secret, json.dumps(result))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            triage.publish(result, self.root / "report")
        self.assertEqual(1, len(output.getvalue().splitlines()))
        self.assertIn("%0A::error::not-a-workflow-command", output.getvalue())
        self.assertNotIn("<img", (self.root / "report/summary.md").read_text())
        self.assertIn("&lt;img", (self.root / "report/summary.md").read_text())

    def test_symlinks_large_evidence_and_byte_limits_are_explicit(self):
        (self.evidence / "result.json").symlink_to(self.log)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "result.json").write_text('{"secret":"must-not-be-read"}')
        (self.evidence / "linked").symlink_to(outside, target_is_directory=True)
        (self.evidence / "check-large.json").write_text("x" * 140000)
        self.log.write_text("ERROR: first cause\n" + "long line\n" * 400)
        self.config["limits"]["log_bytes"] = 1000
        self.save_config()
        state = triage.collect(self.log, self.evidence, self.config)
        self.assertTrue(state["log"]["partial"])
        self.assertEqual([], state["runtime_evidence"])
        self.assertGreaterEqual(state["omitted_evidence_files"], 2)
        self.assertNotIn("must-not-be-read", json.dumps(state))

    def test_input_budget_accounts_for_unicode_and_records_omitted_candidates(self):
        self.log.write_text(("ERROR: cause\n" + "語" * 100 + "\n" + "details\n" * 60) * 20)
        state = triage.collect(self.log, self.evidence, self.config)
        self.assertEqual(8, len(state["candidates"]))
        policy = triage.defaults()
        policy["transport"]["max_request_bytes"] = 4000
        request = triage.request_for(state, self.config, policy)
        self.assertLessEqual(len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode()), 4000)
        self.assertGreater(state["log"]["omitted_candidates"], 12)
        self.assertGreaterEqual(len(state["candidates"]), 1)

    def test_verbose_passing_matches_do_not_hide_the_final_failure_from_the_request(self):
        original = ["test_expected_exception_" + str(index) + "_" + "context_" * 18 + " ... ok"
                    for index in range(200)]
        original.append("ERROR: intentional feedback calibration after all original checks passed")
        self.log.write_text("\n".join(original) + "\n")
        state = triage.collect(self.log, self.evidence, self.config)
        request = triage.request_for(state, self.config, triage.defaults())
        final = [candidate for candidate in request["state"]["candidates"]
                 if original[-1] in candidate["text"]]
        self.assertEqual(1, len(final))
        self.assertEqual(len(original), final[0]["line_end"])
        for candidate in request["state"]["candidates"]:
            self.assertLessEqual(len(candidate["text"].encode()), self.config["limits"]["excerpt_bytes"])
            source = "\n".join(original[candidate["line_start"] - 1:candidate["line_end"]])
            self.assertTrue(source.startswith(candidate["text"]))

    def test_oversized_leading_context_does_not_displace_the_matched_failure(self):
        original = ["normal context " + "語" * 1000] * 5 + ["ERROR: actual startup failed"]
        self.log.write_text("\n".join(original) + "\n")
        state = triage.collect(self.log, self.evidence, self.config)
        self.assertEqual(1, len(state["candidates"]))
        candidate = state["candidates"][0]
        self.assertIn(original[-1], candidate["text"])
        self.assertEqual((6, 6), (candidate["line_start"], candidate["line_end"]))
        self.assertLessEqual(len(candidate["text"].encode()), self.config["limits"]["excerpt_bytes"])

    def test_oversized_logs_keep_early_and_final_failures_at_original_line_numbers(self):
        self.config["limits"]["excerpt_lines"] = 8
        for budget in (1000, self.config["limits"]["log_bytes"]):
            with self.subTest(budget=budget):
                original = ["ERROR: early failure"] + ["ordinary progress " + "x" * 60] * (budget // 60)
                original += ["ERROR: final failure"]
                self.log.write_text("\n".join(original) + "\n")
                self.config["limits"]["log_bytes"] = budget
                state = triage.collect(self.log, self.evidence, self.config)
                self.assertTrue(state["log"]["partial"])
                self.assertLess(state["log"]["read_lines"], len(original))
                excerpts = state["candidates"]
                self.assertTrue(any("early failure" in c["text"] for c in excerpts))
                self.assertTrue(any("final failure" in c["text"] for c in excerpts))
                for candidate in excerpts:
                    self.assertEqual("\n".join(original[candidate["line_start"] - 1:candidate["line_end"]]),
                                     candidate["text"])

    def test_oversized_log_tail_fallback_handles_byte_boundaries_and_unterminated_lines(self):
        self.config["limits"]["log_bytes"] = 1000
        for newline in (b"\n", b"\r\n"):
            for last_newline in (b"", newline):
                # Exercise a tail starting at a line boundary and inside UTF-8 text.
                for padding in range(12):
                    with self.subTest(newline=newline, last_newline=last_newline, padding=padding):
                        original = ["progress 語" + "x" * padding] * 120 + ["final status: incomplete"]
                        self.log.write_bytes(newline.join(line.encode() for line in original) + last_newline)
                        state = triage.collect(self.log, self.evidence, self.config)
                        last = state["candidates"][-1]
                        self.assertIn("final status: incomplete", last["text"])
                        self.assertEqual(len(original), last["line_end"])
                        for candidate in state["candidates"]:
                            self.assertEqual("\n".join(original[candidate["line_start"] - 1:candidate["line_end"]]),
                                             candidate["text"])

    def test_oversized_log_windows_drop_cut_lines_and_never_join_across_the_gap(self):
        self.config["limits"]["log_bytes"] = 1000
        original = ["ERROR: first cause", "Bearer " + "private" * 1000,
                    "ERROR: final cause fixture-key-never-publish"]
        self.log.write_text("\n".join(original))
        state = triage.collect(self.log, self.evidence, self.config)
        candidates = state["candidates"]
        self.assertEqual([(1, 1), (3, 3)], [(c["line_start"], c["line_end"]) for c in candidates])
        self.assertEqual(2, state["log"]["read_lines"])
        self.assertIn("first cause", candidates[0]["text"])
        self.assertIn("final cause", candidates[1]["text"])
        self.assertNotIn("private", json.dumps(state))
        self.assertNotIn("fixture-key-never-publish", json.dumps(state))

    def test_retained_runtime_trace_uses_actual_jsonl_shape_and_latest_bounded_events(self):
        telemetry = self.evidence / "fixture/telemetry"
        telemetry.mkdir(parents=True)
        # Same envelope as devtools.telemetry.Trace.record; arbitrary event data remains data.
        event = {"time": 1.0, "monotonic_ns": 10, "pid": 1, "actor": "coder", "component": "native",
                 "connection": "fixture", "context": {"generation": 1}, "kind": "fixture_event",
                 "data": {"message": "older observation", "password": "must-be-redacted"}}
        recent = copy.deepcopy(event)
        recent["data"]["message"] = "latest failure observation"
        (telemetry / "coder.jsonl").write_text((json.dumps(event) + "\n") * 100 + json.dumps(recent) + "\n")
        self.config["limits"]["evidence_read_bytes"] = 1000
        self.config["limits"]["evidence_events"] = 1
        state = triage.collect(self.log, self.evidence, self.config)
        record = state["runtime_evidence"][0]
        self.assertEqual("fixture/telemetry/coder.jsonl", record["path"])
        self.assertEqual("latest_events", record["selection"])
        self.assertTrue(record["partial"])
        self.assertIn("latest failure observation", record["text"])
        self.assertNotIn("older observation", record["text"])
        self.assertNotIn("must-be-redacted", record["text"])

    def test_bad_config_is_recorded_without_call_and_content_hash_tracks_policy(self):
        client = Mock(evaluate=Mock(return_value=RESPONSE))
        first = self.assess(client)
        self.config["min_confidence"] = 0.9
        self.save_config()
        second = self.assess(client)
        self.assertNotEqual(first["input_sha256"], second["input_sha256"])
        self.config["limits"]["candidates"] = 0
        self.save_config()
        client.reset_mock()
        self.assertEqual("unavailable", self.assess(client)["status"])
        client.evaluate.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())

    def test_diagnosis_is_failure_only_and_does_not_change_the_gate_or_expose_key_to_checks(self):
        for job in ("live-mcp", "maker-mate", "serena"):
            steps = self.workflow["jobs"][job]["steps"]
            verify = next(s for s in steps if s.get("id") == "verify")
            diagnose = next(s for s in steps if s.get("name") == "Diagnose failed check with Jev")
            upload = next(s for s in steps if s.get("name") == "Retain Jev diagnosis")
            self.assertFalse(verify.get("continue-on-error", False))
            self.assertTrue(diagnose["continue-on-error"])
            self.assertEqual("${{ failure() && steps.verify.outcome == 'failure' }}", diagnose["if"])
            self.assertEqual(2, diagnose["timeout-minutes"])
            self.assertTrue(upload["continue-on-error"])
            self.assertEqual("${{ secrets.TYPESAFE_API_KEY }}", diagnose["env"]["TYPESAFE_API_KEY"])
            self.assertNotIn("TYPESAFE_API_KEY", json.dumps(verify))
            self.assertEqual(verify["env"]["JEV_CI_DIR"], diagnose["env"]["JEV_CI_DIR"])
            self.assertEqual(diagnose["env"]["JEV_CI_DIR"] + "/assessment/", upload["with"]["path"])
            self.assertLess(steps.index(verify), steps.index(diagnose))
        self.assertEqual("python3 .github/ci.py gate", self.workflow["jobs"]["lane"]["steps"][-1]["run"])
        self.assertNotIn("TYPESAFE_API_KEY", json.dumps(self.workflow["jobs"]["symfony"]))

    def test_real_workflow_capture_preserves_exit_codes_and_maker_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "var/ci/maker-mate").mkdir(parents=True)
            python = root / "bin/python3"
            python.write_text('#!/bin/bash\nprintf \'{"fixture":true}\\n\'\nprintf \'ERROR: fixture failure\\n\' >&2\nexit "$FIXTURE_EXIT"\n')
            python.chmod(0o755)
            for job in ("live-mcp", "maker-mate", "serena"):
                script = next(s["run"] for s in self.workflow["jobs"][job]["steps"] if s.get("id") == "verify")
                for code in (0, 37, 139):
                    with self.subTest(job=job, code=code):
                        env = {**os.environ, "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
                               "JEV_CI_DIR": str(root / "logs"), "FIXTURE_EXIT": str(code)}
                        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                                                cwd=root, env=env, capture_output=True, text=True)
                        self.assertEqual(code, result.returncode, result.stderr)
                        self.assertIn("ERROR: fixture failure", (root / "logs/command.log").read_text())
                        if job == "maker-mate":
                            self.assertEqual({"fixture": True}, json.loads((root / "var/ci/maker-mate/development-tools.json").read_text()))

    def test_cli_without_key_writes_unavailable_and_exits_successfully(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["python3", str(ROOT / ".github/ci_jev.py"), "--job", "live-mcp",
                                     "--log", directory + "/missing.log", "--output", directory + "/report"],
                                    env={**os.environ, "TYPESAFE_API_KEY": "", "GITHUB_STEP_SUMMARY": ""},
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("missing_credential", json.loads(Path(directory + "/report/assessment.json").read_text())["reason"])


if __name__ == "__main__":
    unittest.main()
