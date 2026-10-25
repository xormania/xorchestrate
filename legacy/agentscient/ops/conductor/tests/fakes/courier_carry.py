# testkit: exempt fixture program planted as the bridge's native pass
"""One native courier pass, as the bridge calls it, with planted callbacks.

sessions-lever.md slice 2: the native route is the bridge's in-process
call, `carry(module, submit=...)`, and a crashed attempt is reconciled by
asking the adapter's `observe` for the message in the native session's
evidence. This program is that caller: it loads `courier.py` from the
repository root substituted once at plant, takes the core module the
courier itself uses, and calls `carry(module, submit=submit,
observe=observe)` once, in this process.

The callbacks answer from the registry spec beside this program
(fakes/<name>.spec.json):

- ``observed``: the reading `observe(message)` returns, with ``{message}``
  replaced by the message it was asked about;
- ``submitted``: the reading `submit(row)` returns.

The contract names neither callback's arguments nor the reading that
means found; the rows supply them as data. Each call appends one JSON
line to the registry call log: ``callback``, ``args``, ``reading``.
Prints the pass's result as JSON; a refusal is printed on stderr as its
wire line, exit 2.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = "@@REPO@@"


def spec_path() -> Path:
    exe = Path(sys.argv[0]).resolve()
    return exe.parent.parent / "fakes" / f"{exe.name}.spec.json"


def log(entry: dict) -> None:
    path = spec_path().with_name(spec_path().name.replace(".spec.json", ".calls.jsonl"))
    line = json.dumps(entry, default=str) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def main() -> int:
    spec = json.loads(spec_path().read_text(encoding="utf-8"))
    tools = Path(REPO) / "ops" / "conductor" / "tools"
    sys.path.insert(0, str(tools))
    import courier

    def observe(*args, **kwargs):
        message = args[0] if args else kwargs.get("message")
        reading = str(spec.get("observed", "")).replace("{message}", str(message))
        log({"callback": "observe", "args": list(args), "kwargs": kwargs, "reading": reading})
        return reading

    def submit(*args, **kwargs):
        reading = str(spec.get("submitted", ""))
        log({"callback": "submit", "args": list(args), "kwargs": kwargs, "reading": reading})
        return reading

    try:
        module = courier.core()
        passed = courier.carry(module, submit=submit, observe=observe)
    except courier.Refusal as refusal:
        sys.stderr.write(refusal.line() + "\n")
        return 2
    sys.stdout.write(json.dumps(passed, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
