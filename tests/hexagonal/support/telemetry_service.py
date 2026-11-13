"""Live collaborator changes using the shared composed telemetry fixture."""

from copy import deepcopy

from tests.hexagonal.support.telemetry import Fixture
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import OrchestrationError


class HealthSource:
    def __init__(self, value=None, *, failure=False, report=None):
        self.value = value
        self.failure = failure
        self.report = report

    def telemetry_health(self):
        if self.report is not None:
            self.report()
        if self.failure:
            raise RuntimeError("private source diagnostic")
        return deepcopy(self.value)


class ChangingAuthority:
    def __init__(self, replace):
        self.replace = replace

    def evaluate(self, action, context):
        self.replace()
        return RuleGuardrail().evaluate(action, context)


class ServiceFixture(Fixture):
    def __init__(self, root, *, boundary="action"):
        super().__init__(root / "original")
        self.boundary = boundary
        self.replacement = Fixture(root / "replacement")
        self.original_capture = None
        self.changed_policy = None

    def close(self):
        self.replacement.close()
        super().close()

    def seed_captures(self):
        self.original_capture = self.app.telemetry
        for subject, identity in ((self, "original-source"), (self.replacement, "replacement-source")):
            result = subject.capture("fixture.service", {"source": identity}, event_id=identity)
            assert result["status"] == "recorded", "The current-source scenario requires durable controls"

    def select_capture(self, selection):
        self.app.telemetry = {"original": self.original_capture,
            "replacement": self.replacement.app.telemetry, "disabled": None}[selection]

    def event_ids(self):
        return [row["event_id"] for row in self.events(event_type="fixture.service")]

    def event_page(self, *, actor="owner", **filters):
        if self.boundary == "service":
            return self.app.telemetry_service.events({"limit": 1000, **filters}, actor)
        return super().event_page(actor=actor, **filters)

    def status(self, *, actor="owner"):
        if self.boundary == "service":
            return self.app.telemetry_service.status({}, actor)
        return super().status(actor=actor)

    def install_health(self, kind, value=None, *, failure=False):
        setattr(self.app, kind, {"controlled": HealthSource(value, failure=failure)})

    def remove_health(self, kind):
        setattr(self.app, kind, {})

    def install_authority_change(self):
        self.changed_policy = RuleGuardrail({"default": "deny"})
        def replace():
            self.app.guardrail = self.changed_policy
        self.app.providers = {"controlled": HealthSource({"pending": 0}, report=replace)}

    def status_failure(self):
        try:
            self.status()
        except OrchestrationError as error:
            return error.code
        return None

    def authority_changed(self):
        return self.app.guardrail is self.changed_policy

    def install_authorization_change(self):
        self.changed_policy = RuleGuardrail({"default": "deny"})
        def replace():
            self.app.guardrail = self.changed_policy
        self.app.guardrail = ChangingAuthority(replace)
