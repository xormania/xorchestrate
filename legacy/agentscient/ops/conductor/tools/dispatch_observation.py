"""Pure interpretation of a dispatch record, shared by three read projections.

File acquisition, error policy, process identity and alert state belong to the
caller. A recorded outcome never establishes that a live process is the worker.
See ../runtime-observation-contract.md.
"""

TERMINAL = frozenset({"closed", "died"})


class DispatchRecord:
    def __init__(self, record):
        self.present = record is not None
        self.record = record or {}
        self.status = self.record.get("status")
        result = self.record.get("result")
        self.result = result if isinstance(result, dict) else {}
        envelope = self.result.get("envelope")
        self.envelope = envelope if isinstance(envelope, dict) else {}
        self.changed = self.result.get("changed_paths")
        self.changed_count = len(self.changed) if isinstance(self.changed, list) else None

    def phase(self, has_exit):
        """On-disk phase, independent of envelope validity or process liveness."""
        if not self.present:
            return "no-copy"
        if self.status in TERMINAL:
            return "closed"
        return "settling" if has_exit else "running"

    def outcome(self):
        """Recorded completion outcome, or None when live observation is needed."""
        status = self.envelope.get("status")
        if status in {"invalid", "tripped"}:
            return "invalid"
        if self.status == "died":
            return "death"
        if self.status == "closed" and status == "ok":
            return "no-op" if self.record.get("role") == "write" and self.changed == [] else "completion"
        return None

    def metrics(self):
        metrics = {} if self.changed_count is None else {"changed": self.changed_count}
        session = self.record.get("session")
        session = session if isinstance(session, dict) else {}
        spend = session.get("spend")
        spend = spend if isinstance(spend, dict) else {}
        for key in ("input", "cached", "output", "total"):
            value = spend.get(key)
            if type(value) is int and value >= 0:
                metrics[key] = value
        return metrics
