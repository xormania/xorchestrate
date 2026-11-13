"""Optional process-side gates for deterministic completion contract fixtures."""
import json
import time
from pathlib import Path


def _receipt(path, value):
    temporary = Path(str(path) + ".pending")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def announce_operation(body, native_id, native_operation):
    """Return the challenge with the peer's actual native invocation identity."""
    if not body.get("operation_observation"):
        return None
    receipt = {**body["operation_observation"], "source": native_id,
               "native_operation": native_operation}
    _receipt(receipt["receipt"], {**receipt, "stage": "accepted"})
    return receipt


def wait_gate(body, stage, observation=None):
    if body.get(stage + "_gate"):
        if observation is None:
            Path(body[stage + "_entered"]).write_text(stage + " held")
        else:
            _receipt(body[stage + "_entered"], {**observation, "stage": stage})
        deadline = time.monotonic() + 30
        while True:
            gate = Path(body[stage + "_gate"])
            if gate.exists() and (observation is None or gate.read_text() == observation["token"]):
                return
            if time.monotonic() >= deadline:
                raise AssertionError("test must release its " + stage + " gate")
            time.sleep(.01)
