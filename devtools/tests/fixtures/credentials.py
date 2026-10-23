"""Shared credentials arrangements without test methods."""

import tempfile
from pathlib import Path


class CredentialFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "typesafe.key"
        self.path.write_text("private-file-key-value\n")
        self.path.chmod(0o600)
        self.reference = {"file": str(self.path)}
