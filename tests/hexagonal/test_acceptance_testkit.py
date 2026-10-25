"""Acceptance composes the shared resource policy, including failed closes."""

import pytest

from scripts.acceptance import scenario as acceptance


@pytest.fixture
def client_factory(monkeypatch):
    closed = []

    class Client:
        def __init__(self, config, deadline, actor):
            self.name = config
            self.failure = None

        def close(self):
            closed.append(self.name)
            if self.failure:
                raise self.failure

    monkeypatch.setattr(acceptance, "Client", Client)
    return closed


def test_scenario_drains_all_clients_after_one_close_fails(tmp_path, client_factory):
    scenario = acceptance.Scenario(tmp_path, 1, "fixture")
    scenario.client("first")
    broken = scenario.client("broken")
    broken.failure = RuntimeError("shutdown failed")
    scenario.client("last")

    with pytest.raises(acceptance.AcceptanceFailure, match="runtime_cleanup_failed"):
        scenario.cleanup()

    assert client_factory == ["last", "broken", "first"]
    assert scenario.clients == []
    # A later cleanup call cannot turn the failed ownership scope into success.
    with pytest.raises(acceptance.AcceptanceFailure, match="runtime_cleanup_failed"):
        scenario.cleanup()
    assert client_factory == ["last", "broken", "first"]


def test_failed_explicit_close_remains_failure_while_other_clients_are_cleaned(tmp_path, client_factory):
    scenario = acceptance.Scenario(tmp_path, 1, "native")
    scenario.client("remaining")
    broken = scenario.client("broken")
    broken.failure = RuntimeError("shutdown failed")

    with pytest.raises(acceptance.AcceptanceFailure, match="runtime_cleanup_failed"):
        scenario.close(broken)
    with pytest.raises(acceptance.AcceptanceFailure, match="runtime_cleanup_failed"):
        scenario.cleanup()

    assert client_factory == ["broken", "remaining"]
    assert scenario.clients == []


def test_exportable_check_receipt_redacts_unexpected_exception_text(tmp_path):
    scenario = acceptance.Scenario(tmp_path, 1, "native")
    with pytest.raises(RuntimeError):
        with scenario.check("native-call"):
            raise RuntimeError("private launch configuration")
    assert scenario.checks[0]["failure"] == {"type": "RuntimeError", "code": "scenario_error"}
    assert scenario.checks[0]["status"] == "failed"
