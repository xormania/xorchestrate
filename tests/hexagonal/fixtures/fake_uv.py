"""A fake ``uv`` for release-handoff tests; installed on PATH as ``uv`` by the test.

``uv python install X`` and ``uv sync --project DIR --python X`` are recorded
in ``$FAKE_UV_LOG``. ``sync`` creates ``DIR/.venv/bin/python``: a stand-in
interpreter that answers ``python -m xorchestrate ...`` with one JSON line
naming the release it was built for, its arguments and the handoff marker.
``$FAKE_UV_FAIL=sync`` makes ``sync`` fail. The release is read from the
toolchain's ``pyproject.toml`` dependency (``fake-release=<release>``).
"""
import json
import os
import re
import sys
from pathlib import Path


def record(arguments):
    log = os.environ.get("FAKE_UV_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as target:
            target.write(json.dumps(arguments) + "\n")


def main(arguments):
    if arguments[:1] == ["toolchain"]:
        release, rest = arguments[1], arguments[2:]
        print(json.dumps({"toolchain": release, "argv": rest, "handoff": os.environ.get("XORCHESTRATE_HANDOFF"),
                          "cwd": os.getcwd()}))
        return 0
    record(arguments)
    if arguments[:2] == ["python", "install"]:
        return 0
    if arguments[:1] == ["sync"]:
        if os.environ.get("FAKE_UV_FAIL") == "sync":
            print("simulated sync failure", file=sys.stderr)
            return 1
        project = Path(arguments[arguments.index("--project") + 1])
        release = re.search(r"fake-release=([^\"\s]+)", (project / "pyproject.toml").read_text()).group(1)
        interpreter = project / ".venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" toolchain {release} "$@"\n')
        interpreter.chmod(0o755)
        return 0
    print(f"unexpected uv arguments: {arguments}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
