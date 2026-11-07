"""Large-link bundle subjects reuse the public CLI/MCP workflow composition."""

import json

from tests.hexagonal.support.telemetry_workflow import Episode


class BundleReceipts:
    def __init__(self, root):
        self.episode = Episode(root, direct=True)
        self.source_bundle = self.episode.bundle("source")
        self.link_targets = [f"unknown-{index:04d}-" + "e" * 985 for index in range(1200)]

    def close(self):
        self.episode.close()

    def seed_links(self):
        work = self.episode.create_work()
        result = self.episode.runtime.application.record_observation("trial.result", {"outcome": "passed"},
            {"actor": "owner", "work_id": work}, event_id="manifest-stress-trial",
            links=[("references", target) for target in self.link_targets])
        assert result["status"] == "recorded", "The fixture must capture valid source links before export"
        assert all(len(target) < 1000 for target in self.link_targets)

    def public_operation(self, operation):
        if operation == "telemetry_snapshot":
            return self.episode.call(operation, {"destination": "source"}), self.source_bundle
        self.episode.call("telemetry_snapshot", {"destination": "source"})
        if operation == "telemetry_verify":
            return self.episode.call(operation, {"destination": "source"}), self.source_bundle
        assert operation == "telemetry_restore"
        return self.episode.call(operation, {"bundle": "source", "destination": "restored"}), self.episode.bundle("restored")

    def verify(self, path):
        return self.episode.call("telemetry_verify", {"destination": str(path)})

    def saved_manifest(self, path):
        content = (path / "manifest.json").read_bytes()
        return json.loads(content), content

    def manifest_path(self, path):
        return str(path / "manifest.json")

    def mcp_verify(self):
        packets = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "independent-bundle-receipts", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "telemetry_verify", "arguments": {"destination": "source"}}}]
        response = self.episode.cli("serve", input=b"".join(json.dumps(packet).encode() + b"\n" for packet in packets))
        assert response.returncode == 0, response.stderr.decode()
        assert response.stderr == b""
        frames = [line + b"\n" for line in response.stdout.splitlines()]
        replies = [(json.loads(frame), frame) for frame in frames]
        assert {reply["id"] for reply, _ in replies} == {1, 2}
        reply, frame = next((reply, frame) for reply, frame in replies if reply["id"] == 2)
        assert "error" not in reply
        result = reply["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result["structuredContent"], frame
