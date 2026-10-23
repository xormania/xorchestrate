#!/usr/bin/env python3
# testkit: exempt planted Codex wrapper; a fake CLI, not a suite
"""Wrap the planted Codex CLI: record argv, probe writes, optional vendor reject.

Placeholders __ARGV_PROBE__, __WRITE_PROBE__, __INNER__, __REJECT__ and
__PROBE_WRITES__ are replaced at plant time so a launcher that strips
env vars still leaves evidence the child ran.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

argv_probe = Path("__ARGV_PROBE__")
write_probe = Path("__WRITE_PROBE__")
inner = Path("__INNER__")
reject_resume_sandbox = "__REJECT__" == "1"
probe_writes = "__PROBE_WRITES__" == "1"

argv_probe.parent.mkdir(parents=True, exist_ok=True)
argv_probe.write_text(json.dumps(sys.argv) + "\n", encoding="utf-8")

if (
    reject_resume_sandbox
    and len(sys.argv) >= 3
    and sys.argv[1] == "exec"
    and sys.argv[2] == "resume"
    and "--sandbox" in sys.argv
):
    sys.stderr.write("error: unexpected argument '--sandbox' found\n")
    raise SystemExit(2)

if probe_writes:
    cwd = Path(os.getcwd())
    out_dir = cwd.parent / "out"
    probe = {
        "cwd": str(cwd),
        "snapshot_write": False,
        "out_write": False,
    }
    try:
        target = cwd / "SKEPTIC_SNAPSHOT_PROBE.txt"
        target.write_text("snapshot-probe\n", encoding="utf-8")
        probe["snapshot_write"] = True
        try:
            target.unlink()
        except OSError as exc:
            probe["snapshot_unlink_error"] = type(exc).__name__
    except OSError as exc:
        probe["snapshot_error"] = type(exc).__name__
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "SKEPTIC.md"
        target.write_text("skeptic-probe\n", encoding="utf-8")
        probe["out_write"] = True
    except OSError as exc:
        probe["out_error"] = type(exc).__name__
    write_probe.write_text(json.dumps(probe) + "\n", encoding="utf-8")

result = subprocess.run(
    [str(inner), *sys.argv[1:]],
    check=False,
    timeout=120,
)
raise SystemExit(result.returncode)
