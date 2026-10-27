"""Input snapshots for the managed composition boundary."""

import json
import sys


def frozen_plan(tmp_path, content, *, prepare):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = tmp_path / "operator" / "native.json"
    source.parent.mkdir()
    source.write_text(content)
    settings = {"root": str(candidate), "output": str(tmp_path / "runs"), "suite": "static", "mode": "native",
                "config": str(source), "brief": None, "drivers": [], "families": [], "timeout": 2,
                "check_timeout": 3, "run_timeout": None, "shutdown_grace": 1, "interpreter": sys.executable}
    directory = prepare(settings)
    return source, directory, json.loads((directory / "plan.json").read_text())


def native_configuration():
    return json.dumps({"sessions": {"codex": {"adapter": "codex", "options": {
        "command": [sys.executable], "cwd": "workspace", "application_tools": True}}}})
