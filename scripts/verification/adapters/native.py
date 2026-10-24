"""Native protocol decoding, separate from vendor-independent verification rules."""


class _Decoder:
    prefix = ""
    origin = ""

    def tool_source(self, source):
        return source.startswith(self.prefix)

    def decode(self, record, mode):
        item = record.get("observation", {})
        source, kind = item.get("source", ""), item.get("kind")
        if not isinstance(source, str):
            return []
        fixture = mode == "fixture" and source == "fixture"
        facts = []
        if kind in {"native_tool_call", "native_tool", "native_discovery"} and (fixture or self.tool_source(source)):
            facts.append({"event": "native_discovery" if kind == "native_discovery" else "native_tool",
                          "call_id": item.get("call_id"), "tool": item.get("action"),
                          "arguments": item.get("arguments"), "origin": self.origin})
        if not (fixture or source.startswith(self.prefix)):
            return facts
        if kind in {"activity", "native_completion", "native_completion_unknown", "turn_completed", "turn_failed"}:
            state = item.get("state") if kind == "activity" else item.get("activity")
            facts.append({"event": "activity", "state": state if state in {"idle", "active", "unknown"} else "unknown",
                          "basis": "native", "activity_kind": kind})
        if not fixture:
            facts.extend(self.turns(item))
        return facts

    def turns(self, item):
        return []


def _turn(phase, identity, *, anonymous=False, claims=None):
    return {"event": "native_turn", "phase": phase, "turn_id": identity,
            "anonymous": anonymous, "turn_claims": claims or {}}


def _claims(mapping, *keys):
    return {key: mapping[key] for key in keys if key in mapping}


class CodexDecoder(_Decoder):
    prefix, origin = "codex", "codex/item/tool/call"

    def turns(self, item):
        kind, packet = item.get("kind"), item.get("payload", {})
        if kind == "native_submission":
            result = item.get("result", {})
            return [_turn("submitted", result.get("turn", {}).get("id"))]
        if kind == "native_completion":
            return [_turn("end", item.get("turn_id"))]
        if kind == "native_notification" and item.get("method") in {"turn/started", "turn/completed"}:
            claims = _claims(packet, "turnId")
            if "id" in packet.get("turn", {}):
                claims["turn.id"] = packet["turn"]["id"]
            return [_turn("start" if item["method"] == "turn/started" else "end",
                          packet.get("turn", {}).get("id", packet.get("turnId")), claims=claims)]
        return []


class ClaudeDecoder(_Decoder):
    prefix, origin = "claude/", "claude/PreToolUse"

    def tool_source(self, source):
        return source == "claude/PreToolUse"

    def turns(self, item):
        kind, turn = item.get("kind"), item.get("turn_id")
        if kind in {"turn_started", "turn_completed", "turn_failed"}:
            return [_turn("start" if kind == "turn_started" else "end", turn, anonymous=turn in (None, ""))]
        if kind == "native_tool" and isinstance(turn, str) and turn:
            return [_turn("start", turn)]
        return []


class GrokDecoder(_Decoder):
    prefix, origin = "grok", "grok/session/update"

    def turns(self, item):
        kind, result = item.get("kind"), item.get("result", {})
        if kind == "native_submission":
            claims = _claims(item, "prompt_id")
            claims.update({"result." + key: value for key, value in _claims(result, "promptId").items()})
            if item.get("transport") == "grok/_x.ai/interject":
                # Acceptance creates outstanding work before any native start or
                # callback. A rejected interjection creates no such obligation.
                if result.get("accepted") is False:
                    return []
                claims.update(_claims(item, "interjection_id"))
                if "id" in result.get("turn", {}):
                    claims["result.turn.id"] = result["turn"]["id"]
                turn = item.get("interjection_id", result.get("turn", {}).get("id"))
            else:
                turn = item.get("prompt_id", result.get("promptId"))
            return [_turn("submitted", turn, claims=claims)]
        if kind == "native_completion":
            return [_turn("end", item.get("prompt_id"))]
        if kind != "native_notification":
            return []
        packet = item.get("payload", {})
        update = packet.get("update", {})
        if item.get("method") == "_x.ai/session/prompt_complete" or update.get("sessionUpdate") == "turn_completed":
            phase = "end"
        elif update.get("sessionUpdate") in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
            phase = "start"
        else:
            return []
        claims = _claims(packet, "promptId")
        claims.update({"update." + key: value for key, value in _claims(update, "prompt_id").items()})
        claims.update({"meta." + key: value for key, value in _claims(packet.get("_meta", {}), "promptId").items()})
        turn = packet.get("promptId", update.get("prompt_id", packet.get("_meta", {}).get("promptId")))
        return [_turn(phase, turn, claims=claims)]


def default_decoders():
    """Historical three-peer composition, replaceable with any decoder mapping."""
    return {"codex": CodexDecoder(), "claude": ClaudeDecoder(), "grok": GrokDecoder()}
