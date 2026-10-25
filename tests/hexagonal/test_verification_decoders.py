"""Concrete verification adapters: wire aliases, provenance and composition.

Shared completion/identity contracts define behavioral expectations elsewhere;
these tests pin each concrete protocol translation at the adapter boundary.
"""
from copy import deepcopy

import pytest

from scripts.verification.adapters.native import ClaudeDecoder, CodexDecoder, GrokDecoder
from scripts.verification.lifecycle import _native_tool_matches


@pytest.mark.parametrize("decoder,source", [(CodexDecoder(), "codex"), (ClaudeDecoder(), "claude/PreToolUse"),
                                             (GrokDecoder(), "grok/session/update")])
def test_native_decoder_keeps_input_and_rejects_another_protocol_source(decoder, source):
    record = {"observation": {"kind": "native_tool", "source": source, "call_id": "call",
                               "action": "record_result", "arguments": {"result": {"opaque": {"actor_id": "payload"}}}}}
    original = deepcopy(record)
    facts = decoder.decode(record, "protocol-fixture")
    assert facts == [{"event": "native_tool", "call_id": "call", "tool": "record_result",
                      "arguments": original["observation"]["arguments"], "origin": decoder.origin}]
    assert record == original
    record["observation"]["source"] = "unselected-protocol/native"
    assert decoder.decode(record, "protocol-fixture") == []


def test_interjection_acceptance_precedes_native_start_and_retains_conflicting_aliases():
    decoder = GrokDecoder()
    item = {"kind": "native_submission", "source": "grok", "transport": "grok/_x.ai/interject",
            "interjection_id": "accepted-B", "result": {"accepted": True, "turn": {"id": "other-turn"}}}
    facts = decoder.decode({"observation": item}, "protocol-fixture")
    assert facts == [{"event": "native_turn", "phase": "submitted", "turn_id": "accepted-B", "anonymous": False,
                      "turn_claims": {"interjection_id": "accepted-B", "result.turn.id": "other-turn"}}]
    item["result"]["accepted"] = False
    assert decoder.decode({"observation": item}, "protocol-fixture") == []


def test_lifecycle_probe_selects_native_facts_through_injected_decoder():
    class CustomDecoder:
        def decode(self, record, mode):
            assert mode == "custom-mode"
            if record == {"custom_packet": "retained"}:
                return [{"event": "native_tool", "tool": "record_result", "arguments": {"result": {"memory": "retained"}}}]
            return []

    decoder = CustomDecoder()
    assert _native_tool_matches(decoder, "custom-mode", {"custom_packet": "retained"}, "record_result", {"memory": "retained"})
    assert not _native_tool_matches(decoder, "custom-mode", {"custom_packet": "unknown"}, "record_result", {"memory": "retained"})
    assert not _native_tool_matches(decoder, "custom-mode", {"custom_packet": "retained"}, "another_action", {"memory": "retained"})
