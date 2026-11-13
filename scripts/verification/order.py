"""Retained serialization witnesses, separate from diagnostic civil timestamps.

One envelope describes one captured store log. Its sequence orders committed
observations at that boundary; it does not assert remote event occurrence order.
"""
from .model import canonical, require


class EvidenceOrder:
    def __init__(self, raw):
        envelope = raw.get("ordering")
        require(isinstance(envelope, dict), "ordering_evidence_missing")
        require(type(envelope.get("version")) is int and envelope["version"] == 1, "ordering_evidence_version")
        self.source = envelope.get("source_id")
        require(isinstance(self.source, str) and self.source, "ordering_evidence_domain")
        self.events = envelope.get("events")
        require(isinstance(self.events, list) and all(isinstance(row, dict) for row in self.events),
                "ordering_evidence_invalid")
        sequences = [row.get("seq") for row in self.events]
        require(all(type(value) is int and value > 0 for value in sequences)
                and len(sequences) == len(set(sequences)), "ordering_sequence_invalid")
        self.creations, self.submissions, self.providers = {}, {}, {}
        for row in self.events:
            kind, identifier = row.get("type"), row.get("record_id")
            if isinstance(kind, str) and kind.endswith(".created") and isinstance(identifier, str):
                self.creations.setdefault((kind.removesuffix(".created"), identifier), []).append(row)
            if (kind == "state.transition" and isinstance(row.get("subject"), dict)
                    and row["subject"].get("kind") == "delivery_attempt"
                    and isinstance(row["subject"].get("key"), str)
                    and row.get("event") == "submit" and row.get("target") == "submitted"):
                self.submissions.setdefault(row["subject"]["key"], []).append(row)
            observed = row.get("observation")
            if (kind == "verification.provider" and isinstance(observed, dict)
                    and isinstance(observed.get("source_id"), str) and type(observed.get("source_seq")) is int):
                self.providers.setdefault((observed["source_id"], observed["source_seq"]), []).append(row)

    def _position(self, matches, diagnostic):
        require(len(matches) <= 1, diagnostic)
        return {"source": self.source, "seq": matches[0]["seq"]} if matches else None

    def created(self, kind, identifier):
        return self._position(self.creations.get((kind, identifier), []), "record_order_ambiguous")

    def submitted(self, identifier):
        return self._position(self.submissions.get(identifier, []), "record_order_ambiguous")

    def provider(self, event):
        source, sequence = event.get("source_id"), event.get("source_seq")
        require(isinstance(source, str) and source and type(sequence) is int and sequence > 0,
                "provider_order_missing")
        matches = self.providers.get((source, sequence), [])
        require(len(matches) == 1, "provider_order_missing")
        # The stamp is diagnostic. All semantic fields must agree with the
        # independently recorded fixture marker, including its source identity.
        expected = {key: value for key, value in event.items() if key != "at"}
        actual = {key: value for key, value in matches[0]["observation"].items() if key != "at"}
        require(canonical(expected) == canonical(actual), "provider_order_binding")
        return self._position(matches, "provider_order_missing")


def position(event):
    order = event.get("order")
    require(isinstance(order, dict) and isinstance(order.get("source"), str) and order["source"]
            and type(order.get("seq")) is int and order["seq"] > 0, "record_order_missing")
    return order["source"], order["seq"]


def precedes(first, second, *, inclusive=False):
    left, right = position(first), position(second)
    require(left[0] == right[0], "ordering_domain_mismatch")
    return left[1] <= right[1] if inclusive else left[1] < right[1]


def latest_observation(events):
    positions = [position(event) for event in events]
    require(len({source for source, _ in positions}) <= 1, "ordering_domain_mismatch")
    return max(events, key=lambda event: position(event)[1]) if events else None


def display_order(event):
    """Stable presentation only; absent witnesses never acquire a proof order."""
    order = event.get("order")
    return (order is None, order["seq"] if order else 0, canonical(event))
