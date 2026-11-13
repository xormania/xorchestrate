"""One runtime allowance survives provider and bridge phase boundaries."""

import pytest

from tests.hexagonal.support.runtime_request_budgets import bridge_budget_subject, mcp_budget_subject
from xorchestrate.model import OrchestrationError


def test_mcp_initialized_notification_receives_discovery_remainder():
    with mcp_budget_subject() as subject:
        assert [tool.name for tool in subject.provider.discover()] == ["read"]
        assert subject.transport.calls == [("initialize", 10), ("notifications/initialized", 6), ("tools/list", 4)]
        assert subject.clock.elapsed == 7


def test_mcp_initialized_notification_cannot_extend_operation_allowance():
    with mcp_budget_subject(initialized=8) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.provider.discover()
        assert caught.value.code == "provider_timeout"
        assert subject.clock.elapsed == 10
        assert [method for method, _ in subject.transport.calls] == ["initialize", "notifications/initialized"]
        assert subject.transport.closed


def test_mcp_immediate_initialization_retains_full_default_allowance():
    with mcp_budget_subject(initialize=0, initialized=0, listing=0) as subject:
        assert [tool.name for tool in subject.provider.discover()] == ["read"]
        assert subject.transport.calls == [("initialize", 10), ("notifications/initialized", 10), ("tools/list", 10)]
        assert not subject.transport.closed


@pytest.mark.parametrize("admission,writes,response", [(0, [4], 7), (4, [3], 4)])
def test_bridge_response_uses_allowance_left_after_admission_and_write(admission, writes, response):
    with bridge_budget_subject(admission=admission, writes=writes, response=response) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.peer.request("observe", {"identity": "original"})
        assert caught.value.code == "bridge_timeout"
        assert caught.value.details["outcome"] == "unknown"
        assert subject.clock.elapsed == 10
        assert not subject.peer.closed
        packet = subject.packet()
        assert packet["params"] == {"identity": "original"}
        assert list(subject.peer._pending) == [packet["id"]]


def test_bridge_writer_admission_expiry_never_sends_or_fences():
    with bridge_budget_subject(admission=12) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.peer.request("effect", {"identity": "must-not-start"})
        assert caught.value.details["outcome"] == "not_started"
        assert subject.clock.elapsed == 10
        assert subject.wire() == b""
        assert subject.peer._pending == {}
        assert not subject.peer.closed


def test_bridge_partial_write_uses_admission_remainder_and_fences():
    with bridge_budget_subject(admission=3, writes=[5, 3]) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.peer.request("effect", {"identity": "uncertain"})
        assert caught.value.details["outcome"] == "unknown"
        assert subject.clock.elapsed == 10
        assert subject.wire() and not subject.wire().endswith(b"\n")
        assert subject.peer.closed
        assert subject.peer._pending == {}


def test_bridge_healthy_exchange_preserves_frame_and_remaining_response_allowance():
    with bridge_budget_subject(admission=2, writes=[3, 1], response=3) as subject:
        assert subject.peer.request("observe", {"identity": "healthy"}) == {"observed": "original response"}
        assert subject.packet()["method"] == "observe"
        assert subject.packet()["params"] == {"identity": "healthy"}
        assert subject.allowances == [4]
        assert subject.clock.elapsed == 9
        assert not subject.peer.closed


def test_bridge_zero_cost_exchange_preserves_default_allowance():
    with bridge_budget_subject(response=2) as subject:
        assert subject.peer.request("observe", {}) == {"observed": "original response"}
        assert subject.allowances == [10]
        assert subject.clock.elapsed == 2
        assert not subject.peer.closed
