"""Shared feedback arrangements without test methods."""

import json
from pathlib import Path
from unittest.mock import patch

from support import Client, fixture_work

from devtools.api import API
from fixtures.github import GitHubFixture


class FeedbackFixture:
    base_setup = GitHubFixture.setUp

    def setUp(self):
        self.base_setup()
        config = json.loads((self.directory / "team.json").read_text())
        config["co"] = "alpha"
        config["members"].extend(
            [
                {"name": "producer", "harness": "external", "workspace": str(self.workspace)},
                {"name": "other", "harness": "external", "workspace": str(self.workspace)},
            ]
        )
        config["project"].update(checkout=str(self.workspace), repository="example/project")
        (self.directory / "team.json").write_text(json.dumps(config))
        API(self.directory, "alpha").call(
            "direct",
            {
                "title": "Delivery",
                "objective": "Bounded work",
                "done_when": "Evidence",
                "constraints": "MCP only",
            },
        )
        fixture_work(API(self.directory, "beta"), [
            {"name": "Feature", "role": "coder", "objective": "Feature", "done_when": "Tested", "constraints": "Product"},
            {"name": "Separate", "role": "docs", "objective": "Docs", "done_when": "Documented", "constraints": "Docs"},
        ])
        self.api = API(self.directory, "producer")
        self.job = self.api.control.snapshot()["job"]
        self.order = self.api.control.snapshot()["work_order"]
        self.api.work.job_update(job=self.job, pull_request=self.provider.pr["html_url"])
        self.provider.exists = True

    def client(self, member="producer"):
        with patch("support.ENTRY", (Path(__file__).resolve().parent.parent / "github_fixture.py")):
            client = Client(self.directory, member, env={"FIXTURE_GITHUB_ROOT": self.provider.url})
        self.addCleanup(client.close)
        return client
