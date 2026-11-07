"""Preservation, analysis and operational-export behavioral expectations."""

import json
from copy import deepcopy


class SnapshotContract:
    def test_snapshot_retains_uncheckpointed_rows_and_captured_artifact_closure(self):
        event, receipt = self.dataset.append("first", {"answer": "✓"}, content=b"native output\n")
        orphan = self.dataset.artifacts.put(b"unrelated private content")
        original = deepcopy(self.dataset.rows())
        self.assertTrue(self.dataset.database.with_name("live.sqlite3-wal").exists())
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["format"], "xorchestrate-telemetry-bundle-v1")
        self.assertEqual(manifest["source"]["cutoff"], receipt.sequence)
        self.assertEqual(manifest["source"]["event_count"], 1)
        self.assertEqual(manifest["source"]["dataset_id"], "fixture-dataset")
        self.assertEqual(manifest["source"]["collector_id"], "fixture-collector")
        self.assertEqual(manifest["source"]["cursors"], {"fixture-stream": "first"})
        self.assertEqual({ref["digest"] for ref in manifest["artifacts"]}, {event.artifacts[0].digest})
        self.assertNotIn(orphan.digest, json.dumps(manifest))
        self.assertTrue(self.dataset.object_path(orphan).exists(), "snapshot must not collect or delete unrelated objects")
        self.assertEqual(self.read_rows(self.bundle / manifest["database"]["path"])[0]["event_id"], "first")
        self.assertEqual(self.digest((self.bundle / manifest["database"]["path"]).read_bytes()), manifest["database"]["sha256"])
        self.assertEqual(self.dataset.rows(), original)
        self.assertEqual(self.dataset.repository.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertFalse(list(self.bundle.rglob("*-wal")))
        self.assertFalse(list(self.bundle.rglob("*-shm")))
        self.assertEqual(self.snapshot.verify_bundle(self.bundle), manifest)

    def test_snapshot_cutoff_and_closure_come_from_completed_backup(self):
        self.dataset.append("early", content=b"early")
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle,
                                                 backup=self.dataset.backup_after_late_writer)
        rows = self.read_rows(self.bundle / manifest["database"]["path"])
        self.assertEqual([row["event_id"] for row in rows], ["early", "late"])
        self.assertEqual(manifest["source"]["event_count"], len(rows))
        self.assertEqual(manifest["source"]["cutoff"], max(row["sequence"] for row in rows))
        expected = {ref["digest"] for row in rows for ref in row["observation"]["artifacts"]}
        self.assertEqual({ref["digest"] for ref in manifest["artifacts"]}, expected)
        self.assertEqual(manifest["source"]["cursors"], {"fixture-stream": "late"})

    def test_fresh_restore_is_readable_without_original_runtime_or_sidecars(self):
        event, _ = self.dataset.append("portable", {"source": "original"}, content=b"portable bytes")
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        self.dataset.close()
        restored = self.snapshot.restore_bundle(self.bundle, self.restored)
        self.assertEqual(restored["manifest"], manifest)
        self.assertTrue(self.restored in self.path(restored["database_path"]).parents)
        self.assertTrue(self.restored in self.path(restored["artifact_root"]).parents or self.path(restored["artifact_root"]) == self.restored)
        rows = self.read_rows(restored["database_path"])
        self.assertEqual([row["event_id"] for row in rows], ["portable"])
        self.assertEqual(self.read_artifact(restored["artifact_root"], event.artifacts[0]), b"portable bytes")
        self.assertFalse(list(self.restored.rglob("*-wal")))
        self.assertFalse(list(self.restored.rglob("*-shm")))

    def test_missing_captured_artifact_refuses_complete_snapshot(self):
        event, _ = self.dataset.append("missing", content=b"do not lose this")
        self.dataset.object_path(event.artifacts[0]).unlink()
        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        self.assert_no_complete_manifest(self.bundle)

    def test_corrupt_captured_artifact_refuses_complete_snapshot(self):
        event, _ = self.dataset.append("corrupt", content=b"correct bytes")
        self.dataset.object_path(event.artifacts[0]).write_bytes(b"changed bytes")
        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        self.assert_no_complete_manifest(self.bundle)

    def test_unavailable_reference_survives_as_explicit_partial_coverage(self):
        reference = self.unavailable_reference()
        self.dataset.append("external", refs=(reference,))
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        self.assertEqual(manifest["status"], "complete", "the bundle operation can complete despite declared source gaps")
        self.assertEqual(manifest["completeness"], "partial")
        references = manifest["artifacts"] + manifest.get("unavailable_artifacts", [])
        expected = self.reference_dict(reference)
        self.assertTrue(any(all(item.get(key) == value for key, value in expected.items()) for item in references))
        self.assertEqual(self.snapshot.verify_bundle(self.bundle)["completeness"], "partial")

    def test_interrupted_backup_never_leaves_a_complete_manifest(self):
        self.dataset.append("safe", content=b"safe")

        def interrupted(source, target):
            target.execute("CREATE TABLE interrupted_capture(value)")
            target.commit()
            raise RuntimeError("controlled backup interruption")

        with self.assertRaises((RuntimeError, self.snapshot.SnapshotError)):
            self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle, backup=interrupted)
        self.assert_no_complete_manifest(self.bundle)
        with self.assertRaises((OSError, self.snapshot.SnapshotError)):
            self.snapshot.verify_bundle(self.bundle)
        self.assertEqual([row["event_id"] for row in self.dataset.rows()], ["safe"])

    def test_export_authorizes_each_observation_before_disclosing_closure(self):
        self.dataset.append("public", content=b"public", work_id="work-public")
        self.dataset.append("private", content=b"private", work_id="work-private")
        checked = []

        def authorize(observation):
            checked.append(observation["event_id"])
            return observation["context"]["task_id"] == "work-public"

        with self.assertRaises(PermissionError):
            self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle, authorize=authorize)
        self.assertIn("private", checked)
        self.assert_no_complete_manifest(self.bundle)
        self.assertFalse(list(self.bundle.rglob("*.sqlite3")), "denied export cannot leave disclosed database bytes")

    def test_configured_backup_deadline_aborts_progress_without_losing_source(self):
        self.dataset.append("large", {"text": "x" * 2_000_000})
        calls = []

        def clock():
            calls.append(None)
            return len(calls) / 10

        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle,
                                          backup_timeout=0.15, clock=clock)
        self.assertGreaterEqual(len(calls), 2, "the configured deadline must be checked during real backup progress")
        self.assert_no_complete_manifest(self.bundle)
        self.assertEqual([row["event_id"] for row in self.dataset.rows()], ["large"])

    def test_verify_rejects_changed_database_bytes(self):
        self.dataset.append("original")
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        database = self.bundle / manifest["database"]["path"]
        with database.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.verify_bundle(self.bundle)

    def test_verify_rejects_manifest_count_inconsistent_with_snapshot(self):
        self.dataset.append("original")
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        manifest["source"]["event_count"] += 1
        self.write_manifest(self.bundle, manifest)
        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.verify_bundle(self.bundle)

    def test_verify_checks_database_references_against_manifest_closure(self):
        self.dataset.append("original", content=b"retained reference")
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        manifest["artifacts"] = []
        self.write_manifest(self.bundle, manifest)
        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.verify_bundle(self.bundle)

    def test_verify_rejects_paths_that_escape_bundle(self):
        self.dataset.append("original")
        manifest = self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        manifest["database"]["path"] = "../live/live.sqlite3"
        self.write_manifest(self.bundle, manifest)
        with self.assertRaises(self.snapshot.SnapshotError):
            self.snapshot.verify_bundle(self.bundle)

    def test_restore_never_replaces_an_existing_destination(self):
        self.dataset.append("original")
        self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        self.restored.mkdir()
        sentinel = self.restored / "unrelated.txt"
        sentinel.write_text("preserve unrelated work")
        with self.assertRaises((FileExistsError, self.snapshot.SnapshotError)):
            self.snapshot.restore_bundle(self.bundle, self.restored)
        self.assertEqual(sentinel.read_text(), "preserve unrelated work")

    def test_verifying_a_bundle_authorizes_each_event_before_returning_metadata(self):
        self.dataset.append("private", {"body": "protected body"}, content=b"protected artifact", work_id="work-private")
        self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        checked = []

        def authorize(event):
            checked.append(event["event_id"])
            return False

        with self.assertRaises(PermissionError):
            self.snapshot.verify_bundle(self.bundle, authorize=authorize)
        self.assertEqual(checked, ["private"])

    def test_restore_denial_does_not_install_database_or_artifacts(self):
        self.dataset.append("private", content=b"protected artifact", work_id="work-private")
        self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        with self.assertRaises(PermissionError):
            self.snapshot.restore_bundle(self.bundle, self.restored, authorize=lambda _: False)
        self.assertFalse(list(self.restored.rglob("*.sqlite3")))
        self.assertFalse(list(self.restored.rglob("sha256")))

    def test_verification_rechecks_authorization_after_reading_the_closure(self):
        self.dataset.append("private", content=b"protected artifact", work_id="work-private")
        self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle)
        checked = []

        def authorize(event):
            checked.append(event["event_id"])
            return len(checked) == 1

        with self.assertRaises(PermissionError):
            self.snapshot.verify_bundle(self.bundle, authorize=authorize)
        self.assertGreaterEqual(len(checked), 2)

    def test_snapshot_final_readback_revocation_cannot_leave_a_completed_bundle(self):
        self.dataset.append("private", content=b"protected artifact", work_id="work-private")
        allowed = [True]

        def authorize(_):
            return allowed[0]

        with self.revoke_after_bundle_verification(self.bundle, allowed):
            with self.assertRaises(PermissionError):
                self.snapshot.create_snapshot(self.dataset.database, self.dataset.artifacts, self.bundle,
                                              authorize=authorize)
        self.assertFalse(allowed[0], "the fault must revoke permission during the actual final bundle readback")
        self.assert_no_complete_manifest(self.bundle)
        self.assertFalse(list(self.bundle.rglob("*.sqlite3")), "revoked export cannot leave disclosed database bytes")
