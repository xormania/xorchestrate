# testkit: exempt fixture program planted as an owned holder
"""A sleeper the world owns, for the managed-sessions rows.

Planted through Tools.plant(..., script=...) and run through
ops.testkit.run as `holder READY STOP`: it writes its pid to READY, then
waits until STOP exists. origin: 20260923T003033Z-review-codex-c99cc9#7
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(
            f"arguments: expected READY STOP; found {len(argv)} arguments; "
            "needed the ready file and the stop file\n"
        )
        return 2
    ready, stop = Path(argv[0]), Path(argv[1])
    ready.write_text(str(os.getpid()), encoding="utf-8")
    while not stop.exists():
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
