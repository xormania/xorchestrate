"""Controlled measurements and owner-work setup shared by acceptance families."""

from copy import deepcopy
from uuid import uuid4

from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
from xorchestrate.model import Assessment, Capability

from .application import application

CANDIDATE = {"identity": "tree:measured-product", "configuration": "config:nondefault"}
BINDINGS = {"scope": "product", "artifact": {"name": "result.json", "sha256": "measured-artifact"},
            "native": {"session_id": "conversation", "incarnation": "incarnation-2"}}


class MeasuringProvider:
    """Controlled measurement boundary: actual candidate is independent of request."""
    def __init__(self):
        self.actual_candidate = deepcopy(CANDIDATE)
        self.actual_bindings = deepcopy(BINDINGS)
        self.transform = lambda result: result
        self.calls = []
        self.entered = None
        self.release = None

    def discover(self):
        return [Capability("measure", "Measure the configured product", {"type": "object", "required": ["measurement", "arguments"]})]

    def invoke(self, name, arguments, context):
        self.calls.append(deepcopy(arguments))
        if self.entered:
            self.entered.set()
            assert self.release.wait(5), "test measurement release missing"
        request = arguments["measurement"]
        observed = {key: deepcopy(request[key]) for key in ("work_id", "check_id", "requirements_identity", "requirement_revision")}
        observed.update({"candidate": deepcopy(self.actual_candidate), "bindings": deepcopy(self.actual_bindings),
            "status": "completed", "passed": True, "outcomes": {name: True for name in request["outcomes"]}})
        return self.transform({"measurement": observed})

    def close(self):
        pass


class Evaluator:
    def __init__(self, action=None):
        self.contexts = []
        self.action = action

    def assess(self, question, context):
        self.contexts.append(deepcopy(context))
        if self.action:
            self.action()
        return Assessment("pass", .999, "Favorable assessment is not mechanical evidence")

    def close(self):
        pass


def make_app(store=None, provider=None, policy=None, evaluator=None, guardrail=None):
    return application(store=store, providers={"checks": provider if provider is not None else MeasuringProvider()},
        guardrail=guardrail, evaluators={"review": evaluator} if evaluator is not None else {},
        clock=lambda: "2026-10-23T20:00:00Z", identifiers=lambda: uuid4().hex,
        assessment={"triage": "review"} if evaluator is not None else {},
        acceptance=policy if policy is not None else RequiredChecksAcceptance())


def setup_work(app, outcomes=None):
    work = app.call("create_work", {"goal": "Deliver A and B"})["work"]
    outcomes = {"A": "Happy path", "B": "Recovery"} if outcomes is None else outcomes
    requirements = app.call("set_requirements", {"work_id": work["id"], "expected_revision": 0,
        "objective": "Deliver the complete owner objective", "outcomes": outcomes,
        "checks": {key: {"capability": "checks/measure", "outcomes": [key], "bindings": BINDINGS} for key in outcomes}})["requirements"]
    app.call("set_candidate", {"work_id": work["id"], "expected_revision": 0, "candidate": CANDIDATE})
    return work, requirements


def measure(app, work, check="A", key=None):
    return app.call("run_measurement", {"work_id": work["id"], "check_id": check, "measurement_key": key or uuid4().hex})


def accept(app, work, assessment=None):
    args = {"work_id": work["id"]}
    if assessment:
        args["assessment_id"] = assessment["id"]
    return app.call("accept_work", args)["acceptance"]


def issues(decision):
    return {item["code"] for item in decision["issues"]}
