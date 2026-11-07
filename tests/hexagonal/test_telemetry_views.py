"""Wire pure projection/export contracts to active telemetry adapters."""

import tempfile
import unittest
from pathlib import Path

from tests.hexagonal.contracts.telemetry_views import (
    ObservationSignalsContract,
    OperationalExportContract,
    TelemetryViewsContract,
)
from tests.hexagonal.support import telemetry_preservation as support


class TelemetryViewTests(TelemetryViewsContract, unittest.TestCase):
    def setUp(self):
        self.views = support.view_api()

    observation = staticmethod(support.observation)


class TelemetryOperationalExportTests(OperationalExportContract, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xor-telemetry-export-")
        self.addCleanup(temporary.cleanup)
        self.dataset = support.Dataset(Path(temporary.name) / "live")
        self.addCleanup(self.dataset.close)
        self.exports = support.export_api()

    observation = staticmethod(support.observation)
    sink = staticmethod(support.Sink)


class TelemetryObservationSignalsTests(ObservationSignalsContract, unittest.TestCase):
    def setUp(self):
        self.exports = support.export_api()

    sink = staticmethod(support.Sink)
    clock = staticmethod(support.Clock)


if __name__ == "__main__":
    unittest.main()
