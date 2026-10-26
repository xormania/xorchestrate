"""Configured state policy reaches the separately launched operator host."""
import json
import select
import subprocess
import sys
from pathlib import Path

import pytest

from xorchestrate.adapters.bridge import BridgeSessionDriver
from xorchestrate.model import OrchestrationError


@pytest.mark.parametrize("policy", ["deny", "allow"])
def test_bridge_cli_consumes_configured_state_engine(tmp_path, policy):
    root = Path(__file__).resolve().parents[2]
    endpoint, state = tmp_path / "host", tmp_path / "host-state.json"
    config = tmp_path / "host.json"
    fixture = Path(__file__).parent / "fixtures" / "session_server.py"
    config.write_text(json.dumps({
        "state_engine": {"adapter": "declared", "options": {
            "guards": {"native_effect": {"default": policy}}}},
        "sessions": {"native": {"adapter": "jsonrpc", "options": {
            "command": [sys.executable, str(fixture), "jsonrpc"], "timeout": 2}}},
    }))
    process = subprocess.Popen([
        sys.executable, "-m", "xorchestrate.bridge", "--config", str(config),
        "--driver", "native", "--endpoint", str(endpoint), "--transport", "fifo",
        "--state", str(state), "--specification", '{"goal":"configured host"}',
    ], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    client = None
    try:
        assert select.select([process.stdout], [], [], 10)[0], "STATE_BRIDGE_CLI_READY"
        assert json.loads(process.stdout.readline())["ready"]
        client = BridgeSessionDriver({"endpoint": str(endpoint), "timeout": 2, "transport": "fifo"})
        backend = client.attach({})
        if policy == "deny":
            with pytest.raises(OrchestrationError) as failure:
                client.send(backend, {"text": "must be refused"})
            assert failure.value.code == "state_guard_rejected", "STATE_BRIDGE_CLI_CONSUMES_POLICY"
            assert failure.value.details["outcome"] == "not_started"
            assert json.loads(state.read_text())["effects"] == {}
        else:
            assert client.send(backend, {"text": "permitted"})["start"]["goal"] == "configured host"
            assert [row["status"] for row in json.loads(state.read_text())["effects"].values()] == ["completed"]
    finally:
        if client is not None:
            client.close()
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
