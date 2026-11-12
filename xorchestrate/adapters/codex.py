"""Codex app-server native session driver."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from ..model import JSON, Capability, OrchestrationError, public_error_code
from ..outcomes import before_effect, effect_outcome
from ..ports import ApplicationToolCallback
from .native_calls import call_fingerprint
from .process import decode_json, encode_json
from .process_sessions import LiveSession, NativeSessions
from .session_base import application_tools, json_object, native_identifier


def _tool_response(result: JSON, success: bool) -> JSON:
    return {"contentItems": [{"type": "inputText", "text": encode_json(result).decode("utf-8")}], "success": success}


class CodexSessionDriver(NativeSessions):
    """Codex app-server adapter, with explicit process and native policy config.

    ``command`` contains the complete argv (typically codex app-server --listen
    stdio://). ``thread`` holds configured thread/start parameters. ``resume``
    accepts an application-authorized stored backend. Requested ``parameters`` require
    an explicit ``request_parameters`` JSON Schema in trusted configuration;
    the default accepts an empty object only. Configured thread parameters win
    over delegated values. Native tools remain under
    the configured Codex sandbox/approval settings; this adapter does not silently
    disable native capabilities or implicitly approve requests.
    """

    protocol = "codex"
    application_callback_methods = ("item/tool/call",)
    jsonrpc = False
    resumable = True

    def _turn_started(self, live, turn_id):
        if not isinstance(turn_id, str) or not turn_id:
            live.activity_model.uncertain()
        elif turn_id not in live.completed:
            if len(live.pending) >= self.max_pending_messages and turn_id not in live.pending:
                live.activity_model.uncertain()
                raise OrchestrationError("Native turn observation bound reached.", "observation_overflow")
            live.pending[turn_id] = None
            if turn_id not in live.seen_turns:
                live.seen_turns.append(turn_id)
        self._activity(live)

    def __init__(self, options: JSON):
        super().__init__(options)
        self.thread = json_object(self.options.get("thread", {}), "Codex thread parameters", "invalid_configuration")
        self.turn = json_object(self.options.get("turn", {}), "Codex turn parameters", "invalid_configuration")
        permissions = json_object(self.options.get("native_permissions", {}), "Native permission responses", "invalid_configuration")
        if any(not isinstance(method, str) or not method or not isinstance(response, dict) for method, response in permissions.items()):
            raise OrchestrationError("Native permission responses must map methods to response objects.", "invalid_configuration")
        if "threadId" in self.thread or "threadId" in self.turn or "input" in self.turn:
            raise OrchestrationError("Native thread IDs and turn input belong to the active session.", "invalid_configuration")
        if "dynamic_tools" in self.options:
            self.set_tools(self.options["dynamic_tools"])

    def set_tools(self, tools: list[JSON]) -> None:
        with self._before_start("dynamic tools"):
            if not isinstance(tools, list):
                raise OrchestrationError("Dynamic tools must be an array.", "invalid_configuration")
            result = []
            names = set()
            for item in tools:
                tool = json_object(item, "Dynamic tool", "invalid_configuration")
                name = tool.get("name")
                if not isinstance(name, str) or not name or name in names or not isinstance(tool.get("description", ""), str):
                    raise OrchestrationError("Dynamic tools require unique names and string descriptions.", "invalid_configuration")
                schema = json_object(tool.get("inputSchema", {"type": "object"}), "Dynamic tool schema", "invalid_configuration")
                names.add(name)
                result.append({"type": "function", "name": name, "description": tool.get("description", ""), "inputSchema": schema})
            self.thread["dynamicTools"] = result

    def bind_application_tools(self, capabilities: list[Capability], callback: ApplicationToolCallback) -> None:
        tools = application_tools(capabilities, callback)
        with self._lock:
            configured = [tool for tool in self.thread.get("dynamicTools", []) if tool.get("name") not in self._application_tools]
            self.set_tools([*configured, *[
                {"name": item.name, "description": item.description, "inputSchema": item.input_schema}
                for item in tools.values()
            ]])
            self._application_tools = tools
            self._application_callback = callback

    def _application_request(self, live: LiveSession, params: JSON) -> JSON:
        with before_effect():
            arguments = params.get("arguments", {})
            if isinstance(arguments, str):
                arguments = decode_json(arguments.encode("utf-8"))
            arguments = json_object(arguments, "Tool arguments")
            key = None
            if "callId" in params:
                call_id = params["callId"]
                if not isinstance(call_id, str) or not call_id:
                    raise OrchestrationError("Native tool callId must be a nonempty string.")
                key = "codex:" + encode_json([live.native_id, call_id]).decode("utf-8")
            call_id = native_identifier(params.get("callId"), "native tool call ID")
            if params.get("threadId") != live.native_id:
                raise OrchestrationError("Application tools require an independently observed native thread ID.", "session_identity")
            trusted = self._callback_context(live, params["tool"], call_id)
            self._observation(live, "native_tool_call", call_id=call_id, action=params["tool"], arguments=arguments)
        result = self._application_callback(params["tool"], arguments, trusted, key)
        self._observation(live, "native_tool_result", call_id=call_id, action=params["tool"], arguments=arguments, result=result)
        return result

    def _request(self, live: LiveSession, method: str, params: Any) -> JSON:
        if method != "item/tool/call":
            configured = self.options.get("native_permissions", {})
            if method in configured:
                params = self._verified_request(live, params)
                self._observation(live, "native_permission", method=method, payload=params, decision=configured[method])
                return deepcopy(configured[method])
            return super()._request(live, method, params)
        claimed = False
        call_id = None
        callback_token = None
        completed = False
        try:
            params = self._verified_request(live, params)
            with live.lock:
                callback_token = uuid.uuid4().hex
                live.active_callbacks.add(callback_token)
                self._observation(live, "activity", state=self._activity(live))
            action = params.get("tool")
            call_id = params.get("callId")
            replay = None
            if call_id is not None:
                native_identifier(call_id, "tool call ID")
                with live.lock:
                    replay = live.calls.reserve(call_id, call_fingerprint(action, params.get("arguments", {})))
                    claimed = replay is None
            if replay is not None:
                completed = True
                if action in self._application_tools and self._application_replay:
                    arguments = params.get("arguments", {})
                    if isinstance(arguments, str):
                        arguments = decode_json(arguments.encode("utf-8"))
                    result = self._application_replay(action, json_object(arguments, "Tool arguments"),
                        self._callback_context(live, action, call_id),
                        "codex:" + encode_json([live.native_id, call_id]).decode("utf-8"))
                    return _tool_response(result, True)
                return replay["result"]
            if self._application_callback is not None and isinstance(action, str) and action in self._application_tools:
                result = self._application_request(live, params)
            else:
                result = super()._request(live, method, params)
            response = _tool_response(result, True)
            if claimed:
                with live.lock:
                    live.calls.complete(call_id, response)
                    try:
                        live.calls.succeed(call_id)
                    except Exception as error:
                        raise OrchestrationError("Callback completed but local settlement was refused.", "operation_unresolved",
                            {"outcome": "completed", "result": deepcopy(result), "cause": public_error_code(error)}) from error
            completed = True
            return response
        except OrchestrationError as exc:
            # A rejected duplicate/replay never resolves the original call. A
            # definitely pre-effect denial is terminal, unlike unknown effects.
            completed = not claimed or effect_outcome(exc) in {"failed", "not_started"}
            if not claimed:
                return _tool_response({"error": {"code": exc.code, "message": str(exc), "details": exc.details}}, False)
            with live.lock:
                return _tool_response({"error": live.calls.fail(call_id, exc)}, False)
        except Exception as exc:
            if claimed:
                with live.lock:
                    live.calls.fail(call_id, exc)
            raise
        finally:
            if callback_token is not None:
                with live.lock:
                    live.active_callbacks.discard(callback_token)
                    if not completed:
                        live.activity_model.uncertain("callback", callback_token)
                    self._observation(live, "activity", state=self._activity(live))

    def _launch(self, specification: JSON, resume: str | None) -> JSON:
        specification, requested = self._prepare_start(specification, ("threadId", "dynamicTools"))

        def handshake(live, spec):
            live.process.request("initialize", {"clientInfo": {"name": "xorchestrate", "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
            live.process.notify("initialized")
            params = {**requested, **self.thread}
            if resume is not None:
                live.native_id = native_identifier(resume, "resume ID")
                params["threadId"] = live.native_id
            result = json_object(live.process.request("thread/resume" if resume is not None else "thread/start", params), "Codex thread result", "provider_protocol")
            thread = json_object(result.get("thread"), "Codex thread", "provider_protocol")
            native_id = native_identifier(thread.get("id"))
            if resume is not None and native_id != resume:
                raise OrchestrationError("Resumed thread identity changed.", "session_identity")
            live.native_id = native_id
            observed = {key: result.get(key, thread.get(key)) for key in ("model", "modelProvider", "reasoningEffort")}
            live.settings = {"requested": requested, "resolved": deepcopy(params), "observed": observed,
                             "precedence": "trusted_configuration"}
        return self._start(specification, handshake)

    def _notification(self, live: LiveSession, method: str, params: JSON) -> None:
        super()._notification(live, method, params)
        turn = params.get("turn", {})
        turn_id = turn.get("id", params.get("turnId"))
        with live.lock:
            if method == "turn/started":
                if isinstance(turn_id, str) and turn_id in live.completed:
                    self._observation(live, "stale_native_activity", turn_id=turn_id, method=method)
                    return
                self._turn_started(live, turn_id)
                self._observation(live, "activity", state=live.activity, turn_id=turn_id)
            elif method == "turn/completed":
                identified = isinstance(turn_id, str) and bool(turn_id)
                stale = identified and turn_id in live.completed
                if identified and turn_id in live.pending:
                    live.pending.pop(turn_id)
                elif not stale and identified and live.submissions:
                    live.unbound_completions.add(turn_id)
                elif not stale:
                    # A duplicate can be ignored; an unbound completion cannot
                    # establish that all observed work ended. A response may
                    # legitimately race its in-flight submission registration.
                    live.activity_model.uncertain()
                if identified and not stale:
                    live.completed.append(turn_id)
                if turn.get("status") in {"failed", "interrupted"} or turn.get("error"):
                    live.activity_model.uncertain()
                self._activity(live)
                self._observation(live, "native_completion", turn_id=turn_id, stale=stale, result=turn, activity=live.activity)
                self._observation(live, "activity", state=live.activity, turn_id=turn_id)
            elif method == "thread/configuration/updated":
                live.settings["observed"].update({key: params[key] for key in ("model", "modelProvider", "reasoningEffort") if key in params})

    def _submit(self, live, parameters):
        token = uuid.uuid4().hex
        with live.lock:
            live.submissions.add(token)
            self._observation(live, "activity", state=self._activity(live))
        try:
            result = json_object(live.process.request("turn/start", parameters), "Codex turn result", "provider_protocol")
            with live.lock:
                turn_id = result.get("turn", {}).get("id")
                if isinstance(turn_id, str):
                    live.unbound_completions.discard(turn_id)
                self._turn_started(live, turn_id)
            return result
        except Exception as exc:
            with live.lock:
                if effect_outcome(exc) not in {"failed", "not_started"}:
                    live.activity_model.uncertain("submission", token)
            raise
        finally:
            with live.lock:
                live.submissions.discard(token)
                if not live.submissions and live.unbound_completions:
                    live.activity_model.uncertain()
                    live.unbound_completions.clear()
                self._observation(live, "activity", state=self._activity(live))

    def send(self, session: JSON, message: JSON) -> JSON:
        message = json_object(message, "Session message")
        text = message.get("text")
        if text is None:
            text = encode_json(message).decode("utf-8")
        if not isinstance(text, str):
            raise OrchestrationError("Session message text must be a string.", details={"outcome": "not_started"})
        live = self._get(session)
        result = self._submit(live, {**self.turn, "threadId": live.native_id, "input": [{"type": "text", "text": text}]})
        self._observation(live, "native_submission", transport="codex/turn/start", result=result)
        return {**result, "submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "transport": "codex/turn/start", "completion": "pending"}

    def deliver(self, session: JSON, message: JSON) -> JSON:
        live = self._get(session)
        message = json_object(message, "Delivery envelope")
        result = self._submit(live, {
            **self.turn, "threadId": live.native_id, "input": [],
            "toolOutput": {"name": "peer_message_received", "namespace": None,
                           "output": encode_json(message).decode("utf-8")},
        })
        self._observation(live, "native_submission", transport="codex/turn/start.toolOutput", message=message, result=result)
        return {"submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "transport": "codex/turn/start.toolOutput", "result": result, "completion": "pending"}


