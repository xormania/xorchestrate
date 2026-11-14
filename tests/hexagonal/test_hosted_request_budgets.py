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


@pytest.mark.parametrize("method", ["start", "attach", "resume", "reconnect"])
def test_hosted_uncertain_attach_failure_is_not_retried_or_reclassified(tmp_path, method):
    failure = OrchestrationError("partial write outcome is unresolved", "bridge_unavailable", {"outcome": "unknown"})
    with hosted_budget_subject(tmp_path, attach=2, attach_errors=[failure]) as subject:
        with pytest.raises(OrchestrationError) as caught:
            subject.invoke(method)
        assert caught.value is failure, "an uncertain operation retains its original failure and outcome"
        assert subject.clock.elapsed == 2
        assert [call[0] for call in subject.calls] == ["attach"]
        assert len(subject.connections) == 1
        assert subject.connections[0].closed
        assert not subject.host.closed, "unresolved attachment must not trigger terminal cleanup"


@pytest.mark.parametrize("method", ["start", "attach", "resume", "reconnect"])
def test_hosted_definite_unavailable_attach_can_retry_with_remaining_allowance(tmp_path, method):
    failure = OrchestrationError("endpoint not yet available", "bridge_unavailable", {"outcome": "not_started"})
    with hosted_budget_subject(tmp_path, attach=2, attach_errors=[failure]) as subject:
        receipt = subject.invoke(method)
        assert receipt["native_id"] == "native-budget"
        assert [call[0] for call in subject.calls] == ["attach", "attach", "activate"]
        assert subject.clock.elapsed == pytest.approx(4.1)
        assert subject.calls[1][2] == pytest.approx(4.9), "a safe retry receives only the original remainder"
        assert subject.connections[0].closed
        assert not subject.connections[1].closed
        assert not subject.host.closed
