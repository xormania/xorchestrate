"""Isolated source/configuration subjects for shared static-check contracts."""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def write_source(root, relative, source):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def static_subject(root):
    """Create unrelated clean modules under each active root and an explicit policy."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "ruff.toml", root / "ruff.toml")
    (root / ".github").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / ".github/requirements-ci.txt", root / ".github/requirements-ci.txt")
    for relative in ("xorchestrate/subject.py", "scripts/subject.py", "tests/hexagonal/support/subject.py"):
        write_source(root, relative, "VALUE = 1\n")
    profile = {
        "schema_version": 1,
        "id": "isolated-static-subject",
        "version": "1",
        "source_roots": ["xorchestrate", "scripts", "tests"],
        "roles": {
            "subject": {
                "modules": ["xorchestrate.*", "scripts.*", "tests.*"],
                "allowed_roles": ["subject"],
                "allowed_external": ["typing"],
                "allow_dynamic": False,
            },
        },
    }
    write_source(root, "contracts/hexagonal.json", json.dumps(profile))
    return root
