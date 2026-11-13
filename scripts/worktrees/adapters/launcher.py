"""Start a server in its own session with its output in a log file."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence


class PopenLauncher:
    def spawn(self, argv: Sequence[str], cwd: str, env: Mapping[str, str], log: str) -> int:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "wb") as output:  # each attempt starts its own log
            process = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        return process.pid
