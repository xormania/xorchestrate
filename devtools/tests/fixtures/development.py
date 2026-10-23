"""Shared development arrangements without test methods."""

import json
import sys

from devtools.api import API
from fixtures.workspaces import WorkspaceFixture


class DevelopmentFixture:
    setUp = WorkspaceFixture.setUp
    prepared = WorkspaceFixture.prepared

    def configure(self, checks=None):
        root = self.prepared()
        (root / "composer.json").write_text("{}")
        (root / "composer.lock").write_text("{}")
        (root / "vendor").mkdir(exist_ok=True)
        (root / "vendor/autoload.php").write_text("<?php")
        import hashlib

        (root / "vendor/.agentscient-dependencies").write_text(hashlib.sha256(b"{}{}").hexdigest())
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["development"] = {
            "checks": checks or {"fixture": [sys.executable, "-c", "print('measured')"]}
        }
        (self.state / "team.json").write_text(json.dumps(config))
        self.api = API(self.state, "coder")
        return root
