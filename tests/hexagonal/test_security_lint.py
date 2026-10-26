"""Calibrate the installed security linter against the actual CI command/config."""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# These snippets are parsed by Ruff, never imported or executed.
SAMPLES = {
    "S102": ('exec("value = 1")\n', 'import json\n\njson.loads("1")\n'),
    "S103": ('import os\n\nos.chmod("example", 0o777)\n',
             'import os\n\nos.chmod("example", 0o600)\n'),
    "S202": ('import tarfile\n\nwith tarfile.open("example.tar") as archive:\n    archive.extractall()\n',
             'import tarfile\n\nwith tarfile.open("example.tar") as archive:\n    archive.extractall(filter="data")\n'),
    "S301": ('import pickle\n\npickle.loads(b"example")\n', 'import json\n\njson.loads("1")\n'),
    "S306": ('import tempfile\n\ntempfile.mktemp()\n', 'import tempfile\n\ntempfile.mkstemp()\n'),
    "S307": ('eval("1 + 1")\n', 'import ast\n\nast.literal_eval("1")\n'),
    "S323": ('import ssl\n\nssl._create_unverified_context()\n', 'import ssl\n\nssl.create_default_context()\n'),
    "S324": ('import hashlib\n\nhashlib.md5(b"example")\n', 'import hashlib\n\nhashlib.sha256(b"example")\n'),
    "S501": ('import requests\n\nrequests.get("https://example.invalid", verify=False, timeout=5)\n',
             'import requests\n\nrequests.get("https://example.invalid", verify=True, timeout=5)\n'),
    "S506": ('import yaml\n\nyaml.load("{}", Loader=yaml.Loader)\n', 'import yaml\n\nyaml.safe_load("{}")\n'),
    "S507": ('import paramiko\n\nclient = paramiko.SSHClient()\n'
             'client.set_missing_host_key_policy(paramiko.AutoAddPolicy())\n',
             'import paramiko\n\nclient = paramiko.SSHClient()\n'
             'client.set_missing_host_key_policy(paramiko.RejectPolicy())\n'),
    "S602": ('import subprocess\n\nsubprocess.run("echo example", shell=True, check=True)\n',
             'import subprocess\n\nsubprocess.run(["echo", "example"], check=True)\n'),
    "S604": ('import custom_process\n\ncustom_process.run("example", shell=True)\n',
             'import custom_process\n\ncustom_process.run(["example"], shell=False)\n'),
    "S605": ('import os\n\nos.system("echo example")\n',
             'import subprocess\n\nsubprocess.run(["echo", "example"], check=True)\n'),
    "S609": ('import subprocess\n\nsubprocess.Popen(["chmod", "777", "*.py"], shell=True)\n',
             'import subprocess\n\nsubprocess.Popen(["chmod", "600", "example.py"], shell=False)\n'),
}


class SecurityLintTests(unittest.TestCase):
    def test_workflow_rejects_unsafe_code_in_runtime_and_nested_actions(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        lint = next(step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "lint")
        # Preserve the actual paths and flags; only replace CI's venv executable.
        command = [sys.executable, *shlex.split(lint["run"])[1:], "--output-format", "json"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "ruff.toml", root / "ruff.toml")
            expected = set()
            for prefix in ("xorchestrate", "tests/hexagonal", ".github/actions/security-probe", "scripts"):
                (root / prefix).mkdir(parents=True)
                for code, (unsafe, _) in SAMPLES.items():
                    path = root / prefix / (code.lower() + ".py")
                    path.write_text(unsafe)
                    expected.add((code, str(path)))
            unsafe = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(1, unsafe.returncode, unsafe.stdout + unsafe.stderr)
            findings = {(item["code"], item["filename"]) for item in json.loads(unsafe.stdout)}
            self.assertLessEqual(expected, findings, f"Missing security findings: {expected - findings}")
            self.assertEqual(set(SAMPLES), {code for code, _ in findings})
            for code, path in expected:
                Path(path).write_text(SAMPLES[code][1])
            safe = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, safe.returncode, safe.stdout + safe.stderr)
            self.assertEqual([], json.loads(safe.stdout))


if __name__ == "__main__":
    unittest.main(verbosity=2)
