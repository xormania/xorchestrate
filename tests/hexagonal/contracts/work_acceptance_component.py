"""Acceptance component boundaries, independent of its concrete composition."""
from typing import Protocol


class AcceptanceFixture(Protocol):
    def configure_measurement(self) -> dict: ...
    def measure(self, key="measurement-1", actor="worker", arguments=None) -> dict: ...
    def deny_invocation(self, denied: bool) -> None: ...
    def invocation_requests(self) -> list[dict]: ...
    def effect_count(self) -> int: ...
    def accept(self) -> dict: ...
    def accepted(self) -> bool: ...
    def replace_provider_identity(self, identity: str) -> None: ...
    def replace_policy(self, identity: str) -> None: ...
    def change_policy_during_evaluation(self) -> None: ...
    def rows(self, kind: str) -> list[dict]: ...


def authorized_invocation_contract(fixture: AcceptanceFixture):
    requirements = fixture.configure_measurement()
    fixture.deny_invocation(True)
    try:
        fixture.measure(key="denied", actor="caller")
    except Exception as error:
        assert getattr(error, "code", None) == "denied"
    else:
        raise AssertionError("ACCEPTANCE_INVOKE_AUTHORITY_REQUIRED")
    assert fixture.effect_count() == 0, "ACCEPTANCE_DENIED_INVOKE_HAS_NO_EFFECT"
    assert fixture.rows("measurement")[0]["status"] == "failed"

    fixture.deny_invocation(False)
    first = fixture.measure(key="once", actor="caller", arguments={"sample": "caller input"})
    request = fixture.invocation_requests()[-1]
    assert request["actor"] == "caller", "ACCEPTANCE_INVOKE_PRESERVES_CALLER"
    assert request["arguments"]["capability"] == "checks/measure"
    bound = request["arguments"]["arguments"]
    assert bound["arguments"] == {"sample": "caller input"}
    assert bound["measurement"]["work_id"] == "work"
    assert bound["measurement"]["check_id"] == "A"
    assert bound["measurement"]["requirements_identity"] == requirements["identity"]
    assert bound["measurement"]["requirement_revision"] == requirements["version"]
    assert first["measurement"]["status"] == "completed"
    before = fixture.invocation_requests()
    replay = fixture.measure(key="once", actor="caller", arguments={"sample": "caller input"})
    assert replay["replayed"] and replay["measurement"] == first["measurement"]
    assert fixture.invocation_requests() == before and fixture.effect_count() == 1
    try:
        fixture.measure(key="once", actor="caller", arguments={"sample": "changed"})
    except Exception as error:
        assert getattr(error, "code", None) == "conflict"
    else:
        raise AssertionError("ACCEPTANCE_MEASUREMENT_KEY_REMAINS_REQUEST_BOUND")
    assert fixture.invocation_requests() == before and fixture.effect_count() == 1


def live_configuration_contract(fixture: AcceptanceFixture):
    fixture.configure_measurement()
    fixture.measure()
    assert fixture.accept()["accepted"] and fixture.accepted()
    fixture.replace_provider_identity("replacement-provider")
    assert not fixture.accepted(), "ACCEPTANCE_PROVIDER_SUPPLIER_REMAINS_CURRENT"
    fixture.replace_provider_identity("initial-provider")
    assert fixture.accepted()
    fixture.replace_policy("replacement-policy")
    assert not fixture.accepted(), "ACCEPTANCE_POLICY_SUPPLIER_REMAINS_CURRENT"
    assert fixture.accept()["accepted"]
    before = fixture.rows("acceptance_decision")
    fixture.change_policy_during_evaluation()
    try:
        fixture.accept()
    except Exception as error:
        assert getattr(error, "code", None) == "conflict"
    else:
        raise AssertionError("ACCEPTANCE_POLICY_RACE_CANNOT_COMMIT")
    assert fixture.rows("acceptance_decision") == before
