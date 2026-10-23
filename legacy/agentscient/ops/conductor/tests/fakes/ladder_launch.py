#!/usr/bin/env python3
# testkit: exempt fixture program planted by the consuming World
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

STUB = Path("@@STUB@@")
argv = sys.argv[1:]
log = STUB / "launch-argv.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\n")
plant_path = STUB / "plant.json"
plant = json.loads(plant_path.read_text(encoding="utf-8"))
plant.setdefault("cwds", []).append(os.getcwd())
if not argv or argv[0] != "wait" or len(argv) < 2:
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stderr.write(
        "arguments: expected wait ID; found "
        + json.dumps(argv)
        + "; needed launch.py wait ID\n"
    )
    raise SystemExit(2)
rec_id = argv[1]
info = plant.get("records", {}).get(rec_id)
if not info:
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stderr.write(
        "wait: expected a minted record; found unknown id "
        + rec_id
        + "; needed wait the detach id\n"
    )
    raise SystemExit(2)
name = info.get("name")
plant.setdefault("waits", []).append({"id": rec_id, "name": name, "cwd": os.getcwd()})
path = Path(info["path"])
if name in plant.get("crash_stages", []):
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        rec_id + " died attempt=1 exit=137 tripped=false envelope=none\n"
    )
    raise SystemExit(1)
if name in plant.get("trip_stages", []):
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        rec_id + " died attempt=1 exit=137 tripped=true envelope=none\n"
    )
    raise SystemExit(1)
if name in plant.get("wait_wall_stages", []):
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(rec_id + " running attempt=1 wall=1\n")
    raise SystemExit(2)
if name in plant.get("wait_poll_invalid_stages", []):
    payload = json.loads(path.read_text(encoding="utf-8"))
    envelope = dict(plant.get("envelopes", {}).get(rec_id) or {})
    envelope["status"] = "invalid"
    payload["status"] = "closed"
    payload["at"]["closed"] = "2026-09-04T12:00:01Z"
    payload["result"] = {"envelope": envelope}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    plant.setdefault("wait_io", []).append({
        "id": rec_id,
        "stderr": (
            "poll " + rec_id + " running elapsed=12s\n"
            "poll " + rec_id + " running elapsed=24s\n"
        ),
        "stdout": (
            rec_id
            + " closed attempt=1 exit=0 tripped=false envelope=invalid\n"
        ),
        "exit": 1,
    })
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stderr.write("poll " + rec_id + " running elapsed=12s\n")
    sys.stderr.write("poll " + rec_id + " running elapsed=24s\n")
    sys.stdout.write(
        rec_id + " closed attempt=1 exit=0 tripped=false envelope=invalid\n"
    )
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
envelope = dict(plant.get("envelopes", {}).get(rec_id) or {})
summary = "ok"
if name in plant.get("invalid_envelope_stages", []):
    envelope["status"] = "invalid"
    summary = "invalid"
payload["status"] = "closed"
payload["at"]["closed"] = "2026-09-04T12:00:01Z"
payload["result"] = {"envelope": envelope}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
sys.stdout.write(
    rec_id + " closed attempt=1 exit=0 tripped=false envelope=" + summary + "\n"
)
raise SystemExit(0)
