# testkit: exempt fixture program planted by the consuming World
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

start_witness = Path("@@START_WITNESS@@")
start = os.environ.get("AGY_FAKE_START") or ""
done = os.environ.get("AGY_FAKE_DONE") or ""
sleep_s = float(os.environ.get("AGY_FAKE_SLEEP") or "0")
status = os.environ.get("AGY_FAKE_STATUS") or "SUCCESS"
omit_usage = os.environ.get("AGY_FAKE_OMIT_USAGE") == "1"
usage_raw = os.environ.get("AGY_FAKE_USAGE") or ""
conversation = os.environ.get("AGY_FAKE_CONVERSATION") or (
    "00000000-0000-4000-8000-000000000001"
)
if usage_raw:
    usage = json.loads(usage_raw)
else:
    output = int(os.environ.get("AGY_FAKE_OUTPUT") or "500")
    usage = {
        "input_tokens": 10,
        "output_tokens": output,
        "thinking_tokens": 1,
        "cache_read_tokens": 4,
        "total_tokens": 10 + output,
    }

try:
    pgid = os.getpgid(0)
except OSError:
    pgid = os.getpid()
start_witness.parent.mkdir(parents=True, exist_ok=True)
start_witness.write_text(json.dumps({
    "pid": os.getpid(),
    "pgid": pgid,
    "argv": sys.argv,
    "cwd": os.getcwd(),
    "exe": sys.argv[0] if sys.argv else "",
}) + "\n", encoding="utf-8")
if start:
    Path(start).write_text("started\n", encoding="utf-8")

sys.stdin.read()
init = {
    "event": "init",
    "conversation_id": conversation,
    "init": {
        "model": "gemini-3.8-flash-high",
        "cwd": os.getcwd(),
        "tools": [],
        "permission_mode": "always-proceed",
    },
}
sys.stdout.write(json.dumps(init) + "\n")
sys.stdout.flush()
step_body = {
    "conversation_id": conversation,
    "step_index": 1,
    "state": "DONE",
    "step_type": "agent_response",
}
if not omit_usage:
    step_body["usage"] = usage
sys.stdout.write(json.dumps({
    "event": "step_update",
    "step_update": step_body,
}) + "\n")
sys.stdout.flush()
if sleep_s > 0:
    time.sleep(sleep_s)
if not omit_usage:
    result = {
        "conversation_id": conversation,
        "status": status,
        "response": os.environ.get("AGY_FAKE_RESPONSE") or "ok",
        "duration_seconds": 1,
        "num_turns": 1,
        "usage": usage,
    }
    sys.stdout.write(json.dumps({"event": "result", "result": result}) + "\n")
    sys.stdout.flush()
if done:
    Path(done).write_text("COMPLETED\n", encoding="utf-8")
raise SystemExit(0)
