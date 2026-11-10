"""Repository admission precedes remote observation and credential resolution."""

READ_ENTRIES = (("direct", "pr.inspect"), ("provider", "pr.handoff"),
                ("application", "pr.inspect"), ("application", "pr.handoff"),
                ("mcp", "pr.inspect"), ("mcp", "pr.handoff"))


def assert_read_denied(subject, boundary, operation, repository):
    before = subject.io_counts
    result = subject.read(boundary, operation, repository)
    assert subject.io_counts == before, "PR_READ_SCOPE_MUST_DENY_BEFORE_HOST_TOKEN_OR_HTTP"
    error = result.get("error", {})
    assert error.get("details", {}).get("cause", error.get("code")) == "pr_repository_denied", "PR_READ_SCOPE_EXPLICIT_DENIAL"
    if boundary in {"application", "mcp"}:
        assert error["code"] == "operation_failed"
        assert error["details"]["outcome"] == "failed", "PR_READ_SCOPE_EFFECT_DID_NOT_START"
    else:
        assert error["details"]["outcome"] == "not_started"


def assert_read_allowed(subject, boundary, operation, repository, *, qualified=True):
    before = subject.io_counts
    result = subject.read(boundary, operation, repository)
    assert "error" not in result, "PR_READ_SCOPE_ALLOWED_REPOSITORY"
    assert all(after > prior for after, prior in zip(subject.io_counts, before, strict=True)), "PR_READ_SCOPE_ALLOWED_PATH_REACHES_REAL_HOST"
    if qualified:
        assert result["result"]["status"] == "observed"
        assert result["result"]["technical_validation"] == "passed"
