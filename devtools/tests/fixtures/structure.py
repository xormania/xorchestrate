"""Shared structure arrangements without test methods."""

import tempfile
from pathlib import Path

from support import initialize

from devtools.api import API
from devtools.store import Store
from fixtures.commands import GOAL


def job(name, **extra):
    return dict(name=name, objective="Deliver " + name, done_when="Evidence demonstrates " + name,
                constraints="Stay in the assigned scope", **extra)


class StructureFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "source"
        self.workspace.mkdir()
        self.state = self.root / "state"
        initialize(self.state, self.workspace, [name + ":external" for name in ("co", "xo", *[f"slot{i}" for i in range(9)])], co="co")
        self.co = API(self.state, "co")
        self.co.call("direct", GOAL)
        self.xo = API(self.state, "xo")
        self.store = Store(self.state)

    def member(self, role, work=None, assignment=None):
        with self.store.connect() as db:
            rows = list(db.execute("SELECT b.*,j.title,a.name FROM member_bindings b LEFT JOIN jobs j ON j.id=b.job "
                                   "LEFT JOIN role_assignments a ON a.id=b.assignment_id WHERE b.role=?", (role,)))
        selected = [row for row in rows if (not work or row["title"] == work) and (not assignment or row["name"] == assignment)]
        self.assertEqual(1, len(selected))
        return API(self.state, selected[0]["member"])

    def assign(self, coordinator, role, work, name=None, **extra):
        coordinator.call("orchestrate", {"work": [{"name": name or role, "job": work, "role": role, **extra}]})
        return self.member(role, work, None if role == "nco" else name or role)

    def complete(self, coordinator, specialist, work, name, text="Result supported by evidence"):
        result = specialist.call("work_report", {"kind": "result", "text": text,
                                 "evidence": {"name": name + ".md", "content": text}})
        coordinator.call("steer", {"action": "complete", "work": work, "assignment": name, "direction": "Contribution inspected"})
        return result
