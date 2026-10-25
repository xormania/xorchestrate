"""Optional process-side gates for deterministic completion contract fixtures."""
import time
from pathlib import Path


def wait_gate(body, stage):
    if body.get(stage + "_gate"):
        Path(body[stage + "_entered"]).write_text(stage + " held")
        deadline = time.monotonic() + 30
        while not Path(body[stage + "_gate"]).exists():
            if time.monotonic() >= deadline:
                raise AssertionError("test must release its " + stage + " gate")
            time.sleep(.01)
