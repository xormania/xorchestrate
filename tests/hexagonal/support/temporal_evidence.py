"""Independent store-order fixtures and diagnostic-time perturbations.

Fixture order is assigned at the construction/observation boundary. No expected
order is recovered from timestamps, normalized events, or checker verdicts.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone


class StoreOrder:
    def __init__(self, raw, source_id="fixture-store"):
        self.envelope = raw.setdefault("ordering", {"version": 1, "source_id": source_id, "events": []})
        self.events = self.envelope["events"]

    def emit(self, **fields):
        event = {"seq": max((row["seq"] for row in self.events), default=0) + 1, **deepcopy(fields)}
        self.events.append(event)
        return event

    def created(self, kind, row):
        return self.emit(type=kind + ".created", record_id=row["id"], at=row.get("created_at"))

    def submitted(self, attempt):
        return self.emit(type="state.transition", subject={"kind": "delivery_attempt", "key": attempt["id"]},
                         event="submit", target="submitted", at=attempt.get("finished_at"))

    def provider(self, event):
        event.setdefault("source_id", "fixture-provider")
        existing = [row["observation"] for row in self.events if row.get("type") == "verification.provider"]
        event.setdefault("source_seq", max((row["source_seq"] for row in existing
                                            if row.get("source_id") == event["source_id"]), default=0) + 1)
        return self.emit(type="verification.provider", observation=event)

    def creation(self, kind, identifier):
        return next(row for row in self.events if row.get("type") == kind + ".created" and row.get("record_id") == identifier)

    def marker(self, event):
        return next(row for row in self.events if row.get("type") == "verification.provider"
                    and row["observation"].get("source_id") == event["source_id"]
                    and row["observation"].get("source_seq") == event["source_seq"])

    def place(self, event, reference, *, after=False):
        """Declare a different fixture execution, preserving a unique log order."""
        ordered = sorted(self.events, key=lambda row: row["seq"])
        ordered.remove(event)
        ordered.insert(ordered.index(reference) + int(after), event)
        for sequence, row in enumerate(ordered, 1):
            row["seq"] = sequence
        self.events[:] = ordered


def record_observations(raw):
    """Stamp rows in the order an independent observer just captured them."""
    order = StoreOrder(raw)
    for row in raw["observations"]:
        order.created("native_observation", row)
    return order


def distort_wall_time(value, mode):
    """Alter only diagnostic timestamps, including their retained copies."""
    base = datetime(2026, 10, 24, tzinfo=timezone.utc)
    def stamp(text):
        original = datetime.fromisoformat(text)
        if mode == "equal":
            return base.isoformat()
        if mode == "rollback":
            return (base - (original - base)).isoformat()
        if mode == "forward":
            return (original + timedelta(days=90)).isoformat()
        raise ValueError(mode)
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"at", "created_at", "finished_at"} and isinstance(item, str):
                value[key] = stamp(item)
            else:
                distort_wall_time(item, mode)
    elif isinstance(value, list):
        for item in value:
            distort_wall_time(item, mode)


def reverse_capture_lists(run):
    """Change delivery order without changing the independently stamped log."""
    for value in run["raw_records"].values():
        if isinstance(value, list):
            value.reverse()
    run["raw_records"].get("ordering", {}).get("events", []).reverse()
    run.get("provider_events", []).reverse()


@contextmanager
def composed_exchange(directory, implementation):
    """Exercise existing suite capture with actual callbacks and provider holds."""
    import json

    from scripts.verification.model import canonical, digest
    from scripts.verification.multi_harness import FIXTURE, ComposedSuite, prepare_config
    from xorchestrate.composition import build

    case = {"id": "timed-busy", "kind": "busy", "source": "codex", "target": "grok"}
    criteria = {"required_cases": [{"id": "startup-" + peer, "kind": "startup", "peer": peer}
                                    for peer in ("codex", "claude", "grok")] + [case]}
    fixture = json.loads(FIXTURE.read_text())
    bindings = {"criteria_sha256": digest(canonical(criteria)), "fixture_sha256": digest(canonical(fixture))}
    config = prepare_config({}, directory, directory / "state", "fixture")
    config["store"] = {"adapter": implementation, "options": (
        {"path": str(directory / "state.sqlite3")} if implementation == "sqlite" else {})}
    runtime = build(config, directory)
    try:
        suite = ComposedSuite(runtime, directory, "fixture", criteria, bindings, fixture, timeout=10,
                              config=config, base=directory)
        suite.start()
        suite.exchange(case)
        suite.settled()
        captured = suite.raw_run()
    finally:
        runtime.close()
    captured["cleanup"] = {"status": "passed"}
    yield captured, criteria, bindings, fixture
