"""Read-only acceptance diagnostics, with real state and private artifact files."""

import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures.control import ControlFixture

from devtools import readiness, role_packages, store, telemetry
from devtools.artifacts import Artifacts

SPEC = importlib.util.spec_from_file_location(
    "acceptance_collector_test", Path(__file__).resolve().parents[1] / "scripts/acceptance_collect.py"
)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
CANARY = "file-backed-secret-for-acceptance-83abf7"
ENV_CANARY = "environment-secret-for-acceptance-348dee"


class AcceptanceCollectTests(ControlFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        output = tempfile.TemporaryDirectory(prefix="acceptance-output-")
        self.addCleanup(output.cleanup)
        self.output = Path(output.name)
        self.runtime = self.output / "runtime"
        self.runtime.mkdir()
        self.manifest = {
            "run_id": "a" * 32, "status": "prepared", "run_root": str(self.state.parent),
            "state": str(self.state), "agentscient_root": str(self.runtime),
            "agentscient_sha": "b" * 40, "baseline_sha": "c" * 40,
            "selected_harnesses": ["codex"], "gates": [],
        }
        self.api = SimpleNamespace(store=store, readiness=readiness, role_packages=role_packages, telemetry=telemetry)
        self.key = self.output / "credential"
        self.key.write_text(CANARY)
        self.key.chmod(0o600)
        overlay = self.state / "fixture-configuration.json"
        settings = json.loads(overlay.read_text())
        settings["project"]["connection"] = {"credential": {"file": str(self.key)}}
        overlay.write_text(json.dumps(settings))

    def operation(self, result):
        with self.store.connect() as db:
            return db.execute(
                "INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,created,updated) "
                "VALUES (?,?,?,'{}','work_check','{}','completed',?,'fixture','fixture')",
                ("coder", self.order, self.job, json.dumps(result)),
            ).lastrowid

    def artifact(self, content, *, order=None):
        saved = Artifacts(self.store, "coder").ingest(
            "check-tests.json", io.BytesIO(json.dumps(content).encode()), "application/json",
            work_order=order or self.order, job=self.job if order in (None, self.order) else None,
        )
        return saved["artifact"]["id"]

    def snapshot(self):
        # SQLite mode=ro may materialize transient WAL reader bookkeeping;
        # compare durable database bytes, configuration and retained files.
        return {str(path.relative_to(self.state)): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file() and not path.is_symlink()
                and path.name not in {"live.sqlite3-wal", "live.sqlite3-shm"}}

    def test_failed_check_artifacts_are_found_filtered_and_read_without_state_mutation(self):
        skipped = self.artifact({"output": "UNRELATED_SUCCESS"})
        failed = self.artifact({"exit_code": 2, "output": "32 test errors " + CANARY,
                                "nested": {"password": "nested-secret"}})
        self.assertNotEqual(12, failed)
        self.operation({"passed": False, "checks": [
            {"exit_code": 0, "evidence": "artifact:" + str(skipped)},
            {"exit_code": 2, "evidence": "artifact:" + str(failed)},
        ]})
        with self.store.connect() as db:
            db.execute("INSERT INTO messages(sender,recipient,body,created,work_order) VALUES (?,?,?,?,?)",
                       ("xo", "owner", json.dumps({"message": ENV_CANARY, "access_token": "nested-token"}),
                        "fixture", self.order))
        before = self.snapshot()
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ENV_CANARY}), patch.object(
            store.Store, "upgrade", side_effect=AssertionError("Read-only diagnostics must not migrate")
        ):
            result = collector.collect(self.manifest, self.api)
        rendered = json.dumps(result)
        for secret in (CANARY, ENV_CANARY, "nested-secret", "nested-token", "UNRELATED_SUCCESS"):
            self.assertNotIn(secret, rendered)
        self.assertIn("32 test errors [REDACTED]", rendered)
        self.assertEqual(["artifact:" + str(failed)], [row["reference"] for row in result["failed_check_artifacts"]])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(store.SCHEMA_VERSION, result["schema_version"])

    def test_private_collision_safe_outputs_stay_outside_run_and_runtime(self):
        report = collector.collect(self.manifest, self.api)
        destination = self.output / "report.json"
        result = collector.write_report(self.manifest, report, destination)
        self.assertEqual(destination, result)
        self.assertEqual(0o600, stat.S_IMODE(destination.stat().st_mode))
        retained = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            collector.write_report(self.manifest, {"replacement": True}, destination)
        self.assertEqual(retained, destination.read_bytes())
        for forbidden in (self.state / "diagnostics.json", self.state.parent / "verifier" / "report.json",
                          self.runtime / "report.json"):
            forbidden.parent.mkdir(exist_ok=True)
            with self.assertRaises(ValueError):
                collector.write_report(self.manifest, report, forbidden)
            self.assertFalse(forbidden.exists())
        link = self.output / "run-alias"
        link.symlink_to(self.state.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            collector.write_report(self.manifest, report, link / "report.json")
        existing_link = self.output / "existing.json"
        existing_link.symlink_to(destination)
        with self.assertRaises(FileExistsError):
            collector.write_report(self.manifest, report, existing_link)
        with patch.object(Path, "home", return_value=self.output):
            first = collector.write_report(self.manifest, report)
            second = collector.write_report(self.manifest, report)
        self.assertNotEqual(first, second)

    def test_baseline_failure_survives_a_later_tail_of_successful_operations(self):
        artifact = self.artifact({"output": "BASELINE_ENVIRONMENT_FAILURE"})
        failed = self.operation({"passed": False, "checks": [{"exit_code": 1, "evidence": "artifact:" + str(artifact)}]})
        for _ in range(45):
            self.operation({"passed": True, "checks": []})
        report = collector.collect(self.manifest, self.api)
        self.assertNotIn(failed, [operation["id"] for operation in report["operations"]])
        self.assertEqual([failed], [operation["id"] for operation in report["failed_checks"]])
        self.assertEqual("BASELINE_ENVIRONMENT_FAILURE", report["failed_check_artifacts"][0]["content"]["output"])

    def test_unavailable_credential_filter_exports_nothing(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "Credential filtering"):
            collector.collect(self.manifest, self.api)
        destination = self.output / "unsafe.json"
        with patch.object(collector, "load_run", return_value=(self.manifest, self.api)), patch("sys.stderr", io.StringIO()) as stderr:
            result = collector.main(["--run-dir", self.manifest["run_root"], "--output", str(destination)])
        self.assertEqual(1, result)
        self.assertFalse(destination.exists())
        self.assertNotIn(CANARY, stderr.getvalue())
        self.assertNotIn(str(self.key), stderr.getvalue())

    def test_oversized_records_are_omitted_and_output_fields_filtered_before_clipping(self):
        self.operation({"checks": [], "output": "x" * (collector.MAX_CELL + 1)})
        report = collector.collect(self.manifest, self.api)
        self.assertEqual("[omitted: size limit]", report["operations"][0]["result"])
        self.assertIn("Oversized database fields were omitted.", report["warnings"])
        self.operation({"output": "x" * 11995 + CANARY + "suffix"})
        report = collector.collect(self.manifest, self.api)
        output = report["operations"][0]["result"]["output"]
        self.assertTrue(output.endswith("[truncated]"))
        self.assertNotIn("file-", output)
        self.assertLess(len(json.dumps(report).encode()), collector.MAX_REPORT)

    def test_artifact_integrity_and_path_escapes_are_not_exported(self):
        ident = self.artifact({"output": "original"})
        self.operation({"checks": [{"exit_code": 1, "evidence": "artifact:" + str(ident)}]})
        with self.store.connect() as db:
            location = db.execute("SELECT location FROM artifacts WHERE id=?", (ident,)).fetchone()[0]
        path = self.state / location
        path.write_text("substituted-secret")
        result = collector.collect(self.manifest, self.api)
        self.assertEqual([], result["failed_check_artifacts"])
        self.assertNotIn("substituted-secret", json.dumps(result))
        path.unlink()
        path.symlink_to(self.key)
        result = collector.collect(self.manifest, self.api)
        self.assertEqual([], result["failed_check_artifacts"])
        self.assertNotIn(CANARY, json.dumps(result))

    def test_telemetry_keeps_catalogue_and_exit_observations_without_terminal_dump(self):
        directory = self.state / "telemetry"
        directory.mkdir(exist_ok=True)
        path = directory / "owner.jsonl"
        values = [
            [], None, 3,
            {"kind": "mcp_in", "data": {"method": "tools/list"}},
            {"kind": "mcp_out", "data": {"result": {"tools": [{"name": "direct"}, {"name": "work_report"}]}}},
            {"kind": "mcp_out", "data": {"method": "notifications/tools/list_changed"}},
            {"kind": "terminal_output", "data": {"text": "DO_NOT_EXPORT_TERMINAL"}},
            {"kind": "console_closed", "data": {"exit_code": 0, "error": CANARY}},
        ]
        path.write_text("\n".join(json.dumps(value) for value in values) + "\n")
        result = collector.collect(self.manifest, self.api)
        self.assertEqual("tools/list", result["console_observations"][0]["method"])
        self.assertEqual(["direct", "work_report"], result["console_observations"][1]["discovered_tools"])
        self.assertEqual("notifications/tools/list_changed", result["console_observations"][2]["method"])
        self.assertEqual(0, result["console_observations"][3]["exit_code"])
        self.assertNotIn("DO_NOT_EXPORT_TERMINAL", json.dumps(result))
        self.assertNotIn(CANARY, json.dumps(result))

    def test_failed_preparation_without_database_keeps_metadata_only(self):
        manifest = {**self.manifest, "state": str(self.state.parent / "absent-state"), "status": "failed"}
        result = collector.collect(manifest, self.api)
        self.assertEqual("failed", result["run"]["status"])
        self.assertNotIn("operations", result)
        self.assertFalse(Path(manifest["state"]).exists())

    def test_escaped_frozen_runner_is_rejected_before_import(self):
        root = self.output / "escaped-run"
        root.mkdir()
        malicious = self.output / "mutable-project"
        (malicious / "acceptance").mkdir(parents=True)
        marker = self.output / "executed"
        (malicious / "acceptance/run.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        (root / "verifier").symlink_to(malicious, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "frozen acceptance runner"):
            collector.load_run(root)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
