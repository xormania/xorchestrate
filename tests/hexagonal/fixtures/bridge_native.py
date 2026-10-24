"""Durable deterministic native-session port for actual host-crash injection.

The private context survives in a separate file. This is not a vendor process.
The test kills only its own bridge-host subprocess while an applied effect has
not returned, then resumes the same simulated conversation from its receipt.
"""
import hashlib
import json
import time
import uuid
from pathlib import Path

from xorchestrate.model import OrchestrationError


class PersistentNativeFixture:
    def __init__(self, options):
        self.options = options
        self.path = Path(options["state_file"])
        self.blocked = Path(options["blocked_file"])
        self.configuration_id = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()

    def _write(self, state):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(self.path)

    def start(self, specification):
        self._write({"native_id": uuid.uuid4().hex, "memory": None, "effects": 0})
        return self._receipt()

    def _receipt(self):
        state = json.loads(self.path.read_text())
        return {"native_id": state["native_id"], "incarnation": uuid.uuid4().hex, "protocol": "crash-fixture",
                "configuration_id": self.configuration_id, "ownership": "owned", "resumable": True}

    def resume(self, source, specification):
        current = self._receipt()
        if source.get("native_id") != current["native_id"] or source.get("configuration_id") != self.configuration_id:
            raise OrchestrationError("Fixture source does not identify retained conversation.", "session_identity")
        return current

    def send(self, backend, message):
        state = json.loads(self.path.read_text())
        if "remember" in message:
            state["memory"] = message["remember"]
            state["effects"] += 1
            self._write(state)
        if message.get("block_after"):
            self.blocked.write_text("effect applied; response outstanding")
            while True:
                time.sleep(.1)
        return {"memory": state["memory"], "effects": state["effects"]}

    def deliver(self, backend, message):
        return self.send(backend, message)

    def poll(self, backend):
        return [{"retained": json.loads(self.path.read_text())}]

    def stop(self, backend):
        return {"stopped": True}

    def close(self):
        pass
