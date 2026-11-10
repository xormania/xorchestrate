"""Controlled failures exercised through the real application and MCP boundaries."""

from copy import deepcopy

from tests.hexagonal.support.application import Provider, application
from tests.hexagonal.support.delivery import Native
from xorchestrate.mcp import MCPServer
from xorchestrate.model import OrchestrationError, record_view


class PRIVATE_ERROR_TYPE(Exception):
    pass


class EffectErrors:
    def __init__(self, store, boundary="application"):
        self.provider = Provider()
        self.app = application(store, provider=self.provider, driver=Native())
        self.boundary = boundary
        self.server = MCPServer(self.app)
        self.server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}})

    @property
    def effect_calls(self):
        return len(self.provider.calls)

    def call(self, action, arguments):
        if self.boundary == "mcp":
            response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": action, "arguments": arguments}})
            assert "result" in response, response
            return response["result"]["structuredContent"]
        try:
            return self.app.call(action, arguments)
        except OrchestrationError as error:
            return {"error": {"code": error.code, "message": str(error), "details": error.details}}

    def invoke(self, key="effect"):
        return self.call("invoke", {"capability": "tools/change", "arguments": {"value": 1}, "idempotency_key": key})

    def raising(self, error):
        def fail(*args):
            raise error
        self.provider.callback = fail

    def fail(self, variant, completed=False):
        error = (OrchestrationError("Public adapter failure.") if variant == "bad_outcome"
            or completed and variant != "json" else PRIVATE_ERROR_TYPE("PRIVATE_ERROR_MESSAGE"))
        error.code = "PRIVATE_ERROR_CODE" if not isinstance(error, OrchestrationError) else "completed"
        error.details = ({"outcome": []} if variant == "bad_outcome" else {"external_result": {"private": "PRIVATE_ERROR_DETAILS"}} if variant == "json"
            else {"external_result": object()} if variant == "non_json" else ["PRIVATE_ERROR_DETAILS"])
        if completed:
            error._xorchestrate_effect_outcome = "completed"
        self.raising(error)

    def complete(self, result, field):
        self.raising(OrchestrationError("Result needs reconciliation.", "completion", {"outcome": "completed", field: result}))

    def reject(self):
        self.raising(OrchestrationError("Remote branch changed; inspect before retrying.", "remote_changed",
            {"outcome": "failed", "retry_safe": False}))

    def succeed(self, result):
        self.provider.callback = lambda *args: deepcopy(result)

    def raw(self, operation_id):
        with self.app.store.transaction() as tx:
            return record_view(tx.get("operation", operation_id))

    def seed_legacy(self, key="effect"):
        self.raising(TimeoutError("old failure"))
        first = self.invoke(key)
        operation_id = first["error"]["details"]["operation_id"]
        with self.app.store.transaction() as tx:
            operation = tx.get("operation", operation_id)
            data = {**operation.data, "error": {"message": "PRIVATE_ERROR_MESSAGE", "code": "PRIVATE_ERROR_CODE",
                "details": {"private": "PRIVATE_ERROR_DETAILS"}}, "application_error": "PRIVATE_ERROR_MESSAGE"}
            data.pop("error_schema_version", None)
            data.pop("application_error_schema_version", None)
            tx.put("operation", operation_id, data, operation.version)
        return operation_id

    def legacy_history(self, operation_id):
        session = self.app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "history"})["session"]
        source = {"actor": "owner", "session_id": session["id"], "native_id": session["backend"]["native_id"],
            "incarnation": session["backend"]["incarnation"], "call_id": "legacy-inspect"}
        self.app.invoke_native("agent", "get_operation", {"operation_id": operation_id}, source)
        # Old native history may contain snapshots written before projection existed.
        legacy = {"operation": self.raw(operation_id)}
        with self.app.store.transaction() as tx:
            for kind in ("native_call", "native_observation"):
                for row in tx.list(kind):
                    data = deepcopy(row.data)
                    payload = data if kind == "native_call" else data.get("observation", {})
                    if payload.get("action") == "get_operation":
                        payload["result"] = deepcopy(legacy)
                        tx.put(kind, row.key, data, row.version)
        return self.call("native_observations", {"session_id": session["id"]})
