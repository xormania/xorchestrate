# testkit: exempt fixture program planted through Tools
"""Stand-in for ``dispatch.sh`` (sessions-lever.md, slice 3).

One JSON argv line per call, on the kit log beside this plant. The spec
(``fakes/<name>.spec.json``) selects the mode:

- ``launch`` (default): print ``record: <id>`` and ``launcher-pid: <pid>``,
  and write ``<jobs root>/<id>/state.json`` whose ``correlation`` is the
  ``--correlation`` argument. ``<id>`` is the spec key ``record`` when
  set, else ``rec-`` and the correlation's alphanumerics
- ``refuse``: print ``line`` on stderr and exit ``code`` (default 3),
  with no ``record:`` line
- ``no-record``: exit ``code`` (default 0) with no ``record:`` line
- ``write-die``: write the state file, then exit ``code`` (default 1)
  with no ``record:`` line

The jobs root is ``DISPATCH_JOBS``, else ``$XDG_STATE_HOME/<lane>/dispatch``.
"""

from __future__ import annotations

import json
import os
import re
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


def flag(argv: list[str], name: str) -> str | None:
    prefix = name + "="
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def lane_name() -> str:
    conf = os.environ.get("LANE_CONF", "")
    if not conf:
        return ""
    try:
        text = Path(conf).read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"^LANE_NAME='([^']*)'", text, re.MULTILINE)
    return match.group(1) if match else ""


def jobs_root() -> Path:
    named = os.environ.get("DISPATCH_JOBS", "")
    if named:
        return Path(named)
    state = os.environ.get("XDG_STATE_HOME", "")
    lane = lane_name()
    if state and lane:
        return Path(state) / lane / "dispatch"
    return Path("dispatch")


def record_id(correlation: str | None, spec: dict) -> str:
    if spec.get("record"):
        return str(spec["record"])
    if not correlation:
        return "rec-none"
    return "rec-" + re.sub(r"[^0-9a-zA-Z]", "", correlation)


def write_state(correlation: str | None, spec: dict) -> str:
    ident = record_id(correlation, spec)
    directory = jobs_root() / ident
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "state.json"
    path.write_text(
        json.dumps({"correlation": correlation}) + "\n",
        encoding="utf-8",
    )
    return ident


def main() -> int:
    argv = sys.argv[1:]
    path = spec_path()
    log_call(path, argv)
    spec = load_spec(path)
    mode = str(spec.get("mode") or "launch")
    correlation = flag(argv, "--correlation")
    if mode == "refuse":
        line = str(spec.get("line") or "refused")
        sys.stderr.write(line if line.endswith("\n") else line + "\n")
        return int(spec.get("code") or 3)
    if mode == "no-record":
        return int(spec.get("code") or 0)
    if mode == "write-die":
        write_state(correlation, spec)
        return int(spec.get("code") or 1)
    ident = write_state(correlation, spec)
    sys.stdout.write(f"record: {ident}\n")
    sys.stdout.write(f"launcher-pid: {os.getpid()}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
