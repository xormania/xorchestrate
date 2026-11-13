"""The hosted attach allowance includes the bridge's connect and activation."""

import pytest

from tests.hexagonal.support.hosted_request_budgets import hosted_budget_subject
from xorchestrate.model import OrchestrationError


@pytest.mark.parametrize("method", ["start", "attach", "resume", "reconnect"])
@pytest.mark.parametrize("costs,code,outcome", [
    ({"connect": 8}, "session_start_failed", "not_started"),
    ({"connect": 3, "attach": 5}, "bridge_timeout", "unknown"),
    ({"connect": 1, "attach": 3, "activate": 4}, "bridge_timeout", "unknown"),
])
def test_hosted_attach_allowance_reaches_each_bridge_phase(tmp_path, method, costs, code, outcome):
    with hosted_budget_subject(tmp_path, **costs) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.invoke(method)
        assert caught.value.code == code
        assert caught.value.details["outcome"] == outcome
        assert subject.clock.elapsed == 7
        assert all(connection.closed for connection in subject.connections)
        assert all(peer.closed for peer in subject.peers)
        if code == "session_start_failed":
            assert len(subject.host.closed) == 1
        else:
            assert not subject.host.closed, "an unresolved bridge request must not invent terminal cleanup"


@pytest.mark.parametrize("method", ["start", "attach", "resume", "reconnect"])
def test_hosted_healthy_attachment_keeps_identity_context_and_native_specification(tmp_path, method):
    with hosted_budget_subject(tmp_path, connect=1, attach=2, activate=3) as subject:
        receipt = subject.invoke(method)
        assert receipt["native_id"] == "native-budget"
        assert receipt["incarnation"] == "current-incarnation"
        assert receipt["host_id"] == "host-budget"
        assert receipt["hosted"]["native_specification"] == {"goal": "original-goal"}
        assert subject.calls[0][0] == "attach"
        assert subject.calls[0][1]["context"] == {"session_id": "original-session"}
        if method in {"resume", "reconnect"}:
            assert subject.calls[0][1]["source"]["incarnation"] == "previous-incarnation"
            assert subject.calls[0][1]["source"]["native_id"] == "native-budget"
        else:
            assert subject.calls[0][1]["source"] is None
        assert subject.calls[1][:2] == ("activate", {"incarnation": "current-incarnation"})
        assert subject.clock.elapsed == 6
        assert not subject.peers[0].closed
        assert not subject.host.closed
