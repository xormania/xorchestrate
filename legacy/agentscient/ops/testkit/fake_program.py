"""The one fake program, driven by a JSON spec. A planted tool is a
three-line launcher that calls ``serve`` here; every invocation appends
one JSON line to the tool's call log with a single ``write`` on an
append-only descriptor, so concurrent calls never lose a line.

The spec: ``{"stdout": "", "stderr": "", "exit": 0, "delay": 0.0,
"responses": [{"match": ["sub", "strings"], "stdout": ..., "stderr":
..., "exit": ...}, ...]}``. The first response whose every ``match``
string appears in the joined argv wins; else the top level answers.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import sys
import time


def log_call(log_path: str, argv: list[str], stdin: str) -> None:
    line = json.dumps({
        "at": time.time_ns(),
        "pid": os.getpid(),
        "argv": argv,
        "cwd": os.getcwd(),
        "stdin_sha256": hashlib.sha256(stdin.encode("utf-8")).hexdigest() if stdin else None,
    }) + "\n"
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_stdin() -> str:
    """What the caller piped in, or nothing: a terminal, or a pipe nobody
    has written to or closed, is never waited on (a planted tool that
    blocks on an inherited stdin hangs the suite that planted it)."""
    stream = sys.stdin
    if stream is None or stream.closed or stream.isatty():
        return ""
    try:
        ready, _, _ = select.select([stream], [], [], 0.05)
    except (OSError, ValueError):
        return ""
    return stream.read() if ready else ""


def choose(spec: dict, argv: list[str]) -> dict:
    joined = " ".join(argv)
    for response in spec.get("responses") or []:
        if all(needle in joined for needle in response.get("match", [])):
            return response
    return spec


def serve(spec_path: str, log_path: str, argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    stdin = read_stdin()
    log_call(log_path, argv, stdin)
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    answer = choose(spec, argv)
    delay = float(answer.get("delay", spec.get("delay", 0.0)) or 0.0)
    if delay:
        time.sleep(delay)
    out = answer.get("stdout", spec.get("stdout", ""))
    err = answer.get("stderr", spec.get("stderr", ""))
    if out:
        sys.stdout.write(out)
        sys.stdout.flush()
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    return int(answer.get("exit", spec.get("exit", 0)))


if __name__ == "__main__":
    raise SystemExit(serve(sys.argv[1], sys.argv[2], sys.argv[3:]))
