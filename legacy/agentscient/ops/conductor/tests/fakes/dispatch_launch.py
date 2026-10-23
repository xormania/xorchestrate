# testkit: exempt fixture program planted through Tools
"""Stand-in for ``launch.py`` status, wait and collect (sessions-lever.md, slice 3).

One JSON argv line per call, on the kit log beside this plant. Readings
come from the spec (``fakes/<name>.spec.json``), in the shape ``launch.py``
prints, so the slice can surface them:

- ``status <id>`` prints ``<id> <word>`` (or the same as JSON with
  ``--json``). ``word`` is the spec key ``status`` and one of the
  contract's job words, default ``running``.
- ``wait <id>`` follows spec key ``wait``: ``clean`` (default) exits 0
  and prints a clean reading; ``not-clean`` exits 1 and prints
  ``wait_reading``; ``timeout`` exits 2, which is ``launch.py``'s own
  timeout, with no reading. The slice maps that 2 to exit 1 and
  ``wait DI-n timeout after S``.
- ``collect <id> --verify ...`` prints ``collect_reading`` and exits 0.
  The logged argv is how a row sees the verification argv.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def spec_path() -> Path:
    named = os.environ.get("TESTKIT_FAKE_SPEC", "")
    if named and Path(named).is_file():
        return Path(named)
    exe = Path(sys.argv[0]).resolve()
    return exe.parent.parent / "fakes" / f"{exe.name}.spec.json"


def load_spec(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def log_call(path: Path, argv: list[str]) -> None:
    log = path.with_name(path.name.replace(".spec.json", ".calls.jsonl"))
    line = json.dumps({
        "at": time.time_ns(),
        "pid": os.getpid(),
        "argv": argv,
        "cwd": os.getcwd(),
    }) + "\n"
    fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def positional(argv: list[str]) -> str:
    for item in argv[1:]:
        if not item.startswith("-"):
            return item
    return ""


def main() -> int:
    argv = sys.argv[1:]
    path = spec_path()
    log_call(path, argv)
    spec = load_spec(path)
    verb = argv[0] if argv else ""
    ident = positional(argv)
    if verb == "status":
        word = str(spec.get("status") or "running")
        if "--json" in argv:
            sys.stdout.write(json.dumps({"id": ident, "status": word}) + "\n")
        else:
            sys.stdout.write(f"{ident} {word}\n")
        return 0
    if verb == "wait":
        kind = str(spec.get("wait") or "clean")
        if kind == "timeout":
            return 2
        if kind == "not-clean":
            reading = str(
                spec.get("wait_reading")
                or f"{ident} closed attempt=1 exit=1 tripped=true envelope=invalid"
            )
            sys.stdout.write(reading if reading.endswith("\n") else reading + "\n")
            return 1
        sys.stdout.write(
            f"{ident} closed attempt=1 exit=0 tripped=false envelope=ok\n"
        )
        return 0
    if verb == "collect":
        reading = str(spec.get("collect_reading") or "collected")
        sys.stdout.write(reading if reading.endswith("\n") else reading + "\n")
        return 0
    sys.stderr.write(f"launch.py: error: unknown verb {verb}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
