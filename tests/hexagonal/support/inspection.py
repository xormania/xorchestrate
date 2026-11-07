"""Standalone inspection composition using storage and narrow callbacks only."""
from copy import deepcopy

from tests.hexagonal.contracts.inspection import OPAQUE, InspectionFailure
from xorchestrate.inspection import InspectionProjector
from xorchestrate.model import Conflict, OrchestrationError

from .application import make_store


def _view(record):
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


class Fixture:
    def __init__(self, implementation, path):
        self.store = make_store(implementation, path)
        self.change = None
        with self.store.transaction() as tx:
            tx.put("work", "work", {"goal": "inspection", "context": deepcopy(OPAQUE)})
            for identity, marker in (("public-session", "public"), ("private-session", "private")):
                tx.put("session", identity, {"work_id": "work", "specification": {"nested": {"value": marker}}})
            tx.put("evidence", "evidence", {"work_id": "work", "source": {"type": "caller"},
                "result": deepcopy(OPAQUE)})
            self.source = self.read_records(tx)
        self.projector = InspectionProjector(self.store, self.context, self.authorize, self.verify_context)

    def close(self):
        self.store.close()

    def context(self, actor, arguments):
        bindings = []
        with self.store.transaction() as tx:
            for field, kind in (("work_id", "work"), ("session_id", "session")):
                if identity := arguments.get(field):
                    record = tx.get(kind, identity)
                    if record is None:
                        raise OrchestrationError("Fixture record missing", "not_found")
                    bindings.append((kind, identity, record.version))
        return {"actor": actor, "arguments": deepcopy(arguments), "bindings": bindings}

    def authorize(self, action, context):
        if context.get("inspection", {}).get("id") == "private-session":
            raise OrchestrationError("Fixture record denied", "denied")
        if context.get("inspection", {}).get("id") == "public-session" and self.change is not None:
            change, self.change = self.change, None
            with self.store.transaction() as tx:
                record = tx.get("session", "public-session")
                if change == "delete":
                    tx.delete("session", record.key, record.version)
                else:
                    tx.put("session", record.key, {**record.data, "changed": True}, record.version)

    def verify_context(self, tx, context):
        for kind, identity, version in context["bindings"]:
            current = tx.get(kind, identity)
            if current is None or current.version != version:
                raise Conflict("Fixture context changed")

    def read_records(self, tx):
        return {"work": _view(tx.get("work", "work")),
            "sessions": [_view(row) for row in tx.list("session")],
            "evidence": [_view(row) for row in tx.list("evidence")]}

    def snapshot(self, tx):
        # Deliberately share the caller-owned input. The collaborator must
        # detach its public result rather than rely on a copying supplier.
        return self.source

    def inspect(self):
        try:
            return self.projector.inspect_snapshot("get_work", "observer",
                self.context("observer", {"work_id": "work"}), self.snapshot)
        except OrchestrationError as error:
            raise InspectionFailure(error.code) from error

    def raw_records(self):
        with self.store.transaction() as tx:
            return {"source": deepcopy(self.source), "store": self.read_records(tx)}

    def change_during_authorization(self, change):
        self.change = change
