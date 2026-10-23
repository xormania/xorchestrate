"""One fake registry, driven by data. A planted tool answers by argv
from its spec, injects failure by data, and appends one JSON line per
call to an append-only log; a wrapped real tool is logged the same way.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .world import PlantFailed, a_tool_name, make_executable

KIT_DIR = Path(__file__).resolve().parent

LAUNCHER = """#!{python}
import sys
sys.path.insert(0, {kit!r})
from fake_program import serve
raise SystemExit(serve({spec!r}, {log!r}))
"""

WRAPPER = """#!{python}
import json, os, sys, time
line = json.dumps({{"at": time.time_ns(), "pid": os.getpid(), "argv": sys.argv[1:],
                   "cwd": os.getcwd(), "stdin_sha256": None}}) + "\\n"
fd = os.open({log!r}, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
os.write(fd, line.encode("utf-8"))
os.close(fd)
os.execv({real!r}, [{real!r}, *sys.argv[1:]])
"""


class Tools:
    def __init__(self, world):
        self.world = world
        self.dir = world.root / "fakes"
        self.dir.mkdir(exist_ok=True)

    def spec_path(self, name: str) -> Path:
        return self.dir / f"{name}.spec.json"

    def log_path(self, name: str) -> Path:
        return self.dir / f"{name}.calls.jsonl"

    def plant(self, name: str, *, stdout: str = "", stderr: str = "", exit: int = 0,
              responses=None, delay: float = 0.0, script: str | None = None) -> Path:
        """Plant ``name`` on the world's bin: a program driven by the spec,
        or ``script``'s own text when the case needs a program the spec
        cannot express. Proves the file is executable and intact."""
        name = a_tool_name(name)
        spec = {"stdout": stdout, "stderr": stderr, "exit": int(exit), "delay": float(delay),
                "responses": list(responses or [])}
        spec_path = self.spec_path(name)
        spec_path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
        if json.loads(spec_path.read_text(encoding="utf-8")) != spec:
            raise PlantFailed(f"INVALID: spec for {name} did not land intact")
        log = self.log_path(name)
        if script is None:
            body = LAUNCHER.format(python=sys.executable, kit=str(KIT_DIR),
                                   spec=str(spec_path), log=str(log))
        else:
            body = script
        return self.plant_executable(self.world.bin / name, body)

    def wrap(self, name: str, *, host_path: str | None = None) -> Path:
        name = a_tool_name(name)
        """Plant a wrapper that logs the argv and execs the real tool.
        ``host_path`` is the PATH the real tool is resolved on; a world
        passes the one it read at construction, since its own PATH no
        longer carries the host."""
        host_path = host_path or os.environ.get("PATH", "/usr/bin:/bin")
        real = shutil.which(name, path=host_path)
        if real is None:
            raise PlantFailed(f"INVALID: {name} is not on the host PATH; nothing to wrap")
        body = WRAPPER.format(python=sys.executable, log=str(self.log_path(name)), real=real)
        return self.plant_executable(self.world.bin / name, body)

    def plant_executable(self, path, content: str) -> Path:
        if not content.strip():
            raise PlantFailed(f"INVALID: an empty program cannot be planted at {path}")
        path = self.world.plant_text(path, content, recognisable=content.splitlines()[0][:20])
        make_executable(path)
        if not path.is_file():
            raise PlantFailed(f"INVALID: {path} is not a file")
        return path

    def calls(self, name: str) -> list[dict]:
        """Every call the tool has logged, in the order they were appended."""
        log = self.log_path(name)
        if not log.is_file():
            return []
        return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
