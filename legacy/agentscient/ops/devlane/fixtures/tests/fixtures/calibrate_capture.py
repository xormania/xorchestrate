#!/usr/bin/env python3
# testkit: exempt planted calibrate-capture stub, a subject
"""Capture stdin of fixture.sh calibrate for the python-tooling grade cases."""

import os
import sys
from pathlib import Path


def refuse(cls, expected, found, needed):
    print(
        f"{cls}: expected {expected}; found {found}; needed {needed}",
        file=sys.stderr,
    )
    raise SystemExit(2)


verb = sys.argv[1] if len(sys.argv) > 1 else ""
if verb != "calibrate":
    refuse("arguments", "calibrate", verb or "none", "name calibrate")
agent = os.environ.get("WF_AGENT", "")
if "<" not in agent or ">" not in agent:
    refuse(
        "identity",
        "Name <address>",
        agent or "unset",
        "export WF_AGENT",
    )
capture = os.environ.get("CALIBRATE_CAPTURE")
if not capture:
    refuse("calibration", "CALIBRATE_CAPTURE", "unset", "set it in tests")
Path(capture).write_text(sys.stdin.read(), encoding="utf-8")
