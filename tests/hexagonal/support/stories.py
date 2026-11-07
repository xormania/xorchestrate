"""Real tmux story subjects and a dispatch-boundary observation adapter."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.hexagonal.support.managed_workflow import json_lines

ROOT = Path(__file__).resolve().parents[3]


def acceptance(output, story, *options):
    result = subprocess.run(
        [sys.executable, "-m", "scripts.acceptance", "--output", str(output), "--story", story,
         "--timeout", "30", "--shutdown-grace", "1", *options],
        cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=45)
    rows = json_lines(result.stdout)
    reports = list(output.glob("*/report.json")) if output.exists() else []
    directory = reports[0].parent if len(reports) == 1 else None
    scenario = json.loads((directory / "scenario.json").read_text()) if directory and (directory / "scenario.json").exists() else None
    report = json.loads(reports[0].read_text()) if directory else None
    return result, rows, scenario, report, directory


class DispatchProbe:
    def __init__(self, directory, **limits):
        from scripts.acceptance.scenario import Client, Scenario

        self.scenario = Scenario(directory, 30, "fixture", **limits)
        self.budget = self.scenario.budget
        self.dispatched = []
        self.client = Client.__new__(Client)
        self.client.budget = self.budget

        def call(method, arguments):
            self.dispatched.append((method, arguments))
            return {"isError": False, "structuredContent": {"ok": True}}

        self.client.call = call

    def reject(self, action, arguments):
        from scripts.acceptance.scenario import AcceptanceFailure

        with pytest.raises(AcceptanceFailure, match="budget"):
            self.client.tool(action, arguments)
