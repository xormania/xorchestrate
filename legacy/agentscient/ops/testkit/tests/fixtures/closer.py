"""Close a world that holds one daemon, the way a case does, and report on
the wire. Run from the repository root as a module:

    python3 -m ops.testkit.tests.fixtures.closer READING TIMEOUT HOW [FLAG...]

HOW is ``recorded`` (started through ``world.daemon``), ``adopted``
(started with ``run``, as a subject would, then recorded through
``world.adopt`` by its socket) or ``around`` (started with ``run``, never
recorded). FLAGs go to the planted daemon.
Before close, the daemon's pids and start times and the world's root are
written as JSON to READING, so the caller can prove from outside that close
left nothing running. A clean close exits 0 with nothing on stdout or
stderr; ``DaemonRefused`` exits 2 with its lines on stderr, stdout empty."""

import contextlib
import json
import sys
from pathlib import Path

from ops.testkit import DaemonRefused, World, run
from ops.testkit.world import processes

DAEMON = Path(__file__).resolve().parent / "daemon.py"


def refuse(cls, expected, found, needed):
    sys.stderr.write(f"{cls}: expected {expected}; found {found}; needed {needed}\n")
    return 2


def stage(world, how, timeout, flags):
    """Start the daemon and return {pid: start time} of what it left running."""
    sock = world.home / "d.sock"
    start = [sys.executable, str(DAEMON), "start", str(sock), *flags]
    stop = [sys.executable, str(DAEMON), "stop", str(sock)]
    if how == "recorded":
        return dict(world.daemon("fixture", start, stop, socket=sock, timeout=timeout).pids)
    result = run(start, cwd=world.cwd, env=world.env(), timeout=timeout, expect=0)
    if how == "adopted":
        return dict(world.adopt("fixture", stop, socket=sock, timeout=timeout).pids)
    pid = int(result.stdout.split()[1])
    return {pid: processes()[pid][1]}


def main(argv):
    if len(argv) < 3 or argv[2] not in ("recorded", "adopted", "around"):
        return refuse("arguments", "READING TIMEOUT recorded|adopted|around [FLAG...]", " ".join(argv) or "nothing",
                      "the reading path, the timeout and how the daemon starts")
    reading, timeout, how, flags = Path(argv[0]), float(argv[1]), argv[2], argv[3:]
    world = World(host_path=False)
    record = {"root": str(world.root), "pids": {}}
    try:
        record["pids"] = {str(pid): started for pid, started in stage(world, how, timeout, flags).items()}
    except BaseException:
        with contextlib.suppress(DaemonRefused):
            world.close()
        raise
    finally:
        reading.write_text(json.dumps(record), encoding="utf-8")
    try:
        world.close()
    except DaemonRefused as refused:
        sys.stderr.write("".join(line + "\n" for line in refused.lines))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
