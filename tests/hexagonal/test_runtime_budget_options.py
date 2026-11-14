"""Per-call bridge allowances narrow the configured runtime request bound."""

import pytest

from tests.hexagonal.support.runtime_request_budgets import bridge_budget_subject
from xorchestrate.model import OrchestrationError


@pytest.mark.parametrize("requested,expected", [(3, 3), (30, 10)])
def test_bridge_request_override_cannot_extend_its_effective_allowance(requested, expected):
    with bridge_budget_subject(response=11) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.peer.request("observe", {"identity": "bounded"}, timeout=requested)
        assert caught.value.code == "bridge_timeout"
        assert caught.value.details["outcome"] == "unknown"
        assert subject.clock.elapsed == expected
        assert subject.allowances == [expected]
        assert not subject.peer.closed
        assert list(subject.peer._pending) == [subject.packet()["id"]]


@pytest.mark.parametrize("requested,expected", [(3, 3), (30, 10)])
def test_bridge_request_override_preserves_healthy_completion(requested, expected):
    with bridge_budget_subject(response=2) as subject:
        assert subject.peer.request("observe", {"identity": "healthy"}, timeout=requested) == {
            "observed": "original response"}
        assert subject.allowances == [expected]
        assert subject.clock.elapsed == 2
        assert not subject.peer.closed
