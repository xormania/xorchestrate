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
HEAVY_JOBS = ("application-tests", "application", "verification", "tmux-acceptance", "architecture", "package")
ALWAYS_JOBS = ("changes", "architecture-status", "ci-result")
CLASSIFIER_JOB = "paths"
# Heavy jobs that aggregate other heavy jobs. They still depend on the classifier,
# but use always() so a cancelled or failed dependency is reported, not skipped.
AGGREGATE_JOBS = {"application": [CLASSIFIER_JOB, "application-tests"]}


def full_run(decision, reason=None):
    assert decision["code"] is True, decision
    if reason is not None:
        assert decision["reason"] == reason, decision


def documentation_only(decision, paths):
    assert decision["code"] is False, decision
    assert decision["paths"] == sorted(set(paths))
    assert decision["code_paths"] == []


def heavy_job_gate(job, needs=(CLASSIFIER_JOB,), status="!cancelled()"):
    """A heavy job runs unless the classifier explicitly reported documentation-only."""
    assert job.get("needs") == list(needs), job.get("needs")
    gate = job["if"]
    assert gate.startswith("${{ " + status + " && "), gate
    assert "needs.paths.outputs.code != 'false'" in gate, gate
    assert "== 'true'" not in gate, "Classifier failure must not skip heavy validation"


GUARD_PREFIXES = ("scripts/ci/", ".github/", "contracts/", "tests/")
GUARDED_PATHS = ("scripts/ci/classify_changes.py", ".github/CI.md", ".github/workflows/ci.yml",
                 "contracts/testing.json", "tests/hexagonal/contracts/ci_changes.py")


def classifier_output(expression):
    """The job output is documentation-only only when the guard and the classifier both say so."""
    assert "steps.guard.outputs.force == 'false'" in expression, expression
    assert "steps.classify.outputs.code" in expression
    assert expression.rstrip(" }").endswith("|| 'true'"), "A missing result must select the full pipeline"


def independent_guard(step):
    """The guard is inline shell over the same base and never invokes the classifier."""
    assert step.get("shell") == "bash"
    assert "scripts.ci" not in step["run"] and "python" not in step["run"]
    assert step["run"].startswith("force=true\n"), "The guard must default to a full run"
    assert "$PULL_REQUEST_BASE" in step["run"] and "git diff --name-only --no-renames -z" in step["run"]
    for prefix in GUARD_PREFIXES:
        assert prefix.replace(".", "\\.") in step["run"], prefix


def always_running(job, *, needs):
    assert job.get("needs", []) == needs
    assert "needs.paths" not in job.get("if", "")
