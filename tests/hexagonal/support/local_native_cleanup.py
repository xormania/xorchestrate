"""Real managed collection sources and independent retained-byte observations."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, is_zipfile

from agent_telemetry.application import Collector
from agent_telemetry.artifacts import FileArtifactStore
from agent_telemetry.domain import CaptureContext, Event
from agent_telemetry.sqlite import SQLiteRepository

from scripts.testkit.adapters.evidence import write_json
from tests.hexagonal.support.local_native_collection import NativeCollection
from tests.hexagonal.support.telemetry import Fixture
from tests.hexagonal.support.telemetry_preservation import read_database
from xorchestrate.adapters import telemetry_snapshot


def hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class CleanupCollection:
    def __init__(self, root):
        self.native = NativeCollection(root, "healthy")
        self.primary = self.native.descriptor
        self.canary = b"REJECTED-COPY-BINARY-CANARY\x00\xff\x80" + bytes(range(32))
        selected = json.loads(self.primary.read_text())
        objects = FileArtifactStore(self.native.artifacts)
        reference = objects.put(self.canary, "application/octet-stream")
        repository = SQLiteRepository(self.native.database)
        try:
            event = Event("cleanup-binary-control", "fixture.cleanup", '{"content":"binary control"}',
                CaptureContext(selected["project_id"], selected["dataset_id"], selected["collector_id"], "cleanup-fixture"),
                artifacts=(reference,))
            assert Collector(repository, objects).record(event).status == "recorded"
        finally:
            repository.close()
        self.other = self._other_source()
        self.source_roots = [Path(selected["runtime_root"]), self.other["root"]]
        self.native.source_files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for source in self.source_roots for path in source.rglob("*") if path.is_file()}
        prior, unchanged, _ = self.native.collect()
        assert unchanged and prior["telemetry"]["status"] == "verified"
        self.prior = Path(prior["archive"]).parent
        self.prior_before = hashes(self.prior)
        self.sources_before = [hashes(source) for source in self.source_roots]
        self.canary_control_present = self._contains_canary(self.prior)

    def close(self):
        self.native.close()

    def _other_source(self):
        runtime = self.native.directory / "checks" / "legitimate second runtime"
        runtime.mkdir()
        fixture = Fixture(runtime, local_options={"runtime_root": str(runtime / "telemetry"),
            "project_id": "legitimate-project", "dataset_id": "legitimate-dataset",
            "collector_id": "legitimate-collector", "artifact_threshold_bytes": 64})
        try:
            work = fixture.create_work("Legitimate independent source remains intact")
            fixture.record_result(work, {"content": "legitimate-source-content" * 100})
            sink = fixture.app.telemetry
            descriptor = runtime / "telemetry-runtime.json"
            write_json(descriptor, {"schema_version": 1, "status": "configured", "runtime_root": str(sink.root),
                "database": str(sink.path), "artifacts": str(sink.artifact_root), "project_id": sink.project_id,
                "dataset_id": sink.dataset_id, "collector_id": sink.collector_id,
                "content_policy": "protected-local", "source_completion": "unknown"})
            return {"descriptor": descriptor, "root": sink.root, "database": sink.path}
        finally:
            fixture.close()

    def _contains_canary(self, root):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if self.canary in path.read_bytes():
                return True
            if is_zipfile(path):
                with ZipFile(path) as archive:
                    if any(self.canary in archive.read(name) for name in archive.namelist()):
                        return True
        return False

    def _valid_entry(self, entry, source):
        try:
            bundle, restored = Path(entry["bundle"]), Path(entry["restored"])
            content = (bundle / "manifest.json").read_bytes()
            manifest = json.loads(content)
            database = bundle / manifest["database"]["path"]
            if not database.is_file():
                return False
            if entry["status"] != "verified" or manifest["status"] != "complete":
                return False
            if read_database(database) != read_database(source):
                return False
            if hashlib.sha256(database.read_bytes()).hexdigest() != manifest["database"]["sha256"]:
                return False
            if (restored / "manifest.json").read_bytes() != content or (restored / "telemetry.sqlite3").read_bytes() != database.read_bytes():
                return False
            for reference in manifest["artifacts"]:
                if reference["availability"] != "captured":
                    continue
                digest = reference["digest"].removeprefix("sha256:")
                relative = Path("artifacts/sha256") / digest[:2] / digest
                data = (bundle / relative).read_bytes()
                if hashlib.sha256(data).hexdigest() != digest or len(data) != reference["size_bytes"]:
                    return False
                if (restored / relative).read_bytes() != data:
                    return False
            return True
        except (OSError, KeyError, ValueError):
            return False

    def collect(self, case):
        if case in {"dataset_id", "collector_id", "project_id"}:
            descriptor = json.loads(self.primary.read_text())
            descriptor[case] = "different-declared-" + case
            write_json(self.primary, descriptor)
        attempted = []
        restored = {"completed": False, "fault": False}
        create, restore = telemetry_snapshot.create_snapshot, telemetry_snapshot.restore_bundle

        def snapshot(database, artifacts, destination, **options):
            if Path(database) == self.native.database:
                attempted.append(Path(destination))
            return create(database, artifacts, destination, **options)

        def recover(bundle, destination, **options):
            result = restore(bundle, destination, **options)
            if Path(bundle) in attempted:
                attempted.append(Path(destination))
                restored["completed"] = True
                if case == "restored-identity":
                    result = deepcopy(result)
                    result["manifest"]["bundle_id"] = "controlled-restored-identity-mismatch"
                    restored["fault"] = True
            return result

        with patch.object(telemetry_snapshot, "create_snapshot", side_effect=snapshot), \
                patch.object(telemetry_snapshot, "restore_bundle", side_effect=recover):
            receipt, _, proof_unchanged = self.native.collect()
        telemetry = receipt["telemetry"]
        output = Path(receipt["archive"]).parent
        primary = next(row for row in telemetry["entries"] if row["source"] == str(self.primary))
        other = next(row for row in telemetry["entries"] if row["source"] == str(self.other["descriptor"]))
        return {"status": telemetry["status"], "primary_status": primary["status"],
            "native_acceptance": telemetry["native_acceptance"], "proof_unchanged": proof_unchanged,
            "primary_gap": any(row.get("source") == str(self.primary) for row in telemetry["gaps"]),
            "source_unchanged": [hashes(source) for source in self.source_roots] == self.sources_before,
            "prior_export_unchanged": hashes(self.prior) == self.prior_before,
            "other_source_valid": self._valid_entry(other, self.other["database"]),
            "primary_valid": self._valid_entry(primary, self.native.database),
            "canary_control_present": self.canary_control_present,
            "attempt_contains_canary": self._contains_canary(output),
            "restore_completed": restored["completed"], "restore_fault_applied": restored["fault"],
            "rejected_databases_absent": all(not any((path / name).exists() for name in
                ("telemetry.sqlite3", "telemetry.sqlite3-wal", "telemetry.sqlite3-shm", "telemetry.sqlite3-journal")) for path in attempted),
            "rejected_artifacts_absent": all(not any(item.is_file() for item in (path / "artifacts").rglob("*")) for path in attempted),
            "rejected_markers_not_complete": all(not (path / "manifest.json").exists()
                or json.loads((path / "manifest.json").read_text()).get("status") != "complete" for path in attempted)}
