"""Real stdio and durable-store acceptance for the progressive live MCP entry."""

import hashlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "ops/conductor/tools"


def request(name, arguments=None, identifier=1):
    return {"jsonrpc": "2.0", "id": identifier, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def invoke(capability, arguments=None, identifier=1):
    return request("capability_invoke", {"capability": capability, "arguments": arguments or {}}, identifier)


class LiveEntry(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="dev-mcp-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")
        self.store = self.world.xdg_state / "testlane/state.db"
        for session in ("sender", "peer"):
            result = run([sys.executable, "-B", str(TOOLS / "coordination.py"), "bind", "--roles", "worker"],
                         cwd=self.world.cwd, env=self.env(session), timeout=20)
            self.assertEqual(result.exit, 0, result.stderr)

    def env(self, session="sender"):
        return self.world.env(extra={
            "LANE_CONF": str(self.conf), "WF_AGENT": "Codex <test@example.test>",
            "CLAUDE_CODE_SESSION_ID": session,
            "COORDINATION_CAPABILITY": hashlib.sha256(session.encode()).hexdigest(),
        })

    def wire(self, *requests, session="sender", binding=None, env=None):
        argv = [sys.executable, "-B", str(TOOLS / "dev_mcp.py")]
        if binding:
            argv += ["--binding", str(binding)]
        result = run([*argv, "serve"], cwd=self.world.cwd, env=env or self.env(session),
                     stdin="".join(json.dumps(row) + "\n" for row in requests), timeout=20)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(all(len(line.encode()) <= 16384 for line in result.stdout.splitlines()))
        return [json.loads(line) for line in result.stdout.splitlines()]

    def payload(self, request, **options):
        result = self.wire(request, **options)[0]["result"]
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]

    def send(self, **over):
        args = {"to": "PT-2", "purpose": "request", "body": "Which service should I reuse?", "key": "question-1"}
        return invoke("coord_send", {**args, **over})

    def test_three_tools_discover_describe_and_write_to_existing_store(self):
        initialized, listed = self.wire(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "dev-lane")
        self.assertEqual(len(listed["result"]["tools"]), 3)
        self.assertLess(len(json.dumps(listed).encode()), 2000)
        search = self.payload(request("capability_search", {"query": "send"}))
        self.assertEqual(search["capabilities"][0]["name"], "coord_send")
        described = self.payload(request("capability_describe", {"capability": "coord_send"}))
        self.assertIn("to", described["tool"]["inputSchema"]["required"])
        sent = self.payload(self.send())
        self.assertEqual(sent["sender"], "PT-1")
        self.assertIsNone(sent["delivered_at"])
        with sqlite3.connect(self.store) as conn:
            row = conn.execute("SELECT sender, recipient, body FROM messages").fetchone()
        self.assertEqual(row, (1, "PT-2", "Which service should I reuse?"))

    def test_peer_exchange_preserves_acknowledgment_and_resolution(self):
        peers = self.payload(invoke("coord_participants"))
        self.assertEqual([row["participant"] for row in peers["participants"]], ["PT-1", "PT-2"])
        sent = self.payload(self.send())
        inbox = self.payload(invoke("coord_inbox", {"unresolved": True}), session="peer")
        self.assertEqual(inbox["messages"][0]["message"], sent["message"])
        ack = self.payload(invoke("coord_ack", {"message": sent["message"]}), session="peer")
        self.assertIsNotNone(ack["acked_at"])
        self.assertNotEqual(ack["place"], "resolved")
        reply = self.payload(invoke("coord_send", {"to": "PT-1", "purpose": "inform", "body": "Reuse the existing core.",
                                                  "reply_to": sent["message"], "key": "reply-1"}), session="peer")
        resolved = self.payload(invoke("coord_resolve", {"message": sent["message"], "evidence": "a" * 40}), session="peer")
        self.assertEqual(resolved["place"], "resolved")
        self.assertEqual(self.payload(invoke("coord_inbox"))["messages"][0]["message"], reply["message"])
        self.assertEqual(self.payload(invoke("coord_attempts", {"message": sent["message"]}))["attempts"], [])
        self.assertEqual(self.payload(invoke("coord_routes"))["routes"], [])

    def test_dispatch_cannot_be_discovered_or_invoked_by_default(self):
        search = self.payload(request("capability_search", {"query": "launch"}))
        self.assertEqual(search["capabilities"], [])
        for capability in ("coord_launch", "coord_wait", "coord_status", "coord_collect"):
            result = self.wire(invoke(capability))[0]["result"]
            self.assertTrue(result["isError"])
            self.assertIn("explicit user request", result["content"][0]["text"])

    def test_invalid_shapes_and_named_authority_write_nothing(self):
        requests = [request("capability_search", {"limit": True}),
                    request("capability_search", {"limit": 11}),
                    request("capability_search", {"query": "x" * 161}),
                    request("capability_invoke", {"capability": "coord_send", "arguments": []}),
                    self.send(participant="PT-2"), self.send(extra="command"),
                    invoke("coord_participants", {"role": "lead"})]
        for response in self.wire(*requests):
            self.assertTrue(response["result"]["isError"], response)
        self.assertEqual(self.payload(invoke("coord_inbox"), session="peer")["messages"], [])

    def test_large_committed_send_keeps_identity_and_retry_does_not_duplicate(self):
        sent = self.payload(self.send(body="x" * 12000))
        self.assertEqual(sent["state"], "unavailable")
        self.assertIn("message", sent)
        repeated = self.payload(self.send(body="x" * 12000))
        self.assertEqual(repeated["message"], sent["message"])
        with sqlite3.connect(self.store) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM messages").fetchone()[0], 1)

    def test_oversized_request_id_refuses_before_write(self):
        call = self.send()
        call["id"] = "x" * 201
        response = self.wire(call)[0]
        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(self.payload(invoke("coord_inbox"), session="peer")["messages"], [])

    def test_discovery_pages_without_full_schema_and_unavailable_evidence_is_explicit(self):
        first = self.payload(request("capability_search", {"limit": 2}))
        second = self.payload(request("capability_search", {"limit": 2, "offset": first["next_offset"]}))
        self.assertTrue({row["name"] for row in first["capabilities"]}.isdisjoint(
            {row["name"] for row in second["capabilities"]}))
        self.assertNotIn("inputSchema", first["capabilities"][0])
        missing = self.payload(request("capability_describe", {"capability": "lane_brief"}))
        self.assertFalse(missing["available"])
        self.assertIn("--evidence-config", missing["reason"])
        self.assertTrue(self.wire(invoke("lane_brief"))[0]["result"]["isError"])
        self.assertEqual(len(self.payload(invoke("coord_participants"))["participants"]), 2)

    def test_binding_allowlist_includes_live_reads_and_still_limits_mutations(self):
        binding = self.world.plant_text("binding.json", json.dumps({
            "version": 1, "participant": "PT-1", "assignment": None, "branch": None,
            "worktree": str(self.world.cwd), "record": None, "issued_at": "2026-09-26T00:00:00Z",
            "capabilities": ["coord_participants"],
        }))
        self.assertEqual(len(self.payload(invoke("coord_participants"), binding=binding)["participants"]), 2)
        denied = self.wire(self.send(), binding=binding)[0]["result"]
        self.assertTrue(denied["isError"])
        self.assertIn("binding does not permit", denied["content"][0]["text"])

    def test_public_session_name_cannot_substitute_for_capability(self):
        env = self.env()
        env["COORDINATION_CAPABILITY"] = "0" * 64
        result = self.wire(self.send(), env=env)[0]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(self.payload(invoke("coord_inbox"), session="peer")["messages"], [])


if __name__ == "__main__":
    unittest.main()
