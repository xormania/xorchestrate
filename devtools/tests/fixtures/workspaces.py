"""Shared workspaces arrangements without test methods."""

import json
import subprocess
import tempfile
from pathlib import Path

from support import initialize

from devtools.api import API
from devtools.store import Store
from devtools.workspaces import git


class WorkspaceFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        subprocess.run(["git", "init", "-b", "dev", str(self.checkout)], capture_output=True, check=True)
        git(self.checkout, "config", "user.name", "Fixture")
        git(self.checkout, "config", "user.email", "fixture@example.invalid")
        (self.checkout / "src").mkdir()
        (self.checkout / "src/Example.php").write_text("original\n")
        (self.checkout / "README.md").write_text("Example\n")
        (self.checkout / "devtools").mkdir()
        (self.checkout / "devtools/protected.py").write_text("protected\n")
        git(self.checkout, "add", ".")
        git(self.checkout, "commit", "-m", "Initial")
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], capture_output=True, check=True)
        git(self.checkout, "remote", "add", "origin", str(self.remote))
        git(self.checkout, "push", "origin", "dev")
        self.state = self.root / "state"
        initialize(
            self.state, self.checkout, ["co:external", "xo:external", "coder:external", "docs:external"], co="co"
        )
        config = json.loads((self.state / "team.json").read_text())
        config["project"] = {**config.get("project", {}), "checkout": str(self.checkout), "repository": "example/project", "target": "dev"}
        (self.state / "team.json").write_text(json.dumps(config))
        API(self.state, "co").call(
            "direct",
            {"title": "Local work", "objective": "Implement", "done_when": "Delivered", "constraints": "Product only"},
        )
        # Workspace tests start at an existing trusted job. Command/plan creation
        # is exercised through normal MCP in test_commands and test_coordination.
        xo = API(self.state, "xo")
        order = xo.control.snapshot()["work_order"]
        job = xo.work.job_create(title="Feature", objective="Feature", done_when="Works", constraints="Product only",
                                 work_order=order)["job"]["id"]
        xo.control.bind("coder", "coder", order, job, "xo")
        self.api = API(self.state, "coder")
        self.store = Store(self.state)

    def prepared(self):
        self.api.call("work_inspect", {})
        return Path(self.api.workspaces.ensure()["root"])
