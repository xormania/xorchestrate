"""Pinned Git source and controlled provider observations for operational trials."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from tests.hexagonal.support.ci_candidate import candidate_repository, commit


def operational_catalog(root):
    """Build real immutable source pairs; provider predictions are separate data."""
    repository = candidate_repository(root)
    profile = {
        "schema_version": 1, "id": "operational.fixture", "version": 1, "source_roots": ["pkg"],
        "roles": {"core": {"modules": ["pkg.*"], "allowed_roles": ["core"], "allowed_external": [],
                            "allow_dynamic": False, "description": "Infrastructure-neutral domain behavior."}},
    }
    profile_text = json.dumps(profile, sort_keys=True) + "\n"
    groups = (
        ("storage", "tuning", "neutral_storage", "The core passes domain values to its port without SQL encoding.",
         "def persist(port, value):\n    return port.execute('INSERT INTO work VALUES (?)', [value])\n",
         "def persist(port, value):\n    return port.save(value)\n"),
        ("transport", "heldout", "neutral_transport", "The core passes domain values without a wire-protocol envelope.",
         "def persist(port, value):\n    return port.execute({'jsonrpc': '2.0', 'method': 'save', 'params': value})\n",
         "def persist(port, value):\n    return port.save(value)\n"),
    )
    cases = []
    for group, split, requirement, statement, before, after in groups:
        refs = []
        for name, source in (("before", before), ("after", after)):
            refs.append(commit(root, {"pkg/work.py": source, "contracts/hexagonal.json": profile_text}, group + " " + name))
        for index, source in enumerate((before, after)):
            cases.append({
                "id": group + ("-before" if index == 0 else "-after"), "group": group, "split": split,
                "source_ref": refs[index], "profile_path": "contracts/hexagonal.json",
                "pins": [{"path": "pkg/work.py", "sha256": hashlib.sha256(source.encode()).hexdigest()},
                         {"path": "contracts/hexagonal.json", "sha256": hashlib.sha256(profile_text.encode()).hexdigest()}],
                "selection": [{"path": "pkg/work.py", "start_line": 1, "end_line": 2}],
                "requirements": [{"id": requirement, "statement": statement}],
                "oracle": {"fault": index == 0, "findings": ([{"path": "pkg/work.py", "start_line": 2,
                                                               "end_line": 2, "requirements": [requirement]}]
                                                             if index == 0 else [])},
                "fixture_outcome": "concern" if index == 0 else "no_concern",
                "provenance": {"pr_url": "https://github.com/fixture/operational/pull/17",
                               "finding_url": "https://github.com/fixture/operational/pull/17#discussion_fixture",
                               "reviewed_ref": refs[0], "fixed_ref": refs[1],
                               "regression_selector": "tests/test_work.py::test_" + group,
                               "witness_url": "https://github.com/fixture/operational/commit/" + refs[1]},
            })
    return {"schema_version": 1, "id": "operational-source-controls", "cases": cases}, repository


class SubjectProvider:
    """Return controlled choices while preserving the exact observed packets."""

    def __init__(self, outcome="concern", *, execution=None):
        self.outcome = outcome
        self.execution = execution
        self.calls = []

    def __call__(self, subjects, options):
        self.calls.append({"subjects": deepcopy(subjects), "options": deepcopy(options)})
        return {
            "status": "complete", "assessments": [{"subject_id": item["subject_id"], "outcome": self.outcome,
                                                     "confidence": 0.9} for item in subjects],
            "execution": deepcopy(self.execution) if self.execution is not None else {},
        }


def declared_disposition(row, directory):
    """A trusted-ledger fixture; it does not attest remote command execution."""
    subject = row["subjects"][0]
    record = {"schema_version": 1, "case_id": row["id"], "source_ref": row["source_ref"],
              "requirement_id": subject["requirement_id"], "source_sha256": subject["source"]["sha256"],
              "verdict": "defect_reproduced", "command": ["fixture-assertion"],
              "test_id": "controlled-source-witness", "assertion": "The selected source has the declared fixture defect."}
    path = directory / "declared-witness.json"
    path.write_text(json.dumps(record))
    entry = {"case_id": row["id"], "source_ref": row["source_ref"], "packet_sha256": row["packet_sha256"],
             "subject_id": subject["subject_id"], "status": "confirmed", "reviewer_minutes": 2.5,
             "witness": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    return {"schema_version": 1, "entries": [entry]}, path
