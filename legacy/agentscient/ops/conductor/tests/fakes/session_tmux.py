# testkit: exempt fixture program planted as tmux
"""Private-socket tmux shim: attach-session is a no-op so attach rows
do not hang a tty-less runner; every other argv execs the host tmux.
"""
from __future__ import annotations

import json
import os
import sys
import time

REAL = "@@TMUX@@"
LOG = "@@LOG@@"
REPO = "@@REPO@@"


def main() -> int:
    argv = sys.argv[1:]
    line = json.dumps({"at": time.time_ns(), "pid": os.getpid(), "argv": argv}) + "\n"
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    if "kill-server" in argv:
        sys.stderr.write(
            "tmux: expected no kill-server; found kill-server; needed target the owned session\n"
        )
        return 2
    if "attach-session" in argv or (argv and argv[0] == "attach"):
        return 0
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from ops.testkit import run
    result = run(
        [REAL, *argv],
        cwd=os.getcwd(),
        env=os.environ.copy(),
        timeout=30,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.exit


if __name__ == "__main__":
    raise SystemExit(main())
