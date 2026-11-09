"""Observable contract for skipping heavy CI validation on documentation-only changes."""

DOCUMENTATION_PATHS = (
    "README.md", "AGENTS.md", "CONTRIB.md", "docs/testing-framework.md", "docs/archive/baseline.json",
    "docs/telemetry-evidence/verification-2026-11-07.md", "examples/README.md", "examples/nested/notes.md",
)
CODE_PATHS = (
    "examples/minimal.json", "contracts/testing.json", ".github/workflows/ci.yml", ".github/CI.md",
    "scripts/ci/classify_changes.py", "tests/hexagonal/test_application.py", "xorchestrate/model.py",
    "xorchestrate/README.md", "pyproject.toml", "ruff.toml", ".gitignore", "docs/implementation/owner-brief.md",
    "README.txt", "docs", "/README.md", "docs/../xorchestrate/model.py", "",
)
HEAVY_JOBS = ("application", "tmux-acceptance", "architecture")
ALWAYS_JOBS = ("changes", "architecture-status")
CLASSIFIER_JOB = "paths"


def full_run(decision, reason=None):
    assert decision["code"] is True, decision
    if reason is not None:
        assert decision["reason"] == reason, decision


def documentation_only(decision, paths):
    assert decision["code"] is False, decision
    assert decision["paths"] == sorted(set(paths))
    assert decision["code_paths"] == []


def heavy_job_gate(job):
    """A heavy job runs unless the classifier explicitly reported documentation-only."""
    assert job.get("needs") == [CLASSIFIER_JOB], job.get("needs")
    gate = job["if"]
    assert "!cancelled()" in gate
    assert "needs.paths.outputs.code != 'false'" in gate, gate
    assert "== 'true'" not in gate, "Classifier failure must not skip heavy validation"


def always_running(job, *, needs):
    assert job.get("needs", []) == needs
    assert CLASSIFIER_JOB not in job.get("needs", [])
    assert "needs.paths" not in job.get("if", "")
