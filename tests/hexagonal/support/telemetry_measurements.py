"""Actual finite procedure subjects and external storage/response observations."""

import base64
import hashlib
import importlib.util
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.hexagonal.support.telemetry_preservation import read_database
from xorchestrate.model import OrchestrationError

ROOT = Path(__file__).resolve().parents[3]
OPERATIONS = {"telemetry_events", "telemetry_payload", "telemetry_snapshot", "telemetry_verify", "telemetry_restore"}


class Measurement:
    def __init__(self, directory):
        self.directory = directory
        self.directory.mkdir(parents=True)
        self.calls, self.runtimes, self.configurations = [], [], []
        self.fault, self.fault_injected = None, False
        self.phase, self.query_starts = None, 0
        self.module_name = "independent_telemetry_measurement_" + directory.name
        spec = importlib.util.spec_from_file_location(self.module_name, ROOT / "docs/telemetry-evidence/measure_overhead.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[self.module_name] = self.module
        with patch.dict(os.environ, {"TELEMETRY_CALIBRATION_ROOT": str(ROOT),
                "TELEMETRY_CALIBRATION_OUTPUT": str(directory / "legacy-calibration.json")}):
            spec.loader.exec_module(self.module)

    def close(self):
        for runtime in self.runtimes:
            runtime.close()
        sys.modules.pop(self.module_name, None)

    def run(self):
        measure = getattr(self.module, "measure_read_trial", None)
        if not callable(measure):
            # The pre-extension procedure really executed its finite capture
            # calibration. Its valid report supplies the semantic missing-metric
            # witness; a missing import/function is not the failure oracle.
            return self.module.report
        original_build = self.module.build

        def build(*args, **kwargs):
            self.configurations.append(deepcopy(args[0]))
            runtime = original_build(*args, **kwargs)
            self.runtimes.append(runtime)
            original_call = runtime.application.call

            def call(action, arguments, *positional, **keywords):
                if action == "telemetry_events" and arguments.get("after", 0) == 0:
                    self.query_starts += 1
                    self.phase = "first-pass" if self.query_starts == 1 else "repeat-pass"
                row = {"action": action, "arguments": deepcopy(arguments), "pass": self.phase}
                self.calls.append(row)
                if action == "telemetry_snapshot" and not self.fault_injected:
                    if self.fault == "failed-snapshot":
                        self.fault_injected = True
                        raise OrchestrationError("Controlled snapshot failure", "telemetry_integrity")
                    if self.fault == "corrupt-artifact":
                        self._corrupt_artifact(runtime.application.telemetry)
                result = original_call(action, arguments, *positional, **keywords)
                if action == "telemetry_events" and self.fault == "empty-query" and not self.fault_injected:
                    self.fault_injected = True
                    result = {**result, "events": [], "next_after": result["cutoff"], "complete": True}
                row["result"] = deepcopy(result)
                return result

            runtime.application.call = call
            return runtime

        with patch.object(self.module, "build", side_effect=build):
            return measure(self.directory, trial=0, count=2, payload_bytes=2048, contention=False)

    def run_invalid(self, fault):
        self.fault = fault
        try:
            result = self.run()
        except Exception:
            return False
        return not (result.get("valid") is False or result.get("status") in {"failed", "invalid"})

    def completed_calls(self):
        return [row for row in self.calls if "result" in row]

    def _corrupt_artifact(self, sink):
        source = sink.snapshot_source()
        rows = read_database(source["database_path"])
        reference = next(ref for row in rows for ref in row["observation"].get("artifacts", [])
                         if ref["availability"] == "captured")
        digest = reference["digest"].removeprefix("sha256:")
        path = source["artifact_store"].root / "sha256" / digest[:2] / digest
        assert path.is_file()
        path.write_bytes(b"controlled artifact corruption")
        self.fault_injected = True

    def independent_facts(self, report):
        sink = self.runtimes[0].application.telemetry
        source = sink.snapshot_source()
        assert Path(report["source_database_path"]) == source["database_path"]
        assert Path(report["artifact_root"]) == source["artifact_store"].root
        assert report["configuration"] == self.configurations[0]
        assert report["configuration_identity"] == sink.configuration_identity
        source_files = [source["database_path"]]
        source_files.extend(Path(str(source["database_path"]) + suffix) for suffix in ("-wal", "-shm", "-journal"))
        source_files.extend(path for path in source["artifact_store"].root.rglob("*") if path.is_file())
        live_bytes = sum(path.stat().st_size for path in source_files if path.is_file())
        rows = read_database(source["database_path"])
        references = {ref["digest"] for row in rows for ref in row["observation"].get("artifacts", [])
                      if ref["availability"] == "captured"}
        payload_calls = [row for row in self.completed_calls() if row["action"] == "telemetry_payload" and row["pass"] == "first-pass"]
        chunks, offset = [], 0
        for row in payload_calls:
            part = row["result"]
            assert part["offset"] == offset
            chunk = base64.b64decode(part["data_base64"], validate=True)
            chunks.append(chunk)
            offset += len(chunk)
        assert payload_calls[-1]["result"]["next_offset"] is None
        payload = json.loads(b"".join(chunks))
        bundles = {}
        for sample in report["measurements"]:
            if sample["operation"] not in {"telemetry_snapshot", "telemetry_verify", "telemetry_restore"}:
                continue
            path = Path(sample["result"]["manifest_reference"]["path"])
            content = path.read_bytes()
            manifest = json.loads(content)
            database = path.parent / manifest["database"]["path"]
            valid = True
            artifact_ids = set()
            for ref in manifest["artifacts"]:
                digest = ref["digest"].removeprefix("sha256:")
                data = (path.parent / "artifacts/sha256" / digest[:2] / digest).read_bytes()
                valid = valid and hashlib.sha256(data).hexdigest() == digest and len(data) == ref["size_bytes"]
                artifact_ids.add(ref["digest"])
            bundles[str(path)] = {"manifest": manifest, "manifest_bytes": content,
                "database_bytes": database.read_bytes(), "rows": read_database(database),
                "artifact_ids": artifact_ids, "artifacts_valid": valid,
                "sidecars": any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))}
        return {"source_rows": rows, "artifact_ids": references, "payload": payload, "bundles": bundles,
            "live_bytes": live_bytes}
