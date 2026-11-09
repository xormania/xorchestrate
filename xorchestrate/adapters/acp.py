"""Agent Client Protocol native session driver."""

from __future__ import annotations

import uuid
from copy import deepcopy

from ..model import JSON, OrchestrationError
from ..outcomes import effect_outcome
from .process import encode_json
from .process_sessions import NativeSessions
from .session_base import json_object, native_identifier


class ACPSessionDriver(NativeSessions):
    """Agent Client Protocol over configured stdio, without vendor CLI assumptions.

    ACP prompt responses complete a turn. Send acknowledges the written request;
    the independent native response is retained as a completion event. Streamed
    notifications are retained for poll while callbacks and control progress.
    Native filesystem/terminal client requests require an explicitly bound
    handler; no filesystem or shell capability is advertised by default.
    Native ``session`` settings, including MCP server commands, come from trusted
    configuration. Requested parameters require explicit delegation through the
    configured ``request_parameters`` JSON Schema and are validated before spawn.
    """

    protocol = "acp"

    def __init__(self, options: JSON):
        super().__init__(options)
        self.session = json_object(self.options.get("session", {}), "ACP session parameters", "invalid_configuration")
        self.initialize = json_object(self.options.get("initialize", {"protocolVersion": 1, "clientCapabilities": {}}), "ACP initialization", "invalid_configuration")
        self.authentication = self.options.get("authentication")
        if self.authentication is not None:
            self.authentication = json_object(self.authentication, "ACP authentication", "invalid_configuration")
        self.resumable = self.options.get("resume_supported", False)
        if not isinstance(self.resumable, bool):
            raise OrchestrationError("resume_supported must be boolean.", "invalid_configuration")
        if "sessionId" in self.session:
            raise OrchestrationError("Native session IDs belong to the active session.", "invalid_configuration")

    def _launch(self, specification: JSON, resume: str | None) -> JSON:
        specification, requested = self._prepare_start(specification, ("sessionId",))

        def handshake(live, spec):
            initialized = json_object(live.process.request("initialize", self.initialize), "ACP initialization result", "provider_protocol")
            capabilities = json_object(initialized.get("agentCapabilities", {}), "ACP agent capabilities", "provider_protocol")
            if self.authentication is not None:
                live.process.request("authenticate", self.authentication)
            params = {**requested, **self.session}
            if resume is not None:
                if not self.resumable or capabilities.get("loadSession") is not True:
                    raise OrchestrationError("ACP session loading is not enabled and advertised by this agent.", "unsupported_operation", {"outcome": "not_started"})
                live.native_id = native_identifier(resume, "resume ID")
                params["sessionId"] = live.native_id
                json_object(live.process.request("session/load", params), "ACP session load result", "provider_protocol")
            else:
                result = json_object(live.process.request("session/new", params), "ACP session result", "provider_protocol")
                live.native_id = native_identifier(result.get("sessionId"))
            live.settings = {"requested": requested, "resolved": deepcopy(params),
                             "observed": {"model": None, "reasoning": None, "provider": None},
                             "precedence": "trusted_configuration"}
            live.settings["observed"].update({key: result[key] for key in ("model", "reasoning", "provider", "configOptions", "models", "modes") if resume is None and key in result})
        return self._start(specification, handshake)

    def _prompt_started(self, live, prompt_id):
        pass

    def _prompt_finished(self, live, prompt_id, completed):
        if not completed:
            live.activity_model.uncertain("prompt", prompt_id)
        self._activity(live)

    def send(self, session: JSON, message: JSON) -> JSON:
        message = json_object(message, "Session message")
        text = message.get("text", encode_json(message).decode("utf-8"))
        if not isinstance(text, str):
            raise OrchestrationError("Session message text must be a string.", details={"outcome": "not_started"})
        live = self._get(session)
        with live.lock:
            if live.pending:
                raise OrchestrationError("This ACP driver has an outstanding prompt and no interjection contract.", "session_busy", {"outcome": "not_started"})
            prompt_id = uuid.uuid4().hex
            live.pending[prompt_id] = None
            self._activity(live)
            self._prompt_started(live, prompt_id)
        try:
            future = live.process.request_async("session/prompt", {
                "sessionId": live.native_id, "prompt": [{"type": "text", "text": text}],
                "_meta": {"promptId": prompt_id},
            }, timeout=self.options.get("turn_timeout", 3600))
        except Exception as exc:
            with live.lock:
                live.pending.pop(prompt_id, None)
                self._prompt_finished(live, prompt_id, effect_outcome(exc) in {"failed", "not_started"})
                self._observation(live, "activity", state=self._activity(live))
            raise
        with live.lock:
            live.pending[prompt_id] = future
        self._observation(live, "native_submission", transport=self.protocol + "/session/prompt", prompt_id=prompt_id, message=message)

        def completed(result):
            try:
                value = json_object(result.result(), "ACP prompt result", "provider_protocol")
                if not isinstance(value.get("stopReason"), str) or not value["stopReason"]:
                    raise OrchestrationError("ACP prompt did not return a stop reason.", "provider_protocol", {"outcome": "unknown"})
                kind, fields = "native_completion", {"result": value}
            except Exception as exc:
                kind, fields = "native_completion_unknown", {
                    "error": exc.code if isinstance(exc, OrchestrationError) else type(exc).__name__, "outcome": "unknown"}
            with live.lock:
                live.pending.pop(prompt_id, None)
                self._prompt_finished(live, prompt_id, kind == "native_completion")
                event = self._observation(live, kind, prompt_id=prompt_id, activity=live.activity, **fields)
                self._observation(live, "activity", state=live.activity, prompt_id=prompt_id)
                if len(live.events) >= self.options.get("max_pending_messages", 1024):
                    live.process.close()
                    return
                live.events.append(event)
        future.add_done_callback(completed)
        return {"submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "prompt_id": prompt_id, "transport": self.protocol + "/session/prompt", "completion": "pending"}
