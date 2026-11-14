"""Independent elapsed-cost boundaries for provider and bridge operations."""

import json
from concurrent.futures import Future, TimeoutError
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from tests.hexagonal.support.bridge_frames import bridge_frame_subject
from tests.hexagonal.support.temporal_waits import ControlledClock
from xorchestrate.adapters import bridge_transport, capabilities
from xorchestrate.model import OrchestrationError


class MCPBudgetTransport:
    """A transport charges each phase against the allowance actually supplied."""

    def __init__(self, clock, *, initialize=4, initialized=2, listing=1):
        self.clock = clock
        self.costs = {"initialize": initialize, "notifications/initialized": initialized, "tools/list": listing}
        self.calls = []
        self.closed = False

    def _consume(self, method, timeout):
        self.calls.append((method, timeout))
        cost = self.costs[method]
        self.clock.advance(elapsed=min(cost, timeout))
        if cost > timeout:
            raise OrchestrationError("transport exhausted its allowance", "provider_timeout", {"outcome": "unknown"})

    def request(self, method, params, *, timeout):
        self._consume(method, timeout)
        if method == "initialize":
            return {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "budget-witness", "version": "1"}}
        if method == "tools/list":
            return {"tools": [{"name": "read", "description": "Read a value.",
                               "inputSchema": {"type": "object", "properties": {}}}]}
        raise AssertionError(f"unexpected request: {method}")

    def notify(self, method, params=None, *, timeout=10):
        self._consume(method, timeout)

    def poll_notifications(self):
        return []

    def close(self):
        self.closed = True


@contextmanager
def mcp_budget_subject(**costs):
    clock = ControlledClock()
    transport = MCPBudgetTransport(clock, **costs)
    with patch.object(capabilities, "time", clock), patch.object(capabilities, "JsonLineProcess", return_value=transport):
        provider = capabilities.StdioMCPProvider({"command": ["unused-budget-fixture"], "timeout": 10})
        try:
            yield SimpleNamespace(provider=provider, transport=transport, clock=clock)
        finally:
            provider.close()


class AdmissionCost:
    """A writer becomes available after a known elapsed cost, or remains held."""

    def __init__(self, clock, cost):
        self.clock, self.cost = clock, cost
        self.held = False

    def acquire(self, blocking=True, timeout=-1):
        if timeout >= 0 and self.cost > timeout:
            self.clock.advance(elapsed=timeout)
            return False
        self.clock.advance(elapsed=self.cost)
        self.held = True
        return True

    def release(self):
        assert self.held
        self.held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


@contextmanager
def bridge_budget_subject(*, admission=0, writes=(), response=0, timeout=10):
    """Real framed bytes, with deterministic costs at OS readiness boundaries."""
    clock = ControlledClock()
    write_costs = iter(writes)
    response_allowances = []

    class ResponseCost(Future):
        def result(self, timeout=None):
            response_allowances.append(timeout)
            if timeout is not None and response > timeout:
                clock.advance(elapsed=max(0, timeout))
                raise TimeoutError()
            clock.advance(elapsed=response)
            return {"observed": "original response"}

    with bridge_frame_subject(timeout=timeout) as subject:
        original_select = bridge_transport.select.select

        def readiness(readers, writers, errors, allowance=None):
            if writers:
                cost = next(write_costs, 0)
                if allowance is not None and cost > allowance:
                    clock.advance(elapsed=max(0, allowance))
                    return [], [], []
                clock.advance(elapsed=cost)
            return original_select(readers, writers, errors, 0)

        subject.peer._writer = AdmissionCost(clock, admission)
        with patch.object(bridge_transport, "time", clock), patch.object(bridge_transport, "Future", ResponseCost), \
                patch.object(bridge_transport.select, "select", readiness):
            yield SimpleNamespace(peer=subject.peer, connection=subject.connection, clock=clock,
                                  allowances=response_allowances, wire=lambda: bytes(subject.connection.wire),
                                  packet=lambda: json.loads(bytes(subject.connection.wire)))
