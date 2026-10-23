"""Runner checks use fake metadata and commands, never the host's services."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runners.sh"
SENTINEL = "runner-test-secret-must-not-leak"


class RunnerScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runner-script-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runners = self.root / "runner directories"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.states = self.root / "states.json"
        self.states.write_text(json.dumps({"1": "active", "2": "inactive"}))
        for index in (1, 2):
            directory = self.runners / f"ag{index}"
            directory.mkdir(parents=True)
            (directory / ".runner").write_text(json.dumps({
                "gitHubUrl": "https://github.com/uscient", "agentName": f"runner-{index}",
                "irrelevant_secret": SENTINEL,
            }), encoding="utf-8-sig")
        systemctl = self.bin / "systemctl"
        systemctl.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["RUNNER_TEST_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
statefile = pathlib.Path(os.environ["RUNNER_TEST_STATES"])
states = json.loads(statefile.read_text())
index = args[2].split("@")[1].split(".")[0]
if args[1] == "start":
    if os.environ.get("RUNNER_TEST_START_FAIL"):
        print(os.environ["RUNNER_TEST_SECRET"], file=sys.stderr)
        sys.exit(1)
    states[index] = "active"
    statefile.write_text(json.dumps(states))
else:
    active = states[index]
    directory = pathlib.Path(os.environ["RUNNER_TEST_ROOT"]) / ("ag" + index)
    if os.environ.get("RUNNER_TEST_WRONG_UNIT"):
        directory = pathlib.Path("/wrong-directory")
    print("LoadState=loaded")
    print("ActiveState=" + active)
    print("SubState=" + ("running" if active == "active" else "dead"))
    print("MainPID=" + ("42" if active == "active" else "0"))
    print("WorkingDirectory=" + str(directory))
''')
        systemctl.chmod(0o755)
        docker = self.bin / "docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$RUNNER_TEST_SECRET"\nexit "${RUNNER_TEST_DOCKER_FAIL:-0}"\n')
        docker.chmod(0o755)
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "RUNNER_TEST_LOG": str(self.log), "RUNNER_TEST_STATES": str(self.states),
            "RUNNER_TEST_ROOT": str(self.runners), "RUNNER_TEST_SECRET": SENTINEL,
        }
        source = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        source = source.rsplit("raise SystemExit(main())", 1)[0]
        self.module = types.ModuleType("runner_script_under_test")
        # Load only the embedded Python from this repository's trusted runner script.
        exec(compile(source, str(SCRIPT), "exec"), self.module.__dict__)  # noqa: S102

    def invoke(self, command="status", extra_env=None):
        output = io.StringIO()
        argv = [str(SCRIPT), command, "--runner-root", str(self.runners), "--count", "2"]
        with (patch.dict(os.environ, self.env | (extra_env or {})),
              patch.object(sys, "argv", argv), patch.object(self.module.os, "geteuid", return_value=1000),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(output)):
            code = self.module.main()
        self.assertNotIn(SENTINEL, output.getvalue())
        return code, output.getvalue()

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_status_reports_inactive_without_starting_then_start_is_idempotent(self):
        code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("agentscient-runner@2.service: not running", output)
        self.assertFalse(any("start" in call for call in self.calls()))
        code, output = self.invoke("start")
        self.assertEqual(code, 0, output)
        self.assertIn("started and running", output)
        starts = [call for call in self.calls() if "start" in call]
        self.assertEqual(starts, [["--user", "start", "agentscient-runner@2.service"]])
        code, output = self.invoke("start")
        self.assertEqual(code, 0, output)
        self.assertEqual([call for call in self.calls() if "start" in call], starts)

    def test_invalid_registration_prevents_any_start(self):
        (self.runners / "ag1" / ".runner").write_text(json.dumps({
            "agentName": "other", "gitHubUrl": f"https://{SENTINEL}@github.com/uscient",
        }))
        code, output = self.invoke("start")
        self.assertEqual(code, 1)
        self.assertIn("No services started", output)
        self.assertFalse(any("start" in call for call in self.calls()))

    def test_wrong_service_directory_prevents_start(self):
        code, output = self.invoke("start", {"RUNNER_TEST_WRONG_UNIT": "1"})
        self.assertEqual(code, 1)
        self.assertIn("WorkingDirectory does not match", output)
        self.assertFalse(any("start" in call for call in self.calls()))

    def test_failed_start_and_docker_failures_are_redacted(self):
        code, output = self.invoke("start", {"RUNNER_TEST_START_FAIL": "1", "RUNNER_TEST_DOCKER_FAIL": "1"})
        self.assertEqual(code, 1)
        self.assertIn("start did not establish", output)
        self.assertIn("FAIL Docker", output)

    def test_sourcing_preserves_errexit_parent_and_makes_no_service_calls(self):
        result = subprocess.run(
            ["bash", "-c", 'set -e; source "$1" start; printf "parent-survived\\n"', "bash", str(SCRIPT)],
            text=True, capture_output=True, env=os.environ | self.env, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("parent-survived", result.stdout)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
