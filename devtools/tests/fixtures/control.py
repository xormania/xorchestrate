"""Shared control arrangements without test methods."""

import tempfile
from pathlib import Path

from support import Client, initialize

from devtools.api import API
from devtools.store import Store


class ControlFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        self.state = root / "state"
        initialize(self.state, workspace, [name + ":external" for name in ("owner", "xo", "coder", "audit", "idle")])
        self.store = Store(self.state)
        # Arrange trusted state through the real API. Tests open a stdio Client
        # explicitly when the wire protocol is part of the behavior under test.
        operator = API(self.state, "owner", operator=True)

        def invoke(capability, **arguments):
            return operator.call("capability_invoke", {"capability": capability, "arguments": arguments})

        self.order = invoke(
            "work_order_create", title="One", objective="Build the feature", done_when="Works"
        )["work_order"]["id"]
        self.job = invoke(
            "job_create", title="Feature", objective="Implement contract", done_when="Checks pass"
        )["job"]["id"]
        self.other = invoke(
            "job_create", title="Unrelated", objective="SECRET SIBLING", done_when="Separate"
        )["job"]["id"]
        for member, role, parent, job in (
            ("owner", "co", None, None),
            ("xo", "xo", "owner", None),
            ("coder", "coder", "xo", self.job),
            ("audit", "audit", "xo", self.other),
        ):
            args = dict(member=member, role=role, work_order=self.order, brief="Use established contracts")
            if parent:
                args["reports_to"] = parent
            if job:
                args["job"] = job
            invoke("member_bind", **args)

    def client(self, member, **args):
        client = Client(self.state, member, **args)
        self.addCleanup(client.close)
        return client
