"""Optional publication of real verified bundles through an in-memory destination."""

import tempfile
import unittest
from pathlib import Path

from tests.hexagonal.contracts.telemetry_publication import BundlePublicationContract
from tests.hexagonal.support import telemetry_publication as support


class TelemetryBundlePublicationTests(BundlePublicationContract, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xor-telemetry-publication-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshot = support.snapshot_api()
        self.lineage = dict(support.LINEAGE)
        self.bundle, self.manifest = self.create_bundle("bundle")

    publisher = staticmethod(support.MemoryPublisher)
    tamper = staticmethod(support.tamper)

    def create_bundle(self, name):
        return support.verified_bundle(self.root / name)


if __name__ == "__main__":
    unittest.main()
