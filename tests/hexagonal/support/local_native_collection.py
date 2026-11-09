"""Actual local corpus and existing managed collection, without vendor claims."""

import hashlib
import inspect
import json
import os
import sqlite3
from pathlib import Path
from zipfile import ZipFile

from scripts.testkit.adapters.evidence import write_json
from tests.hexagonal.support.managed_profiles import ROOT, run_shell
from tests.hexagonal.support.managed_storage import owned_store
from tests.hexagonal.support.telemetry import Fixture


class NativeCollection:
    def __init__(self, root, case):
        self.root, self.case = root, case
        self.store = owned_store(root / "managed runs", {"suite": "stories", "mode": "native",
            "drivers": ["codex"], "root": str(ROOT), "interpreter": os.sys.executable})
        self.directory = self.store.directory
        self.runtime = self.directory / "checks" / "controlled runtime"
        self.runtime.mkdir(parents=True)
        self.safe = "ALLOWED-CORPUS-CONTENT-" + "richλ" * 200
        self.private = "excluded-private-input-marker"
        private_input = self.directory / "inputs" / "operator-config.json"
        write_json(private_input, {"api_key": self.private})
        self.proof_before = self.store.read_state()["proof"]
        self.source_files = {}
        self.descriptor = self.runtime / "telemetry-runtime.json"
        if case == "disabled":
            write_json(self.descriptor, {"schema_version": 1, "status": "disabled",
                "reason": "operator explicitly disabled capture", "content_policy": "protected-local",
                "source_completion": "unknown"})
            return
        corpus = (root / "external shared corpus") if case == "external" else self.runtime / "telemetry"
        fixture = Fixture(self.runtime, local_options={"runtime_root": str(corpus), "artifact_threshold_bytes": 64})
        try:
            work = fixture.create_work("Controlled local collection; no vendor execution claim")
            fixture.record_result(work, {"body": self.safe, "empty": "", "zero": 0, "api_key": self.private})
            sink = fixture.app.telemetry
            self.database = sink.path
            self.artifacts = sink.artifact_root
            self.event_count = len(sink.scan(limit=1000)["events"])
            references = [ref for row in sink.scan(limit=1000)["events"] for ref in row.get("artifacts", [])
                if ref["availability"] == "captured"]
            assert references, "collection control must have actual artifact-backed events"
            ref = references[0]
            digest = ref["digest"].removeprefix("sha256:")
            self.first_artifact = self.artifacts / "sha256" / digest[:2] / digest
            descriptor = {"schema_version": 1, "status": "configured", "runtime_root": str(corpus),
                "database": str(self.database), "artifacts": str(self.artifacts), "project_id": sink.project_id,
                "dataset_id": sink.dataset_id, "collector_id": sink.collector_id,
                "content_policy": "protected-local", "source_completion": "unknown"}
        finally:
            fixture.close()
        if case == "lineage":
            descriptor["dataset_id"] = "a-different-dataset"
        write_json(self.descriptor, descriptor)
        if case == "missing":
            self.database.unlink()
        if case == "corrupt":
            data = self.first_artifact.read_bytes()
            self.first_artifact.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        for source in (self.database, self.first_artifact):
            if source.exists():
                self.source_files[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()

    def close(self):
        self.store.release_owner()

    def collect(self):
        options = {}
        if "supplemental" in inspect.signature(self.store.collect).parameters:
            from scripts.testkit.adapters.native_evidence import collection_supplement
            options["supplemental"] = collection_supplement
        result = self.store.collect(**options)
        unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
            for path, value in self.source_files.items())
        return result, unchanged, self.store.read_state()["proof"] == self.proof_before

    def independent_bundle(self, entry, telemetry):
        bundle, restored = Path(entry["bundle"]), Path(entry["restored"])
        manifest_bytes = (bundle / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        assert manifest["status"] == manifest["completeness"] == "complete"
        assert manifest["source"]["event_count"] == self.event_count == entry["event_count"]
        database = bundle / "telemetry.sqlite3"
        assert hashlib.sha256(database.read_bytes()).hexdigest() == manifest["database"]["sha256"]
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == self.event_count
        contents = []
        for ref in manifest["artifacts"]:
            if ref["availability"] != "captured":
                continue
            digest = ref["digest"].removeprefix("sha256:")
            data = (bundle / "artifacts" / "sha256" / digest[:2] / digest).read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest
            assert len(data) == ref["size_bytes"]
            contents.append(data)
        assert self.safe.encode() in b"".join(contents)
        assert self.private.encode() not in b"".join(contents)
        assert (restored / "manifest.json").read_bytes() == manifest_bytes
        assert (restored / "telemetry.sqlite3").read_bytes() == database.read_bytes()
        assert not any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
        with ZipFile(telemetry["archive"]) as archive:
            names = archive.namelist()
            assert "index.json" in names
            prefixes = [name[:-len("manifest.json")] for name in names if name.endswith("/manifest.json")]
            assert len(prefixes) == 1
            for path in bundle.rglob("*"):
                if path.is_file():
                    assert archive.read(prefixes[0] + path.relative_to(bundle).as_posix()) == path.read_bytes()
            assert not any(name.startswith("/") or ".." in Path(name).parts for name in names)
            assert self.private.encode() not in b"".join(archive.read(name) for name in names)
        assert Path(telemetry["archive"]).stat().st_mode & 0o077 == 0
        assert Path(telemetry["index"]).stat().st_mode & 0o077 == 0
        return True

    def public_verify_then_corrupt(self, entry):
        self.store.release_owner()
        script = ROOT / "scripts" / "proof.sh"
        result = run_shell(script, ["verify", str(self.directory)], cache=self.root / "verify cache")
        bundle = Path(entry["bundle"])
        manifest = json.loads((bundle / "manifest.json").read_text())
        reference = next(ref for ref in manifest["artifacts"] if ref["availability"] == "captured")
        digest = reference["digest"].removeprefix("sha256:")
        artifact = bundle / "artifacts" / "sha256" / digest[:2] / digest
        artifact.write_bytes(b"intentionally-corrupt-bundle-object")
        rejected = run_shell(script, ["verify", str(self.directory)], cache=self.root / "verify cache")
        return result.returncode, result.stdout, rejected.returncode, rejected.stdout + rejected.stderr

    def verify_omitted_population(self, mutation):
        if mutation == "subset":
            runtime = self.directory / "checks" / "second controlled runtime"
            runtime.mkdir()
            fixture = Fixture(runtime, local_options={"runtime_root": str(runtime / "telemetry"),
                "artifact_threshold_bytes": 64, "dataset_id": "second-dataset", "collector_id": "second-collector"})
            try:
                work = fixture.create_work("Second independent local corpus")
                fixture.record_result(work, {"body": self.safe + "second"})
                sink = fixture.app.telemetry
                descriptor = {"schema_version": 1, "status": "configured", "runtime_root": str(sink.root),
                    "database": str(sink.path), "artifacts": str(sink.artifact_root), "project_id": sink.project_id,
                    "dataset_id": sink.dataset_id, "collector_id": sink.collector_id,
                    "content_policy": "protected-local", "source_completion": "unknown"}
            finally:
                fixture.close()
            write_json(runtime / "telemetry-runtime.json", descriptor)
        receipt, _, _ = self.collect()
        telemetry = receipt.get("telemetry")
        assert isinstance(telemetry, dict) and telemetry.get("status") == "verified", \
            "POPULATION_CONTROL_MUST_BEGIN_WITH_A_REAL_VERIFIED_COLLECTION"
        self.store.release_owner()
        script = ROOT / "scripts" / "proof.sh"
        before = run_shell(script, ["verify", str(self.directory)], cache=self.root / "population cache")
        index = Path(telemetry["index"])
        value = json.loads(index.read_text())
        population = len(value["entries"])
        archive = Path(telemetry["archive"])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        value["entries"] = [] if mutation == "empty" else value["entries"][:-1]
        index.write_text(json.dumps(value))
        after = run_shell(script, ["verify", str(self.directory)], cache=self.root / "population cache")
        return before.returncode, after.returncode, population, hashlib.sha256(archive.read_bytes()).hexdigest() == digest

