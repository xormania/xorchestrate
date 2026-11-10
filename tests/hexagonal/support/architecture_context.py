"""Measured source and evaluator adapters for diagnostic context contracts."""
from __future__ import annotations

import hashlib
import subprocess
from copy import deepcopy

from xorchestrate.model import Assessment


def measured_sources(root):
    preamble = "from .ports import Store\n\n" + "UNCHANGED = 'padding'\n" * 180
    changed = "@trace\ndef changed(port: Store):\n    value = port.store('work')\n    return value\n"
    sources = {
        "pkg/work.py": preamble + changed + "\ndef invoke(port):\n    return changed(port)\n",
        "pkg/ports.py": "class Store:\n    def store(self, value):\n        raise NotImplementedError\n",
        "tests/test_work.py": "from pkg.work import changed\n\ndef test_changed():\n    assert changed(FakePort()) == 'work'\n",
        "pkg/adapters/disk.py": "def store(value):\n    return value\n",
    }
    for path, source in sources.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source)
    changed_line = preamble.count("\n") + 1
    return sources, changed_line, changed


def source_report(sources):
    files = []
    for path, source in sources.items():
        role = "tests" if path.startswith("tests/") else "adapters" if "/adapters/" in path else "core"
        files.append({"path": path, "module": path[:-3].replace("/", "."), "role": role,
                      "sha256": hashlib.sha256(source.encode()).hexdigest()})
    return {
        "status": "pass", "summary": {"violations": 0}, "findings": [],
        "subject": {"source_sha256": "measured-subject", "contract_sha256": "declared-contract"},
        "profile": {"id": "fixture.context", "roles": {
            "core": {"allow_dynamic": False, "allowed_roles": ["core"]},
            "adapters": {"allow_dynamic": True, "allowed_roles": ["core", "adapters"]},
            "tests": {"allow_dynamic": False, "allowed_roles": ["core", "tests"]}}},
        "files": files, "coverage": {"modules": len(files), "limitations": ["Static source only."]},
    }


def commit_source_baseline(root):
    for args in (["init", "-q"], ["config", "user.name", "Fixture"], ["config", "user.email", "fixture@example.invalid"],
                 ["add", "."], ["commit", "-qm", "source baseline"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class RecordingEvaluator:
    """A declared choice provider, not an oracle about fixture source quality."""

    calls = []
    choice = "insufficient_evidence"

    def __init__(self, options):
        self.options = deepcopy(options)
        self.endpoint, self.model, self.criteria = options["endpoint"], options["model"], deepcopy(options["criteria"])

    def assess(self, question, context):
        self.calls.append({"question": question, "context": deepcopy(context)})
        return Assessment(self.choice, 0.7, "", (), {"model": self.options["model"],
                          "probabilities": {key: int(key == self.choice) for key in self.criteria}})

    def close(self):
        pass
