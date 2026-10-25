"""Grok Build ACP delivery and native MCP observations.

The prompt/interject split, epoch fencing, and canonical tool metadata handling
adapt the retained Agentscient and multi-harness proof mechanisms. No historical
role, tool restriction or evaluator policy is imported.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import deque
from copy import deepcopy

from ..model import Capability, OrchestrationError
from .jsonrpc import encode_json
from .sessions import ACPSessionDriver, _identifier, _object


class GrokSessionDriver(ACPSessionDriver):
    protocol = "grok"

    def _activity(self, live):
        state = getattr(live, "grok", {})
        live.activity = ("active" if live.pending or live.active_callbacks or live.submissions
                         or state.get("native_busy") or state.get("interjections") else
                         "unknown" if live.activity_uncertain else "idle")
        return live.activity

    def _native_prompt_started(self, live, prompt):
        state = live.grok
        if not isinstance(prompt, str) or not prompt:
            live.activity_uncertain = True
        elif prompt not in state["completed"]:
            if len(state["active_prompts"]) >= self.max_pending_messages and prompt not in state["active_prompts"]:
                live.activity_uncertain = True
                raise OrchestrationError("Native prompt observation bound reached.", "observation_overflow")
            state["active_prompts"].add(prompt)
            if prompt not in state["seen"]:
                state["seen"].append(prompt)
                state["current"] = prompt
        state["native_busy"] = bool(state["active_prompts"])

    def _native_prompt_finished(self, live, prompt, completed):
        state = live.grok
        identified = isinstance(prompt, str) and bool(prompt)
        duplicate = identified and prompt in state["completed"]
        if not identified or (prompt not in state["active_prompts"] and not duplicate):
            live.activity_uncertain = True
        if identified:
            state["active_prompts"].discard(prompt)
            if not duplicate:
                state["completed"].append(prompt)
        if not completed:
            live.activity_uncertain = True
        state["native_busy"] = bool(state["active_prompts"])
        return duplicate

    def __init__(self, options, request_handler=None):
        super().__init__(options, request_handler)
        self._application_tools = {}
        self._application_callback = None
        self.server_name = self.options.get("application_server", "xorchestrate")
        if not isinstance(self.server_name, str) or not self.server_name or "__" in self.server_name:
            raise OrchestrationError("Grok application_server must be an unambiguous name.", "invalid_configuration")
        self.permissions = _object(self.options.get("native_permissions", {}), "Native permissions", "invalid_configuration")
        if self.permissions.get("default", "deny") not in {"allow", "deny", "delegate"}:
            raise OrchestrationError("Grok native permission default must be allow, deny or delegate.", "invalid_configuration")
        for key in ("allow", "deny"):
            if not isinstance(self.permissions.get(key, []), list) or any(not isinstance(x, str) for x in self.permissions.get(key, [])):
                raise OrchestrationError("Native permission lists must contain tool names.", "invalid_configuration")

    def bind_application_tools(self, capabilities, callback):
        if not callable(callback) or not isinstance(capabilities, list) or any(not isinstance(c, Capability) for c in capabilities):
            raise OrchestrationError("Application tools require descriptors and callback.", "invalid_configuration")
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind tools before starting sessions.", "conflict")
            self._application_tools = {c.name: deepcopy(c) for c in capabilities}
            self._application_callback = callback

    def _tool_name(self, wire):
        if not isinstance(wire, str):
            return None
        for prefix in ("mcp__" + self.server_name + "__", self.server_name + "__"):
            if wire.startswith(prefix) and wire[len(prefix):] in self._application_tools:
                return wire[len(prefix):]
        return None

    def _tool_observation(self, update):
        meta = _object(update.get("_meta", {}), "Grok tool metadata", "provider_protocol")
        identity = meta.get("x.ai/tool")
        wire = update.get("title")
        basis = "exact ACP title"
        if identity is not None:
            if not isinstance(identity, dict) or type(identity.get("version")) is not int or identity["version"] != 1:
                raise OrchestrationError("Unsupported Grok canonical tool metadata.", "provider_protocol")
            wire = identity.get("name")
            basis = "x.ai/tool v1"
            if wire in {"search_tool", "use_tool"} and identity.get("namespace") != "grok_build":
                raise OrchestrationError("Conflicting Grok tool namespace.", "provider_protocol")
            if self._tool_name(wire) and identity.get("namespace") != "mcp":
                raise OrchestrationError("Conflicting Grok MCP namespace.", "provider_protocol")
            title_name = self._tool_name(update.get("title"))
            if title_name and title_name != self._tool_name(wire):
                raise OrchestrationError("Canonical tool identity conflicts with exact title.", "provider_protocol")
        args = update.get("rawInput")
        if wire == "search_tool":
            return "native_discovery", wire, args, basis
        if wire == "use_tool":
            if not isinstance(args, dict) or set(args) != {"tool_name", "tool_input"}:
                raise OrchestrationError("Grok dispatch requires exact target and input.", "provider_protocol")
            name = self._tool_name(args["tool_name"])
            if not name:
                return "native_action", wire, args, basis
            return "native_tool_call", name, _object(args["tool_input"], "Tool input", "provider_protocol"), basis
        name = self._tool_name(wire)
        return ("native_tool_call" if name else "native_action", name or wire, args, basis)

    def _notification(self, live, method, params):
        super()._notification(live, method, params)
        if not hasattr(live, "grok") or live.native_id is None:
            return
        update = params.get("update", {})
        kind = update.get("sessionUpdate")
        prompt = params.get("promptId", update.get("prompt_id", params.get("_meta", {}).get("promptId")))
        state = live.grok
        with live.lock:
            completion = None
            if method == "_x.ai/session/prompt_complete" or kind == "turn_completed":
                if not isinstance(prompt, str) or not prompt:
                    live.activity_uncertain = True
                    self._activity(live)
                    self._observation(live, "native_completion_unknown", reason="missing_native_prompt_id", activity=live.activity)
                    self._observation(live, "activity", state=live.activity)
                    return
                stale = self._native_prompt_finished(live, prompt, True)
                state["interjections"].pop(prompt, None)
                completion = {"prompt_id": prompt, "stale": stale, "result": params}
            elif kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
                if isinstance(prompt, str) and prompt in state["completed"]:
                    self._observation(live, "stale_native_activity", prompt_id=prompt, payload=params)
                    return
                self._native_prompt_started(live, prompt)
            if kind in {"config_option_update", "current_mode_update", "available_commands_update"}:
                live.settings["observed"][kind] = deepcopy(update)
            if kind == "tool_call":
                event_kind, action, args, basis = self._tool_observation(update)
                call_id = _identifier(update.get("toolCallId"), "native tool call ID")
                signature = hashlib.sha256(encode_json([event_kind, action, args], sort_keys=True)).hexdigest()
                previous = state["calls"].get(call_id)
                if previous and previous["signature"] != signature:
                    raise OrchestrationError("Grok call ID was reused for a conflicting execution.", "call_id_conflict")
                if not previous:
                    if len(state["calls"]) >= self.max_native_calls:
                        raise OrchestrationError("Native replay ledger is full.", "session_capacity")
                    state["calls"][call_id] = {"signature": signature, "action": action, "arguments": deepcopy(args), "used": False}
                    self._observation(live, event_kind, call_id=call_id, action=action, arguments=args,
                                      identity_basis=basis, wire_tool=update.get("title"), prompt_id=prompt)
                    # A correlated application acknowledgement/reply establishes
                    # handling of this interjection; discovery alone never does.
                    if event_kind == "native_tool_call" and isinstance(args, dict):
                        for ident, envelope in list(state["interjections"].items()):
                            if args.get("attempt_id") == envelope.get("attempt_id") and envelope.get("attempt_id"):
                                state["interjections"].pop(ident, None)
                state["condition"].notify_all()
            self._activity(live)
            if completion is not None:
                self._observation(live, "native_completion", activity=live.activity, **completion)
            if completion is not None or kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
                self._observation(live, "activity", state=live.activity, prompt_id=prompt)

    def _request(self, live, method, params):
        if method != "session/request_permission":
            return super()._request(live, method, params)
        params = self._verified_request(live, params)
        call = params.get("toolCall", {})
        canonical = call.get("_meta", {}).get("x.ai/tool", {})
        name = canonical.get("name", call.get("title"))
        allowed = name in self.permissions.get("allow", []) or self.permissions.get("default") == "allow"
        if name in self.permissions.get("deny", []):
            allowed = False
        if self.permissions.get("default") == "delegate" and name not in self.permissions.get("allow", []) + self.permissions.get("deny", []):
            return super()._request(live, method, params)
        option = next((o for o in params.get("options", []) if o.get("kind") == ("allow_once" if allowed else "reject_once")), None)
        result = {"outcome": {"outcome": "selected", "optionId": option["optionId"]} if option else {"outcome": "cancelled"}}
        self._observation(live, "native_permission", action=name, allowed=allowed, call_id=call.get("toolCallId"), result=result)
        return result

    def _gateway(self, live, action, payload):
        if action != "tools/call":
            return self._gateway_call(live, action, payload)
        token = uuid.uuid4().hex
        completed = False
        with live.lock:
            live.active_callbacks.add(token)
            self._observation(live, "activity", state=self._activity(live))
        try:
            result = self._gateway_call(live, action, payload)
            completed = True
            return result
        finally:
            with live.lock:
                live.active_callbacks.discard(token)
                if not completed:
                    live.activity_uncertain = True
                self._observation(live, "activity", state=self._activity(live))

    def _gateway_call(self, live, action, payload):
        if action == "tools/list":
            return {"tools": [{"name": c.name, "description": c.description, "inputSchema": c.input_schema} for c in self._application_tools.values()]}
        if action == "initialized":
            return {}
        if action != "tools/call":
            raise OrchestrationError("Unsupported Grok gateway action.", "unsupported_method")
        name, arguments = payload.get("name"), _object(payload.get("arguments", {}), "Tool arguments")
        if name not in self._application_tools or self._application_callback is None:
            raise OrchestrationError("Unknown application tool.", "unknown_action")
        if live.native_id is None:
            raise OrchestrationError("Native conversation identity is not established.", "session_identity")
        request_key = encode_json([payload.get("mcp_connection_id"), payload.get("mcp_request_id")]).decode()
        if not payload.get("mcp_connection_id") or payload.get("mcp_request_id") is None:
            raise OrchestrationError("MCP request lacks a connection-scoped identity.", "missing_call_identity")
        fingerprint = hashlib.sha256(encode_json([name, arguments], sort_keys=True)).hexdigest()
        replay = None
        with live.lock:
            prior = live.grok["mcp_requests"].get(request_key)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise OrchestrationError("MCP request ID was reused with different arguments.", "call_id_conflict")
                if prior["state"] == "completed":
                    replay = deepcopy(prior)
                else:
                    raise OrchestrationError("MCP request already has an unresolved invocation.", "call_in_progress")
            else:
                if len(live.grok["mcp_requests"]) >= self.max_native_calls:
                    raise OrchestrationError("MCP replay ledger is full.", "session_capacity")
                live.grok["mcp_requests"][request_key] = {"fingerprint": fingerprint, "state": "pending"}
        if replay is not None:
            if self._application_replay:
                return self._application_replay(name, deepcopy(arguments), deepcopy(replay["context"]), replay["key"])
            return replay["result"]
        deadline = time.monotonic() + self.options.get("observation_timeout", 10)
        with live.grok["condition"]:
            while True:
                matches = [(ident, row) for ident, row in live.grok["calls"].items()
                           if row["action"] == name and row["arguments"] == arguments and not row["used"]]
                if matches:
                    # ACP observations carry native IDs; MCP requests do not.
                    # Exact equal inputs are semantically indistinguishable, so
                    # consume them one-to-one in observed order and retain the
                    # association basis instead of inventing stronger identity.
                    eligible = [ident for ident, _ in matches]
                    call_id, row = matches[0]
                    row["used"] = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or live.closed:
                    raise OrchestrationError("MCP execution has no matching native tool observation.", "missing_native_observation")
                live.grok["condition"].wait(min(remaining, .1))
        trusted = self._callback_context(live, name, call_id)
        trusted["correlation_basis"] = "exact_action_arguments_observed_order"
        self._observation(live, "native_mcp_association", call_id=call_id, action=name, arguments=arguments,
                          eligible_native_call_ids=eligible, mcp_connection_id=payload["mcp_connection_id"],
                          mcp_request_id=payload["mcp_request_id"], correlation_basis=trusted["correlation_basis"])
        result = self._application_callback(name, arguments, trusted, "grok:" + encode_json([live.native_id, call_id]).decode())
        self._observation(live, "native_tool_result", action=name, arguments=arguments, call_id=call_id, result=result)
        with live.lock:
            live.grok["mcp_requests"][request_key] = {"fingerprint": fingerprint, "state": "completed", "result": deepcopy(result),
                "context": deepcopy(trusted), "key": "grok:" + encode_json([live.native_id, call_id]).decode()}
        return result

    def _launch(self, specification, resume):
        specification, requested = self._prepare_start(specification, ("sessionId",))
        def handshake(live, spec):
            live.grok = {"current": None, "native_busy": False, "active_prompts": set(),
                         "seen": deque(maxlen=1024), "completed": deque(maxlen=1024),
                         "calls": {}, "mcp_requests": {}, "interjections": {}, "condition": threading.Condition(live.lock)}
            params = {**requested, **deepcopy(self.session)}
            if self._application_callback:
                from .native_gateway import LocalGateway
                live.gateway = LocalGateway(lambda action, payload: self._gateway(live, action, payload), self.options)
                server = {"name": self.server_name, **live.gateway.mcp_spec(), "env": []}
                params["mcpServers"] = [*params.get("mcpServers", []), server]
            result = _object(live.process.request("initialize", self.initialize), "Grok initialization", "provider_protocol")
            capabilities = result.get("agentCapabilities", {})
            methods = [m.get("id") for m in result.get("authMethods", [])]
            authentication = self.authentication
            if authentication is None and methods:
                method = self.options.get("authentication_method", "cached_token")
                if method not in methods:
                    raise OrchestrationError("Configured Grok authentication method is unavailable.", "configuration_error", {"outcome": "not_started"})
                authentication = {"methodId": method, "_meta": {"headless": True}}
            if authentication is not None:
                live.process.request("authenticate", authentication)
            if resume is not None:
                if not self.resumable or capabilities.get("loadSession") is not True:
                    raise OrchestrationError("Grok did not advertise session loading.", "unsupported_operation", {"outcome": "not_started"})
                live.native_id = resume
                params["sessionId"] = resume
                result = _object(live.process.request("session/load", params), "Grok load result", "provider_protocol")
            else:
                result = _object(live.process.request("session/new", params), "Grok start result", "provider_protocol")
                live.native_id = _identifier(result.get("sessionId"))
            live.settings = {"requested": requested,
                             "resolved": {**{k: v for k, v in params.items() if k != "mcpServers"},
                                          "launch_command": deepcopy(self.options["command"])},
                             "observed": {"model": result.get("model"), "reasoning": result.get("reasoning"), "provider": result.get("provider"),
                                          **{key: deepcopy(result[key]) for key in ("configOptions", "models", "modes") if key in result}},
                             "precedence": "trusted_configuration"}
            self._observation(live, "native_configuration", method="session/load" if resume else "session/new", result=result)
        return self._start(specification, handshake)

    def _prompt_started(self, live, prompt_id):
        self._native_prompt_started(live, prompt_id)

    def _prompt_finished(self, live, prompt_id, completed):
        self._native_prompt_finished(live, prompt_id, completed)
        self._activity(live)

    def send(self, session, message):
        return self.deliver(session, message)

    def deliver(self, session, message):
        message = _object(message, "Delivery envelope")
        live = self._get(session)
        with live.lock:
            busy = bool(live.pending or live.active_callbacks or live.grok["native_busy"] or live.grok["interjections"])
            if busy:
                if len(live.grok["interjections"]) >= self.options.get("max_pending_messages", 1024):
                    raise OrchestrationError("Outstanding interjection capacity reached.", "session_capacity", {"outcome": "not_started"})
                ident = message.get("attempt_id") or uuid.uuid4().hex
                live.grok["interjections"][ident] = deepcopy(message)
        if not busy:
            return super().send(session, message)
        result = _object(live.process.request("_x.ai/interject", {"sessionId": live.native_id,
            "text": message.get("text", encode_json(message).decode()), "interjectionId": ident}), "Grok interjection result", "provider_protocol")
        self._observation(live, "native_submission", transport="grok/_x.ai/interject", message=message, interjection_id=ident, result=result)
        return {"submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "transport": "grok/_x.ai/interject", "interjection_id": ident, "result": result, "completion": "pending"}

    def stop(self, session):
        live = self._get(session)
        try:
            return super().stop(session)
        finally:
            if hasattr(live, "gateway"):
                live.gateway.close()

    def close(self):
        with self._lock:
            gateways = [live.gateway for live in self._sessions.values() if hasattr(live, "gateway")]
        try:
            super().close()
        finally:
            for gateway in gateways:
                gateway.close()
