"""Deterministic protocol peer with separate diagnostics and framed stdout."""

import json
import os
import sys
import time


def diagnostic(mode):
    if mode in {"secret", "assessment_secret"}:
        sys.stderr.buffer.write(b"diagnostic: " + os.environ["API_KEY"].encode() + b"\n")
    elif mode == "binary":
        sys.stderr.buffer.write(b"binary-\xff\x00-diagnostic\n")
    else:
        sys.stderr.buffer.write(b"diagnostic-start\n" + b"x" * 131072 + b"\ndiagnostic-end\n")
    sys.stderr.buffer.flush()


mode = sys.argv[1]
diagnostic(mode)
if mode.startswith("rpc"):
    for line in sys.stdin:
        packet = json.loads(line)
        if mode == "rpc_hold":
            time.sleep(10)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": packet["id"], "result": {"echo": packet.get("params")}}), flush=True)
else:
    payload = json.load(sys.stdin)
    if mode == "invalid":
        print("{invalid", flush=True)
    elif mode == "failure":
        sys.exit(7)
    elif mode == "timeout":
        time.sleep(10)
    elif mode == "secret":
        print(json.dumps({"private": os.environ["API_KEY"], "tokens": {"input_tokens": 0}}), flush=True)
    elif mode == "assessment_secret":
        print(json.dumps({"outcome": "ready", "confidence": .9,
                          "details": {"private": os.environ["API_KEY"], "tokens": {"input_tokens": 0}}}), flush=True)
    else:
        print(json.dumps({"echo": payload, "unicode": "λ"}, ensure_ascii=False), flush=True)
