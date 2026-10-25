"""Shared native boundaries arrangements without test methods."""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from support import fixture_environment, initialize

from devtools.api import API
from devtools.native import Native
from devtools.store import Store

DELEGATION = ("Agent", "Task", "task", "spawn_agent", "spawn_subagent", "send_input", "wait_agent",
              "close_agent", "resume_agent", "update_goal", "workflow", "unknown_delegate")


class NativeBoundaryFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.directory = self.root / "state"
        initialize(self.directory, self.workspace, ["owner:external", "worker:external"])
        self.store = Store(self.directory)
        operator = API(self.directory, "owner", operator=True)
        order = operator.invoke("work_order_create", {"title": "Native boundary", "objective": "Bound work",
                                                       "done_when": "Verified"})["work_order"]["id"]
        operator.invoke("member_bind", {"member": "owner", "role": "co", "work_order": order})
        operator.invoke("member_bind", {"member": "worker", "role": "coder", "work_order": order,
                                        "reports_to": "owner"})
        self.generation = self.store.begin_session("worker", os.getpid())
        environment = patch.dict(os.environ, fixture_environment(self.directory, {
            "HOME": str(self.home), "CODEX_HOME": str(self.home / "codex"), "GROK_HOME": str(self.home / "grok"),
        }), clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def native(self, harness):
        config = {"workspace": str(self.workspace), "harness": harness, "binary": "fixture"}
        native = Native(self.directory, "worker", config, self.generation)
        self.addCleanup(native.trace.close)
        native.session = "existing-session"
        native.mcp, native.serena = Mock(request=AsyncMock()), Mock(request=AsyncMock())
        return native

    def effects(self):
        with self.store.connect() as db:
            rows = {table: [tuple(row) for row in db.execute("SELECT * FROM " + table)]
                    for table in ("members", "member_bindings", "role_assignments", "messages", "work_orders",
                                  "jobs", "operations", "job_workspaces", "order_workspaces")}
        return rows, sorted(str(path.relative_to(self.workspace)) for path in self.workspace.rglob("*"))

    def permission(self, native, tool, arguments=None, namespace="grok_build", options=None):
        return asyncio.run(native.request("session/request_permission", {"sessionId": native.session,
            "toolCall": {"_meta": {"x.ai/tool": {"namespace": namespace, "name": tool}},
                         "rawInput": {} if arguments is None else arguments},
            "options": options if options is not None else [
                {"kind": "allow_once", "optionId": "allow"}, {"kind": "reject_once", "optionId": "reject"}]}))
