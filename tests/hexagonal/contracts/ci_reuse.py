"""Independent exact-candidate reuse scenarios, including event-burst liveness."""

from copy import deepcopy

IDENTITY_CHANGES = (
    ("repository", "id"), ("source_repository", "id"),
    ("pr", "number"), ("pr", "source_ref"), ("pr", "target_ref"),
    ("checkout", "commit"), ("checkout", "tree"),
    ("inputs", "workflow_sha256"), ("inputs", "catalog_sha256"),
    ("inputs", "lock_sha256"), ("inputs", "profile_sha256"), ("inputs", "checker_sha256"),
)
RUNTIME_CHANGES = (
    ("python",), ("implementation",), ("architecture",), ("platform",), ("uv",), ("mode",),
    ("runner", "os"), ("runner", "image"), ("runner", "version"), ("tmux",),
)
MATERIAL_STATES = (("completed", "failure"), ("completed", "cancelled"),
                   ("in_progress", None), ("queued", None))


def replace_path(value, path, replacement):
    changed = deepcopy(value)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return changed


def assert_reused(result, run_id):
    assert result["decision"] == "reuse", "CI_REUSE_HEALTHY_EXACT_CANDIDATE"
    assert result["producer"]["run_id"] == run_id, "CI_REUSE_ORIGINAL_PRODUCER"


def assert_execute(result):
    assert result["decision"] == "execute", "CI_REUSE_UNPROVED_MUST_EXECUTE"


def assert_metadata_burst(subject, action):
    """A validates; B/C metadata wait or are replaced; current D reuses A."""
    original = subject.add_run(101, 1)
    first = subject.add_run(201, 2, conclusion="cancelled", action=action, jobs=[])
    second = subject.add_run(202, 3, conclusion="cancelled", action=action, jobs=[])
    result = subject.select([first, original, subject.current_run(), second])
    assert_reused(result, original["id"])
    assert [row["run_id"] for row in result["history"]] == [202, 201, 101], "CI_REUSE_EVENT_ORDER"
    assert [row.get("classification") for row in result["history"][:2]] == [
        "superseded_unstarted_metadata", "superseded_unstarted_metadata"], "CI_REUSE_METADATA_NEUTRALITY"
    assert subject.inspected == [202, 201, 101], "CI_REUSE_CURRENT_RUN_EXCLUDED"
