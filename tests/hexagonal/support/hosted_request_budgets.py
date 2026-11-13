"""Hosted attachment through the real bridge driver and independent phase costs."""

from collections import deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.hexagonal.support.socket_request_budgets import ScriptedSocket
from tests.hexagonal.support.temporal_waits import ControlledClock
from xorchestrate.adapters import bridge, hosted
from xorchestrate.model import OrchestrationError
from xorchestrate.ports import HostedTerminal, TerminalClosure


class BudgetTerminalHost:
    name = "budget"

    def __init__(self):
        self.items = {}
        self.closed = []

    def inventory(self):
        return list(self.items.values())

    terminals = inventory

    def open_terminal(self, name, command, **_):
        Path(command.cwd, "state.json").write_text("{}")
        terminal = HostedTerminal(name, True, None, None, {"name": name})
        self.items[name] = terminal
        return terminal

    def adopt(self, receipt):
        terminal = HostedTerminal(receipt["name"], True, None, None, dict(receipt))
        self.items[terminal.name] = terminal
        return terminal

    def close_terminal(self, name):
        self.closed.append(name)
        return TerminalClosure(self.items.pop(name), True)


@contextmanager
def hosted_budget_subject(root, *, connect=0, attach=0, activate=0, attach_errors=()):
    clock = ControlledClock()
    host = BudgetTerminalHost()
    calls, connections, peers = [], [], []
    costs = {"attach": attach, "activate": activate}
    errors = deque(attach_errors)

    def connect_socket(*_args, **_kwargs):
        connection = ScriptedSocket(clock, connect=connect)
        connections.append(connection)
        return connection

    class BudgetPeer:
        def __init__(self, connection, handler, options):
            self.connection, self.options = connection, options
            self.closed = False
            peers.append(self)

        def request(self, method, params, *, timeout=None):
            allowance = self.options.get("timeout", 30) if timeout is None else timeout
            calls.append((method, params, allowance))
            cost = costs[method]
            clock.advance(elapsed=min(cost, max(0, allowance)), wall=-100)
            if cost > allowance:
                raise OrchestrationError("bridge phase exhausted its allowance", "bridge_timeout", {"outcome": "unknown"})
            if method == "attach":
                if errors:
                    raise errors.popleft()
                return {"native_id": "native-budget", "incarnation": "current-incarnation",
                        "host_id": "host-budget", "ownership": "external"}
            return {"active": True}

        def close(self):
            self.closed = True
            self.connection.close()

    options = {"session": {"adapter": "fixture", "options": {}}, "cwd": str(root),
               "directory": str(root / "sessions"), "attach_timeout": 7, "timeout": 30}
    driver = hosted.HostedSessionDriver(options)
    driver.bind_terminal_host(host, endpoints=str(root / "e"), entry="budget")

    def invoke(method):
        specification = {"context": {"session_id": "original-session"}, "goal": "original-goal"}
        if method in {"start", "attach"}:
            return getattr(driver, method)(specification)
        key = "0123456789ab"
        directory, endpoint = driver._paths(key)
        directory.mkdir(parents=True)
        (directory / "state.json").write_text("{}")
        source = {"handle": "previous-handle", "native_id": "native-budget", "incarnation": "previous-incarnation",
                  "host_id": "host-budget", "ownership": "external", "bridge_endpoint": str(endpoint),
                  "bridge_transport": "unix", "hosted": {"key": key, "terminal": {"name": "budget-" + key},
                  "native_specification": {"goal": "original-goal"}}}
        return getattr(driver, method)(source, specification)

    with patch.object(hosted, "time", clock), patch.object(bridge, "time", clock, create=True), \
            patch.object(bridge.socket, "socket", connect_socket), patch.object(bridge, "Peer", BudgetPeer):
        try:
            yield SimpleNamespace(invoke=invoke, clock=clock, calls=calls, connections=connections,
                                  peers=peers, host=host, driver=driver)
        finally:
            driver.close()
