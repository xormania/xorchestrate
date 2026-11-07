"""Independent preservation behavior through public and adapter boundaries."""

import tempfile
import unittest
from pathlib import Path

from tests.hexagonal.contracts.telemetry_preservation import SnapshotContract
from tests.hexagonal.support import telemetry_preservation as support
from tests.hexagonal.support.application import application


class TelemetryPreservationSurfaceTests(unittest.TestCase):
    def test_public_snapshot_and_verification_operations_are_discoverable(self):
        app = application()
        actions = {capability.name for capability in app.describe()}
        self.assertTrue(
            {"telemetry_snapshot", "telemetry_verify"} <= actions,
            "ordinary CLI/MCP discovery must expose telemetry snapshot and verification",
        )


class TelemetrySnapshotTests(SnapshotContract, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xor-telemetry-preservation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset = support.Dataset(self.root / "live")
        self.addCleanup(self.dataset.close)
        self.bundle = self.root / "bundle"
        self.restored = self.root / "restored"
        self.snapshot = support.snapshot_api()

    read_rows = staticmethod(support.read_database)
    digest = staticmethod(support.digest)
    path = staticmethod(Path)
    unavailable_reference = staticmethod(support.unavailable_reference)
    reference_dict = staticmethod(support.reference_dict)
    write_manifest = staticmethod(support.write_manifest)
    revoke_after_bundle_verification = staticmethod(support.revoke_after_bundle_verification)

    @staticmethod
    def read_artifact(root, reference):
        return support.FileArtifactStore(root).read(reference)

    def assert_no_complete_manifest(self, destination):
        path = destination / "manifest.json"
        if path.exists():
            self.assertNotEqual(support.read_manifest(destination).get("status"), "complete")


if __name__ == "__main__":
    unittest.main()
