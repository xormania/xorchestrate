"""Replaceable delivery policies selected by trusted composition."""
from ..model import OrchestrationError


class LatestSessionSelection:
    def __init__(self, options=None):
        self.options = options or {}
        if self.options.keys() - {"driver"}:
            raise OrchestrationError("Session selection accepts an optional driver.", "configuration_error")

    def select(self, message, recipient, sessions):
        eligible = [s for s in sessions if not self.options.get("driver") or s["driver"] == self.options["driver"]]
        return max(eligible, key=lambda s: (s.get("created_at", ""), s["id"]))["id"] if eligible else None


class DeliverySchedule:
    def __init__(self, options=None):
        options = options or {}
        if options.keys() - {"dispatch_on_enqueue", "paused"} or any(type(v) is not bool for v in options.values()):
            raise OrchestrationError("Delivery scheduling accepts boolean dispatch_on_enqueue and paused.", "configuration_error")
        self.dispatch_on_enqueue = options.get("dispatch_on_enqueue", False)
        self.paused = options.get("paused", False)

    def ready(self, delivery, session, now):
        return not self.paused


class DefiniteFailureRetry:
    def __init__(self, options=None):
        options = options or {}
        self.maximum = options.get("max_attempts", 3)
        if options.keys() - {"max_attempts"} or type(self.maximum) is not int or self.maximum < 1:
            raise OrchestrationError("Retry max_attempts must be a positive integer.", "configuration_error")

    def permit(self, delivery, history):
        return delivery["status"] == "failed" and len(history) < self.maximum


class CompleteExchangeReceipt:
    def __init__(self, options=None):
        if options:
            raise OrchestrationError("Complete exchange receipt has no implicit policy overrides.", "configuration_error")

    def evaluate(self, snapshot, recipient, expectation):
        from ..exchange import evaluate_exchange
        return evaluate_exchange(snapshot, recipient, expectation)
