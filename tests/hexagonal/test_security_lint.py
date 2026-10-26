"""Calibrate shared security checks against independent unsafe/safe samples."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.testkit.adapters.evidence import RecordingRunner
from scripts.testkit.adapters.static import definitions, prepare_static
from tests.hexagonal.support.static_checks import static_subject

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
    def test_shared_check_rejects_unsafe_code_in_runtime_and_nested_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = static_subject(base / "candidate")
            evidence = base / "evidence"
            evidence.mkdir()
            definition = next(check for check in definitions(directory=evidence) if check.name == "ruff")
            runner = RecordingRunner(evidence / "commands")
            environment = {"PYTHONPYCACHEPREFIX": str(base / "pycache")}
            expected = set()
            for prefix in ("xorchestrate", "tests/hexagonal", ".github/actions/security-probe", "scripts"):
                (root / prefix).mkdir(parents=True, exist_ok=True)
                for code, (unsafe, _) in SAMPLES.items():
                    path = root / prefix / (code.lower() + ".py")
                    path.write_text(unsafe)
                    expected.add((code, str(path)))
            prepared, _ = prepare_static(definition, root=root, directory=evidence,
                                         runner=runner, env=environment)
            unsafe = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=30)
            self.assertEqual(1, unsafe.returncode, unsafe.stdout + unsafe.stderr)
            findings = {(item["code"], item["filename"]) for item in json.loads((evidence / "ruff.json").read_text())}
            self.assertLessEqual(expected, findings, f"Missing security findings: {expected - findings}")
            self.assertEqual(set(SAMPLES), {code for code, _ in findings})
            for code, path in expected:
                Path(path).write_text(SAMPLES[code][1])
            prepared, _ = prepare_static(definition, root=root, directory=evidence,
                                         runner=runner, env=environment)
            safe = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=30)
            self.assertEqual(0, safe.returncode, safe.stdout + safe.stderr)
            self.assertEqual([], json.loads((evidence / "ruff.json").read_text()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
