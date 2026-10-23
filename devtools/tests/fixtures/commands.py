"""Shared commands arrangements without test methods."""

import tempfile
from pathlib import Path

from support import Client, adopt_plan, initialize

from devtools.api import API
from devtools.store import Store


def responsibility(name="Implementation", role="coder"):
    return dict(
        name=name,
        role=role,
        objective="Use the existing service",
        done_when="Evidence retained",
        constraints="Preserve unrelated changes",
    )


GOAL = dict(
    title="Prepare local development",
    objective="Ready the Symfony starting point",
    done_when="Local workflow demonstrated",
    constraints="Keep product implementation out of scope",
)


class CommandFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.state = self.root / "state"
        initialize(
            self.state,
            self.workspace,
            [name + ":external" for name in ("owner", "xo", "coder", "audit", "spare")],
            co="owner",
        )
        self.store = Store(self.state)
        self.co = API(self.state, "owner")

    def client(self, member, **kwargs):
        client = Client(self.state, member, **kwargs)
        self.addCleanup(client.close)
        return client

    def team(self, work=None):
        self.co.call("direct", GOAL)
        self.xo = API(self.state, "xo")
        work = work or [responsibility()]
        name = work[0]["name"]
        adopt_plan(self.xo, [{"name": name, **{key: work[0][key] for key in ("objective", "done_when", "constraints")}}])
        self.xo.call("orchestrate", {"work": [dict(item, job=name) for item in work]})
        return API(self.state, "coder")
