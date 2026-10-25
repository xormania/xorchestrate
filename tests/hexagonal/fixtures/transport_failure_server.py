"""Private peers for interrupted input delivery and callback failures."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def emit(packet):
    sys.stdout.write(json.dumps(packet) + "\n")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("--marker", type=Path)
    args = parser.parse_args()

    def mark(event, **details):
        if args.marker:
            with args.marker.open("a", encoding="utf-8") as target:
                target.write(json.dumps({"event": event, **details}) + "\n")

    mark("started")
    if args.mode in {"unread_input", "late_input", "output_overflow", "input_deadline"}:
        if args.mode == "input_deadline":
            time.sleep(1.2)
        emit({"value": "x" * 2048} if args.mode == "output_overflow" else {"ok": True})
        os.close(sys.stdout.fileno())
        mark("stdout_closed")
        if args.mode == "output_overflow":
            time.sleep(5)
        elif args.mode == "input_deadline":
            time.sleep(1.2)
            mark("deadline_overrun")
            time.sleep(5)
        else:
            time.sleep(0.15)
            if args.mode == "late_input":
                data = sys.stdin.buffer.read()
                mark("input_received", value_length=len(json.loads(data)["value"]))
        return

    request = json.loads(sys.stdin.readline()) if args.mode == "request_callback" else None
    emit({"jsonrpc": "2.0", "id": "callback", "method": "fixture/callback", "params": {}})
    reply = sys.stdin.readline()
    if reply and request:
        emit({"jsonrpc": "2.0", "id": request["id"], "result": {"callback_answered": True}})
    for line in sys.stdin:
        packet = json.loads(line)
        emit({"jsonrpc": "2.0", "id": packet["id"], "result": {}})


if __name__ == "__main__":
    main()
