#!/usr/bin/env python3
# testkit: exempt fixture program planted by the consuming World
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

STUB = Path("@@STUB@@")
argv = sys.argv[1:]
log = STUB / "findings-argv.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\n")
plant_path = STUB / "plant.json"
plant = json.loads(plant_path.read_text(encoding="utf-8"))
plant.setdefault("cwds", []).append(os.getcwd())
plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
if plant.get("ledger_fail"):
    sys.stderr.write(
        "ledger: expected a writable findings export; found a write error; "
        "needed a writable ledger\n"
    )
    raise SystemExit(3)
if argv and argv[0] == "export":
    sys.stdout.write("{}\n")
raise SystemExit(0)
