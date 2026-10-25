"""External capability providers. Discovery supplies data, never executable policy.

CLI commands accept one JSON object on stdin and return one JSON object on
stdout. Discovery returns ``{"capabilities": [definitions...]}``; invocation
receives ``{"name": ..., "arguments": {...}, "context": {...}}``. A definition
contains name, description, input_schema, and optional annotations/output_schema.
Only the configured command is executable, even if discovery supplies a command.

MCP uses its published stdio lifecycle and tools protocol. Server instructions,
annotations, and descriptions are untrusted content; they do not alter policy.
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import asdict

from .. import __version__
from ..model import Capability, OrchestrationError
from ..schema import check_schema, validate
from .jsonrpc import JsonLineProcess, command_options, encode_json, positive_integer, positive_number, run_json_process


def _definitions(items, *, mcp: bool, maximum: int) -> tuple[list[Capability], dict]:
    if not isinstance(items, list) or len(items) > maximum:
        raise OrchestrationError("Capability definitions must be a bounded list.", "provider_protocol")
    result, outputs, names = [], {}, set()
    for item in items:
        if not isinstance(item, dict):
            raise OrchestrationError("Capability definition must be an object.", "provider_protocol")
        name, description = item.get("name"), item.get("description", "")
        schema = item.get("inputSchema" if mcp else "input_schema")
        annotations = item.get("annotations", {})
        output_schema = item.get("outputSchema" if mcp else "output_schema")
        if not isinstance(name, str) or not name or name in names or not isinstance(description, str):
            raise OrchestrationError("Capability names must be nonempty and unique; descriptions must be strings.", "provider_protocol")
        if not isinstance(schema, dict) or not isinstance(annotations, dict):
            raise OrchestrationError("Capability input schema and annotations must be objects.", "provider_protocol")
        try:
            check_schema(schema)
            if output_schema is not None:
                if not isinstance(output_schema, dict):
                    raise OrchestrationError("Capability output schema must be an object.")
                check_schema(output_schema)
        except OrchestrationError as exc:
            raise OrchestrationError("Capability definition contains an invalid schema.", "provider_protocol") from exc
        names.add(name)
        result.append(Capability(name, description, copy.deepcopy(schema), copy.deepcopy(annotations)))
        if output_schema is not None:
            outputs[name] = copy.deepcopy(output_schema)
    return result, outputs


def _arguments(arguments, context, schema):
    if not isinstance(arguments, dict) or not isinstance(context, dict):
        raise OrchestrationError("Capability arguments and context must be objects.", details={"outcome": "not_started"})
    try:
        validate(arguments, schema)
        encode_json(context)
    except OrchestrationError as exc:
        exc.details["outcome"] = "not_started"
        raise


def _expected_descriptor(capability: Capability, context: dict) -> None:
    # Application-owned context binds this invocation to the descriptor its
    # policy evaluated. It is compared as data and does not confer authority.
    if "capability" in context and not _same_json(context["capability"], asdict(capability)):
        raise OrchestrationError("Capability descriptor changed since authorization.", "capability_changed", {"outcome": "not_started"})


def _same_json(left, right) -> bool:
    # Python equality conflates True with 1 (and False with 0), whereas policy
    # and JSON Schema distinguish booleans from numbers. Ignore object order
    # while preserving JSON value types throughout nested descriptors.
    return encode_json(left, sort_keys=True) == encode_json(right, sort_keys=True)


def _content_blocks(content):
    if not isinstance(content, list):
        raise OrchestrationError("MCP tool result must contain a content list.", "provider_protocol", {"outcome": "unknown"})
    for item in content:
        valid = isinstance(item, dict)
        if valid:
            kind = item.get("type")
            if kind == "text":
                valid = isinstance(item.get("text"), str)
            elif kind in ("image", "audio"):
                valid = isinstance(item.get("data"), str) and isinstance(item.get("mimeType"), str)
            elif kind == "resource_link":
                valid = isinstance(item.get("uri"), str) and isinstance(item.get("name"), str)
            elif kind == "resource":
                resource = item.get("resource")
                valid = isinstance(resource, dict) and isinstance(resource.get("uri"), str) and (
                    isinstance(resource.get("text"), str) or isinstance(resource.get("blob"), str)
                )
            else:
                valid = False
            valid = valid and ("annotations" not in item or isinstance(item["annotations"], dict))
        if not valid:
            raise OrchestrationError("MCP tool returned invalid content blocks.", "provider_protocol", {"outcome": "unknown"})


class CLIProvider:
    """Manifest- or command-discovered tools using an operator-configured argv."""

    def __init__(self, options: dict):
        self.options = copy.deepcopy(options)
        command_options(self.options)
        positive_number(self.options, "timeout", 30)
        positive_integer(self.options, "max_output_bytes", 4 * 1024 * 1024)
        self.maximum = positive_integer(self.options, "max_tools", 10000)
        if "discovery_command" in self.options:
            command_options(self.options, "discovery_command")
        elif "capabilities" not in self.options:
            raise OrchestrationError("CLI provider requires capabilities or discovery_command.", "invalid_configuration")
        self._closed = False
        self._capabilities: dict[str, Capability] = {}
        self._outputs: dict = {}
        self._has_snapshot = False
        self._snapshot_lock = threading.RLock()

    def _open(self):
        if self._closed:
            raise OrchestrationError("Capability provider is closed.", "provider_unavailable", {"outcome": "not_started"})

    def discover(self) -> list[Capability]:
        with self._snapshot_lock:
            return self._discover()

    def _discover(self) -> list[Capability]:
        self._open()
        if "discovery_command" in self.options:
            result = run_json_process(self.options, {"operation": "discover"}, "discovery_command")
            definitions = result.get("capabilities")
        else:
            definitions = self.options["capabilities"]
        capabilities, outputs = _definitions(definitions, mcp=False, maximum=self.maximum)
        self._capabilities = {capability.name: capability for capability in capabilities}
        self._outputs = outputs
        self._has_snapshot = True
        return copy.deepcopy(capabilities)

    def invoke(self, name: str, arguments: dict, context: dict) -> dict:
        with self._snapshot_lock:
            self._open()
            # Use the descriptor the caller discovered and authorized. Refreshing
            # here could substitute new metadata after the caller's policy check.
            # An empty snapshot is still a snapshot; only explicit discovery can
            # add names or replace schemas after this point.
            if not self._has_snapshot:
                self._discover()
            capability = self._capabilities.get(name)
            if capability is None:
                raise OrchestrationError("Unknown capability.", "not_found", {"outcome": "not_started"})
            _arguments(arguments, context, capability.input_schema)
            _expected_descriptor(capability, context)
            output_schema = self._outputs.get(name)
        # Independent CLI invocations can run concurrently after capturing the
        # selected descriptor; later discovery cannot replace these schemas.
        result = run_json_process(self.options, {"name": name, "arguments": arguments, "context": context})
        if output_schema is not None:
            try:
                validate(result, output_schema)
            except OrchestrationError as exc:
                raise OrchestrationError("CLI output does not match its advertised schema.", "provider_protocol", {"outcome": "unknown"}) from exc
        return result

    def close(self) -> None:
        self._closed = True


class StdioMCPProvider:
    """MCP stdio tool discovery/calls, with no catalog or role assumptions."""

    # Protocol compatibility, not project policy. Implemented features are the
    # common stdio lifecycle, paginated tools/list, and synchronous tools/call.
    SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

    def __init__(self, options: dict):
        self.options = copy.deepcopy(options)
        command_options(self.options)
        self.timeout = positive_number(self.options, "timeout", 30)
        self.maximum = positive_integer(self.options, "max_tools", 10000)
        self.max_pages = positive_integer(self.options, "max_pages", 100)
        self.max_discovery = positive_integer(self.options, "max_discovery_bytes", self.options.get("max_output_bytes", 4 * 1024 * 1024))
        self.version = self.options.get("protocol_version", self.SUPPORTED_VERSIONS[0])
        if self.version not in self.SUPPORTED_VERSIONS:
            raise OrchestrationError("Configured MCP protocol version is unsupported.", "invalid_configuration")
        self._client: JsonLineProcess | None = None
        self._closed = False
        self._capabilities: dict[str, Capability] = {}
        self._outputs: dict = {}
        self._dirty = False
        self._has_snapshot = False
        self._observed_capabilities: dict[str, Capability] = {}
        self._observed_outputs: dict = {}
        self._snapshot_lock = threading.RLock()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OrchestrationError("MCP operation exceeded its deadline.", "provider_timeout", {"outcome": "not_started"})
        return remaining

    def _disconnect(self):
        if self._client is not None:
            self._client.close()
            self._client = None
        self._observed_capabilities = {}
        self._observed_outputs = {}
        # Keep the caller's authorized descriptors across reconnection. Newly
        # observed descriptors must still match the requested authorized tool.
        self._dirty = self._has_snapshot

    def _connection(self, deadline: float) -> JsonLineProcess:
        if self._closed:
            raise OrchestrationError("Capability provider is closed.", "provider_unavailable", {"outcome": "not_started"})
        if self._client is None:
            self._client = JsonLineProcess(self.options)
            try:
                result = self._client.request("initialize", {
                    "protocolVersion": self.version,
                    "capabilities": {},
                    "clientInfo": {"name": "xorchestrate", "version": __version__},
                }, timeout=self._remaining(deadline))
                if not isinstance(result, dict) or result.get("protocolVersion") not in self.SUPPORTED_VERSIONS:
                    raise OrchestrationError("MCP server negotiated an unsupported protocol version.", "provider_protocol")
                capabilities, info = result.get("capabilities"), result.get("serverInfo")
                if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
                    raise OrchestrationError("MCP server did not advertise tools support.", "provider_protocol")
                if not isinstance(info, dict) or not isinstance(info.get("name"), str) or not isinstance(info.get("version"), str):
                    raise OrchestrationError("MCP server returned invalid implementation information.", "provider_protocol")
                self._client.notify("notifications/initialized")
            except Exception:
                self._disconnect()
                raise
        return self._client

    def discover(self) -> list[Capability]:
        with self._snapshot_lock:
            return self._discover(time.monotonic() + self.timeout)

    def _discover(self, deadline: float) -> list[Capability]:
        capabilities, outputs, dirty = self._read_snapshot(deadline)
        self._capabilities = capabilities
        self._outputs = outputs
        self._has_snapshot = True
        self._observed_capabilities = capabilities
        self._observed_outputs = outputs
        self._dirty = dirty
        return copy.deepcopy(list(capabilities.values()))

    def _read_snapshot(self, deadline: float) -> tuple[dict[str, Capability], dict, bool]:
        try:
            client = self._connection(deadline)
            # Hints received before this explicit refresh belong to the old
            # snapshot. Hints received during pagination invalidate the new one.
            client.poll_notifications()
            definitions, cursors = [], set()
            received_bytes = 0
            params = {}
            for _ in range(self.max_pages):
                page = client.request("tools/list", params, timeout=self._remaining(deadline))
                if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
                    raise OrchestrationError("MCP tools/list returned an invalid page.", "provider_protocol")
                received_bytes += len(encode_json(page))
                if received_bytes > self.max_discovery:
                    raise OrchestrationError("MCP discovery exceeded its total output bound.", "provider_protocol")
                definitions.extend(page["tools"])
                if len(definitions) > self.maximum:
                    raise OrchestrationError("MCP discovery exceeded the configured tool count.", "provider_protocol")
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise OrchestrationError("MCP tools/list returned an invalid or repeated cursor.", "provider_protocol")
                cursors.add(cursor)
                params = {"cursor": cursor}
            else:
                raise OrchestrationError("MCP discovery exceeded the configured page count.", "provider_protocol")
            capabilities, outputs = _definitions(definitions, mcp=True, maximum=self.maximum)
            dirty = any(item["method"] == "notifications/tools/list_changed" for item in client.poll_notifications())
            return {capability.name: capability for capability in capabilities}, outputs, dirty
        except Exception:
            self._disconnect()
            raise

    def invoke(self, name: str, arguments: dict, context: dict) -> dict:
        # JSON-RPC on this connection is serialized already. Keep descriptor
        # selection consistent with that exchange while allowing the caller to
        # detect a different discovery between authorization and invocation.
        with self._snapshot_lock:
            return self._invoke(name, arguments, context)

    def _invoke(self, name: str, arguments: dict, context: dict) -> dict:
        deadline = time.monotonic() + self.timeout
        call_started = False
        try:
            if self._closed:
                raise OrchestrationError("Capability provider is closed.", "provider_unavailable", {"outcome": "not_started"})
            if not self._has_snapshot:
                self._discover(deadline)
            client = self._connection(deadline)
            notifications = client.poll_notifications()
            if any(item["method"] == "notifications/tools/list_changed" for item in notifications):
                self._dirty = True
            if self._dirty:
                # A catalog change does not invalidate unrelated capabilities.
                # Refresh observations without changing the descriptors the
                # caller explicitly discovered and authorized.
                self._observed_capabilities, self._observed_outputs, self._dirty = self._read_snapshot(deadline)
                if self._dirty:
                    raise OrchestrationError("MCP catalog changed during discovery; obtain a stable snapshot before invocation.", "capability_changed", {"outcome": "not_started"})
            capability = self._capabilities.get(name)
            if capability is None:
                raise OrchestrationError("Unknown capability.", "not_found", {"outcome": "not_started"})
            observed = self._observed_capabilities.get(name)
            if (observed is None or not _same_json(asdict(observed), asdict(capability))
                    or not _same_json(self._observed_outputs.get(name), self._outputs.get(name))):
                raise OrchestrationError("Requested capability changed; discover and authorize it again before invocation.", "capability_changed", {"outcome": "not_started"})
            _arguments(arguments, context, capability.input_schema)
            _expected_descriptor(capability, context)
            output_schema = self._outputs.get(name)
            call_started = True
            result = client.request("tools/call", {
                "name": name,
                "arguments": arguments,
                "_meta": {"io.xorchestrate/context": context},
            }, timeout=self._remaining(deadline))
            if not isinstance(result, dict):
                raise OrchestrationError("MCP tool result must contain a content list.", "provider_protocol", {"outcome": "unknown"})
            _content_blocks(result.get("content"))
            if "isError" in result and not isinstance(result["isError"], bool):
                raise OrchestrationError("MCP isError must be boolean.", "provider_protocol", {"outcome": "unknown"})
            if "structuredContent" in result and not isinstance(result["structuredContent"], dict):
                raise OrchestrationError("MCP structuredContent must be an object.", "provider_protocol", {"outcome": "unknown"})
            if result.get("isError", False):
                # MCP reports an execution error, not transactional rollback.
                # A tool may have applied part of its work before this result.
                raise OrchestrationError("MCP tool reported an execution error.", "capability_failed", {"outcome": "unknown", "result": result})
            if output_schema is not None:
                if "structuredContent" not in result:
                    raise OrchestrationError("MCP tool omitted its advertised structured output.", "provider_protocol", {"outcome": "unknown"})
                try:
                    validate(result["structuredContent"], output_schema)
                except OrchestrationError as exc:
                    raise OrchestrationError("MCP output does not match its advertised schema.", "provider_protocol", {"outcome": "unknown"}) from exc
            return result
        except OrchestrationError as exc:
            if not call_started:
                exc.details["outcome"] = "not_started"
            if exc.code in {"provider_protocol", "provider_timeout", "provider_unavailable"}:
                self._disconnect()
            raise

    def close(self) -> None:
        self._closed = True
        self._disconnect()
