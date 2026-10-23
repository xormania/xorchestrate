"""Common transport boundary; endpoint suites retain routing/authority contracts."""

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from ops.testkit import World

SOURCE = Path(__file__).resolve().parents[1] / "tools/mcp_transport.py"


class McpTransport(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="mcp-transport-")
        self.addCleanup(self.world.close)
        path = self.world.stage(SOURCE)
        spec = importlib.util.spec_from_file_location("transport_under_test", path)
        self.transport = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.transport)
        self.route = Mock(return_value={"domain": True})

    def dispatch(self, request, strict=True):
        return self.transport.dispatch(request, self.route,
                                       server={"serverInfo": {"name": "example", "version": "1"},
                                               "instructions": "bound example"},
                                       strict_initialize=strict)

    def request(self, method, params=None, ident=1):
        return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}

    def test_negotiates_versions_and_keeps_server_identity(self):
        for wanted in ("2024-11-05", "2025-03-26", "2025-06-18", "unknown"):
            answer = self.dispatch(self.request("initialize", {"protocolVersion": wanted}))
            result = answer["result"]
            self.assertEqual(result["protocolVersion"], "2025-06-18" if wanted == "unknown" else wanted)
            self.assertEqual(result["serverInfo"], {"name": "example", "version": "1"})
            self.assertEqual(result["instructions"], "bound example")
            self.assertEqual(set(result["capabilities"]), {"tools", "resources"})
        self.route.assert_not_called()

    def test_strict_initialize_keeps_the_legacy_server_policy_explicit(self):
        for params in ({"extra": True}, {"protocolVersion": None},
                       {"capabilities": []}, {"clientInfo": "bad"}):
            request = self.request("initialize", params)
            self.assertEqual(self.dispatch(request)["error"]["code"], -32602)
            self.assertIn("result", self.dispatch(request, strict=False))
        self.route.assert_not_called()

    def test_validates_envelope_and_strips_metadata_before_routing(self):
        for request, code in (([], -32600), ({"jsonrpc": "1.0"}, -32600),
                              ({"jsonrpc": "2.0", "id": 1}, -32600),
                              (dict(self.request("tools/call"), params=[]), -32602),
                              (self.request("tools/call", {"_meta": []}), -32602)):
            self.assertEqual(self.dispatch(request)["error"]["code"], code)
        self.route.assert_not_called()
        request = self.request("tools/call", {"name": "domain", "_meta": {"progressToken": "t"}})
        self.assertEqual(self.dispatch(request), {"domain": True})
        self.route.assert_called_once_with(1, "tools/call", {"name": "domain"})
        self.assertIn("_meta", request["params"])

    def test_notifications_cannot_invoke_a_domain_mutation(self):
        for params in ({"name": "mutate"}, {"_meta": []}):
            request = self.request("tools/call", params)
            del request["id"]
            self.assertIsNone(self.dispatch(request))
        self.route.assert_not_called()

    def test_ping_allows_extra_members_without_domain_dispatch(self):
        self.assertEqual(self.dispatch(self.request("ping", {"extra": True})),
                         {"jsonrpc": "2.0", "id": 1, "result": {}})
        self.route.assert_not_called()

    def test_bad_frames_are_drained_and_boundary_and_eof_frames_survive(self):
        ping = json.dumps(self.request("ping")).encode()
        frames = [b"{\n", b"\xff\n", b"NaN\n", b"[" * 20000 + b"]" * 20000 + b"\n",
                  b"x" * (self.transport.MAX_LINE * 2 + 1) + b"\n",
                  ping + b" " * (self.transport.MAX_LINE - len(ping) - 1) + b"\n", ping]
        answers = []
        self.assertEqual(self.transport.serve(io.BytesIO(b"".join(frames)), self.dispatch, answers.append), 0)
        self.assertEqual([r["error"]["code"] for r in answers[:5]], [-32700] * 5)
        self.assertEqual(answers[5:], [{"jsonrpc": "2.0", "id": 1, "result": {}}] * 2)
        self.route.assert_not_called()
