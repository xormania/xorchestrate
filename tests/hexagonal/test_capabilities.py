"""Capability integration tests cross real subprocess boundaries."""

import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from xorchestrate.adapters.capabilities import CLIProvider, StdioMCPProvider
from xorchestrate.adapters.jsonrpc import JsonLineProcess
from xorchestrate.model import OrchestrationError

FIXTURE = Path(__file__).parent / "fixtures" / "capability_server.py"


def definition(name="project.custom"):
    return {
        "name": name,
        "description": "External description, not policy.",
        "input_schema": {
            "type": "object", "properties": {"value": {"type": "string"}, "mode": {"type": "string"}},
            "required": ["value"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "authority": "owner"},
    }


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.definitions = self.root / "tools.json"
        self.marker = self.root / "calls"
        self.write_definitions([definition()])

    def write_definitions(self, items):
        self.definitions.write_text(json.dumps(items))

    def argv(self, transport, *extra):
        return [sys.executable, str(FIXTURE), transport, "--definitions", str(self.definitions), *extra]

    def mcp(self, **options):
        provider = StdioMCPProvider({"command": self.argv("mcp", "--marker", str(self.marker)), "timeout": 10, **options})
        self.addCleanup(provider.close)
        return provider

    def cli(self, dynamic=False, **options):
        config = {"command": self.argv("cli", "--marker", str(self.marker)), "timeout": 10}
        config.update({"discovery_command": self.argv("discover")} if dynamic else {"capabilities": [definition()]})
        provider = CLIProvider({**config, **options})
        self.addCleanup(provider.close)
        return provider

    def assert_code(self, code, action):
        with self.assertRaises(OrchestrationError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_mcp_negotiation_pagination_and_dynamic_new_tool(self):
        self.write_definitions([definition(), definition("additional.tool")])
        provider = self.mcp()
        self.assertEqual([item.name for item in provider.discover()], ["project.custom", "additional.tool"])
        self.write_definitions([definition("brand.new")])
        error = self.assert_code("not_found", lambda: provider.invoke("brand.new", {"value": "a"}, {}))
        self.assertEqual(error.details["outcome"], "not_started")
        self.assertFalse(self.marker.exists())
        self.assertEqual([item.name for item in provider.discover()], ["brand.new"])
        result = provider.invoke("brand.new", {"value": "a"}, {"project": "new project"})
        self.assertEqual(result["structuredContent"]["name"], "brand.new")
        self.assertEqual(result["structuredContent"]["context"], {"project": "new project"})

    def test_cli_manifest_and_dynamic_discovery_share_call_contract(self):
        for dynamic in (False, True):
            with self.subTest(dynamic=dynamic):
                provider = self.cli(dynamic)
                self.assertEqual(provider.discover()[0].name, "project.custom")
                self.assertEqual(provider.invoke("project.custom", {"value": "x"}, {"role": "any"})["context"], {"role": "any"})
        self.write_definitions([definition("future.capability")])
        provider = self.cli(True)
        self.assertEqual(provider.invoke("future.capability", {"value": "x"}, {})["name"], "future.capability")

    def test_invalid_arguments_never_reach_executable(self):
        for provider in (self.cli(), self.mcp()):
            with self.subTest(provider=type(provider).__name__):
                with self.assertRaises(OrchestrationError) as caught:
                    provider.invoke("project.custom", {"value": 3}, {})
                self.assertEqual(caught.exception.details["outcome"], "not_started")
                self.assertFalse(self.marker.exists())

    def test_discovery_return_does_not_mutate_cached_schema(self):
        for provider in (self.cli(), self.mcp()):
            capabilities = provider.discover()
            capabilities[0].input_schema.clear()
            with self.assertRaises(OrchestrationError):
                provider.invoke("project.custom", {"value": 3}, {})
        self.assertFalse(self.marker.exists())

    def test_command_arguments_remain_literal_and_environment_is_explicit(self):
        marker = self.root / "must-not-exist"
        literal = f"$(touch {marker}); echo injected"
        tool = definition()
        tool["command"] = ["nonexistent-malicious-program"]
        self.write_definitions([tool])
        with patch.dict(os.environ, {"CAPABILITY_AMBIENT_SECRET": "must-not-leak"}):
            provider = self.cli(True, command=self.argv("cli", "--literal", literal), env={"CAPABILITY_TEST": "configured"}, cwd=str(self.root))
            result = provider.invoke("project.custom", {"value": literal}, {})
        self.assertEqual(result["literal"], literal)
        self.assertEqual(result["arguments"]["value"], literal)
        self.assertEqual(result["env"], "configured")
        self.assertIsNone(result["ambient"])
        self.assertEqual(result["cwd"], str(self.root))
        self.assertFalse(marker.exists())

    def test_mcp_server_requests_and_notifications_do_not_grant_authority(self):
        result = self.mcp().invoke("project.custom", {"value": "x", "mode": "server_requests"}, {})
        self.assertEqual(result["structuredContent"]["arguments"]["mode"], "server_requests")

    def test_explicit_environment_forwarding_and_override(self):
        with patch.dict(os.environ, {"CAPABILITY_TEST": "forwarded", "CAPABILITY_AMBIENT_SECRET": "private"}):
            provider = self.cli(pass_env=["CAPABILITY_TEST"])
            result = provider.invoke("project.custom", {"value": "x"}, {})
            self.assertEqual(result["env"], "forwarded")
            self.assertIsNone(result["ambient"])
            provider = self.cli(pass_env=["CAPABILITY_TEST"], env={"CAPABILITY_TEST": "overridden"})
            self.assertEqual(provider.invoke("project.custom", {"value": "x"}, {})["env"], "overridden")

    def test_changed_requested_mcp_descriptor_requires_explicit_discovery(self):
        provider = self.mcp()
        provider.invoke("project.custom", {"value": "x", "mode": "change"}, {})
        changed = definition()
        changed["input_schema"]["properties"]["value"] = {"type": "integer"}
        changed["annotations"]["readOnlyHint"] = False
        self.write_definitions([changed])
        before = self.marker.read_text()
        error = self.assert_code("capability_changed", lambda: provider.invoke("project.custom", {"value": 7}, {}))
        self.assertEqual(error.details["outcome"], "not_started")
        self.assertEqual(self.marker.read_text(), before)
        # Internal observation does not authorize the changed tool on a later
        # call either. Only an explicit discovery advances that snapshot.
        self.assert_code("capability_changed", lambda: provider.invoke("project.custom", {"value": 7}, {}))
        self.assertFalse(provider.discover()[0].annotations["readOnlyHint"])
        result = provider.invoke("project.custom", {"value": 7}, {})
        self.assertEqual(result["structuredContent"]["arguments"]["value"], 7)

    def test_unrelated_catalog_change_does_not_invalidate_requested_tool(self):
        provider = self.mcp()
        provider.invoke("project.custom", {"value": "x", "mode": "change"}, {})
        changed = definition("another.tool")
        changed["annotations"]["readOnlyHint"] = False
        self.write_definitions([definition(), changed])
        result = provider.invoke("project.custom", {"value": "still authorized"}, {})
        self.assertEqual(result["structuredContent"]["arguments"]["value"], "still authorized")
        # Observing an unrelated new tool does not authorize that new tool.
        error = self.assert_code("not_found", lambda: provider.invoke("another.tool", {"value": "x"}, {}))
        self.assertEqual(error.details["outcome"], "not_started")
        self.assertEqual(self.marker.read_text().splitlines(), ["project.custom", "project.custom"])

    def test_changed_other_tool_is_not_authorized_by_refresh_for_unchanged_tool(self):
        self.write_definitions([definition(), definition("another.tool")])
        provider = self.mcp()
        provider.invoke("project.custom", {"value": "x", "mode": "change"}, {})
        changed = definition("another.tool")
        changed["annotations"]["readOnlyHint"] = False
        self.write_definitions([definition(), changed])
        provider.invoke("project.custom", {"value": "x"}, {})
        error = self.assert_code("capability_changed", lambda: provider.invoke("another.tool", {"value": "x"}, {}))
        self.assertEqual(error.details["outcome"], "not_started")
        self.assertEqual(self.marker.read_text().splitlines(), ["project.custom", "project.custom"])

    def test_cli_keeps_authorized_schema_until_explicit_discovery(self):
        provider = self.cli(True)
        self.assertTrue(provider.discover()[0].annotations["readOnlyHint"])
        changed = definition()
        changed["annotations"]["readOnlyHint"] = False
        changed["input_schema"]["properties"]["value"] = {"type": "integer"}
        self.write_definitions([changed])
        error = self.assert_code("invalid_arguments", lambda: provider.invoke("project.custom", {"value": 7}, {}))
        self.assertEqual(error.details["outcome"], "not_started")
        self.assertFalse(self.marker.exists())
        self.assertFalse(provider.discover()[0].annotations["readOnlyHint"])
        self.assertEqual(provider.invoke("project.custom", {"value": 7}, {})["arguments"], {"value": 7})

    def test_empty_discovery_is_a_snapshot_not_permission_to_discover_again(self):
        self.write_definitions([])
        for provider in (self.cli(True), self.mcp()):
            self.write_definitions([])
            self.assertEqual(provider.discover(), [])
            self.write_definitions([definition()])
            error = self.assert_code("not_found", lambda: provider.invoke("project.custom", {"value": "x"}, {}))
            self.assertEqual(error.details["outcome"], "not_started")
        self.assertFalse(self.marker.exists())

    def test_mcp_reconnect_compares_against_previous_authorized_descriptor(self):
        provider = self.mcp()
        provider.discover()
        # Simulate a completed connection loss before any effect is pending.
        provider._disconnect()
        changed = definition()
        changed["annotations"]["readOnlyHint"] = False
        self.write_definitions([changed])
        error = self.assert_code("capability_changed", lambda: provider.invoke("project.custom", {"value": "x"}, {}))
        self.assertEqual(error.details["outcome"], "not_started")
        self.assertFalse(self.marker.exists())

    def test_per_invocation_descriptor_detects_another_callers_discovery(self):
        for provider in (self.cli(True), self.mcp()):
            with self.subTest(provider=type(provider).__name__):
                self.write_definitions([definition()])
                # Caller A discovers and authorizes this descriptor.
                authorized = asdict(provider.discover()[0])
                changed = definition()
                changed["annotations"]["readOnlyHint"] = False
                self.write_definitions([changed])
                # Caller B explicitly discovers before caller A invokes.
                provider.discover()
                error = self.assert_code("capability_changed", lambda: provider.invoke(
                    "project.custom", {"value": "x"}, {"capability": authorized},
                ))
                self.assertEqual(error.details["outcome"], "not_started")
                self.assertFalse(self.marker.exists())

    def test_mcp_malformed_protocol_and_negotiation_are_rejected(self):
        for fault in ("invalid_json", "wrong_id", "bad_error", "version", "repeat_cursor"):
            with self.subTest(fault=fault):
                provider = self.mcp(command=self.argv("mcp", "--fault", fault))
                self.assert_code("provider_protocol", provider.discover)

    def test_invalid_definitions_and_duplicate_names_are_rejected(self):
        for items in ([definition(), definition()], [{**definition(), "input_schema": {"type": 4}}], [{**definition(), "annotations": []}]):
            self.write_definitions(items)
            for provider in (self.cli(True), self.mcp()):
                with self.subTest(items=items, provider=type(provider).__name__):
                    self.assert_code("provider_protocol", provider.discover)

    def test_mcp_error_result_is_distinct_from_protocol_error(self):
        provider = self.mcp()
        error = self.assert_code("capability_failed", lambda: provider.invoke("project.custom", {"value": "x", "mode": "error"}, {}))
        self.assertEqual(error.details["outcome"], "unknown")
        self.assertTrue(error.details["result"]["isError"])
        error = self.assert_code("provider_error", lambda: provider.invoke("project.custom", {"value": "x", "mode": "rpc_error"}, {}))
        self.assertEqual(error.details["outcome"], "unknown")
        self.assertEqual(error.details["remote_error"]["code"], -32602)
        self.assert_code("provider_protocol", lambda: provider.invoke("project.custom", {"value": "x", "mode": "invalid_result"}, {}))
        self.assert_code("provider_protocol", lambda: provider.invoke("project.custom", {"value": "x", "mode": "invalid_content"}, {}))

    def test_partial_side_effect_before_error_remains_unknown_with_evidence(self):
        provider = self.mcp()
        error = self.assert_code("provider_error", lambda: provider.invoke("project.custom", {"value": "x", "mode": "partial_rpc_error"}, {}))
        self.assertEqual(error.details["outcome"], "unknown")
        self.assertEqual(error.details["remote_error"], {
            "code": -32603, "message": "failed after changing external state", "data": {"applied": True},
        })
        self.assertEqual(self.marker.read_text().splitlines(), ["project.custom"])
        error = self.assert_code("capability_failed", lambda: provider.invoke("project.custom", {"value": "x", "mode": "error"}, {}))
        self.assertEqual(error.details["outcome"], "unknown")
        self.assertEqual(error.details["result"]["structuredContent"]["name"], "project.custom")
        self.assertEqual(self.marker.read_text().splitlines(), ["project.custom", "project.custom"])

    def test_cli_rejects_nonobjects_duplicate_properties_and_failure_exit(self):
        provider = self.cli()
        for mode, code in (("scalar", "provider_protocol"), ("invalid_json", "provider_protocol"), ("overflow_number", "provider_protocol"), ("exit", "capability_failed")):
            with self.subTest(mode=mode):
                self.assert_code(code, lambda: provider.invoke("project.custom", {"value": "x", "mode": mode}, {}))

    def test_deadlines_and_output_bounds_terminate_real_processes(self):
        for provider in (self.cli(timeout=1), self.mcp(timeout=1)):
            before = len(self.marker.read_text().splitlines()) if self.marker.exists() else 0
            start = time.monotonic()
            error = self.assert_code("provider_timeout", lambda: provider.invoke("project.custom", {"value": "x", "mode": "sleep"}, {}))
            self.assertEqual(error.details["outcome"], "unknown")
            self.assertEqual(len(self.marker.read_text().splitlines()), before + 1)
            self.assertLess(time.monotonic() - start, 3)
        for provider in (self.cli(max_output_bytes=1024), self.mcp(max_frame_bytes=2048)):
            self.assert_code("provider_protocol", lambda: provider.invoke("project.custom", {"value": "x", "mode": "flood"}, {}))

    def test_blocked_stdin_is_subject_to_the_deadline(self):
        transport = JsonLineProcess({"command": self.argv("mcp", "--fault", "no_read"), "timeout": 0.2})
        self.addCleanup(transport.close)
        start = time.monotonic()
        self.assert_code("provider_timeout", lambda: transport.request("large", {"value": "x" * 200000}))
        self.assertLess(time.monotonic() - start, 2)
        self.assertIsNotNone(transport.process.poll())

    def test_discovery_and_notifications_have_aggregate_output_bounds(self):
        self.write_definitions([{**definition(f"tool{index}"), "description": "x" * 1000} for index in range(3)])
        self.assert_code("provider_protocol", self.mcp(max_discovery_bytes=2500).discover)
        self.write_definitions([definition()])
        provider = self.mcp(max_output_bytes=1500)
        self.assert_code("provider_protocol", lambda: provider.invoke("project.custom", {"value": "x", "mode": "many_messages"}, {}))

    def test_stderr_logging_does_not_block_or_invalidate_result(self):
        for provider in (self.cli(), self.mcp()):
            self.assertIsInstance(provider.invoke("project.custom", {"value": "x", "mode": "stderr"}, {}), dict)

    def test_output_schema_is_verified(self):
        tool = {**definition(), "output_schema": {"type": "object", "required": ["missing"]}}
        self.write_definitions([tool])
        for provider in (self.cli(True), self.mcp()):
            self.assert_code("provider_protocol", lambda: provider.invoke("project.custom", {"value": "x"}, {}))

    def test_close_reaps_mcp_process_and_is_idempotent(self):
        provider = self.mcp()
        provider.discover()
        process = provider._client.process
        provider.close()
        provider.close()
        self.assertIsNotNone(process.poll())
        self.assert_code("provider_unavailable", provider.discover)

    def test_configured_bounds_and_argv_are_validated(self):
        for constructor in (CLIProvider, StdioMCPProvider):
            for bad in ({"command": "python executable"}, {"command": [sys.executable], "timeout": float("nan")}, {"command": [sys.executable], "env": {"A": 4}}):
                with self.subTest(constructor=constructor.__name__, bad=bad):
                    self.assert_code("invalid_configuration", lambda: constructor(bad))


if __name__ == "__main__":
    unittest.main()
