"""Raw observation adapter for the shared callback identity scenario port."""

from copy import deepcopy

from scripts.verification import checker
from tests.hexagonal.contracts.identity import IdentityOutcome
from tests.hexagonal.support.raw_evidence import lifecycle_input, raw_exchange


def callback(inputs, phase, action):
    return next(record for record in inputs[0]["raw_records"]["observations"]
                if record["observation"].get("phase") == phase
                and record["observation"].get("action") == action)


class RawCallbackEvidence:
    """Expose semantic mutations; only the real checker decides observed status."""

    def __init__(self, scope, operation):
        self.scope = scope
        if scope == "exchange":
            self.inputs = raw_exchange("busy")
            self.raw = self.inputs[0]["raw_records"]
            self.session = self.raw["sessions"][0]
            self.peer = self.inputs[1]["required_cases"][0]["source"]
        else:
            self.inputs, self.observations = lifecycle_input(operation)
            self.raw = self.inputs[1]["raw_records"]
            self.session = self.raw["sessions"][1]
            self.peer = self.inputs[0]["peer"]

    def change_claim(self, change):
        if change.record == "session":
            row, rows = self.session, self.raw["sessions"]
        elif change.record in {"evidence", "evidence-source"}:
            rows = self.raw["evidence"]
            row = rows[0]
        else:
            rows = self.raw["observations"]
            if self.scope == "lifecycle":
                row = self.observations[0 if change.record == "native" else 1]
            elif change.record == "native":
                row = rows[0]
            else:
                phase, action = change.record.split("/")
                row = callback(self.inputs, phase, action)
        if change.claim == "record":
            if not change.absent:
                raise ValueError("Record mutations only support removal")
            rows.remove(row)
            return
        value = change.value
        if value == "qualified-actor":
            value = self.peer
        elif value == "qualified-session":
            value = self.session["id"]
        elif value == "qualified-other":
            value = self.raw["sessions"][1]["id"]
        if change.claim == "actor":
            container, key = row, "actor_id" if change.record == "session" else "created_by"
            if change.record == "evidence-source":
                key, value = "source", {"type": "caller", "actor": value}
        elif change.claim == "application_session":
            container, key = row, "session_id"
        else:
            container = row["backend"] if change.record == "session" else row["observation"]
            key = {"native_session": "native_id", "incarnation": "incarnation",
                   "nested_session": "session_id", "call_id": "call_id"}[change.claim]
        if change.absent:
            container.pop(key, None)
        else:
            container[key] = deepcopy(value)

    def evaluate(self):
        if self.scope == "exchange":
            result = checker.verify(*self.inputs)
            diagnostics = frozenset(case["assertion"] for case in result["cases"] if "assertion" in case)
            return IdentityOutcome(result["status"] == "passed", diagnostics, result)
        try:
            result = checker.lifecycle_evidence(*self.inputs)
        except checker.VerificationFailure as error:
            return IdentityOutcome(False, frozenset((error.assertion,)), {"assertion": error.assertion})
        return IdentityOutcome(True, frozenset(), result)
