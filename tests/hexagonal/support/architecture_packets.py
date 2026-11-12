"""Measured generic package and Git fixtures for coherent packet contracts."""
from __future__ import annotations

import subprocess

from scripts.architecture.python import check
from tests.hexagonal.support.architecture_context import commit_source_baseline


def measure(root, sources, roles=None):
    for path, source in sources.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source)
    if roles is None:
        roles = {"application": ["sample.*"]}
    profile = {
        "schema_version": 1, "id": "fixture.coherent-packets", "version": 1,
        "source_roots": ["sample", "checks"] if any(path.startswith("checks/") for path in sources) else ["sample"],
        "roles": {name: {"modules": modules, "allowed_roles": list(roles),
                         "allowed_external": ["stdlib:*", "pytest"], "allow_dynamic": False}
                  for name, modules in roles.items()},
    }
    return check(root, profile)


def role_competition(root):
    sources = {
        "sample/aaa_tools.py": "\n".join(f"def helper_{index}():\n    return {index}\n" for index in range(20)),
        "sample/domain.py": "def execute(port):\n    return port.read()\n\ndef coordinate(port):\n    return port.write()\n",
        "sample/driver.py": "def store(value):\n    return value\n\ndef retrieve(value):\n    return value\n",
    }
    report = measure(root, sources, {"tools": ["sample.aaa_tools"], "application": ["sample.domain"], "adapters": ["sample.driver"]})
    ranges = [{"path": path, "start_line": 1, "end_line": source.count("\n")} for path, source in sources.items()]
    return report, ranges


def baseline(root, sources, roles=None):
    measure(root, sources, roles)
    commit_source_baseline(root)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def binding_chain(root):
    sources = {
        "sample/subject.py": "class Subject:\n    def execute(self):\n        return 'ok'\n",
        "checks/contracts/consumer.py": "def consumer_contract(subject):\n    assert subject.execute() == 'ok'\n",
        "checks/test_consumer.py": (
            "import pytest\n"
            "from checks.contracts.consumer import consumer_contract\n"
            "from checks.support.consumer import make_subject\n\n"
            "CHECKS = [consumer_contract]\n\n"
            "@pytest.fixture\n"
            "def subject():\n    return make_subject()\n\n"
            "@pytest.mark.parametrize('contract', CHECKS)\n"
            "def test_consumer(contract, subject):\n    contract(subject)\n"
        ),
        "checks/support/consumer.py": "from sample.subject import Subject\n\ndef make_subject():\n    return Subject()\n",
        "checks/support/unrelated.py": "def make_subject():\n    return object()\n",
    }
    roles = {"application": ["sample.*"], "contracts": ["checks.contracts.*"],
             "wrappers": ["checks.test_*"], "fixtures": ["checks.support.*"]}
    return measure(root, sources, roles), sources
