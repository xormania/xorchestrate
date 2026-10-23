"""Task-facing schemas. The GitHub provider's full API is not an agent tool."""


def capabilities(spec):
    text = {"type": "string", "maxLength": 240}
    detail = {"type": "string", "maxLength": 8000}
    ident = {"type": "integer", "minimum": 1}
    page = {"type": "integer", "minimum": 1, "maximum": 10000}
    revision = {
        "type": "string",
        "pattern": "^[0-9a-f]{40}$",
        "description": "The head revision returned by pr_get/pr_diff.",
    }
    repository = {"type": "string", "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", "maxLength": 200}
    scope = {"job": ident, "repository": repository, "pr": ident}
    cursor = {"type": "string", "maxLength": 500}
    return [
        spec(
            "pr_list",
            "Find GitHub PRs in the selected job's repository; return compact state and branch summaries.",
            {
                "job": ident,
                "repository": repository,
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "page": page,
            },
            readonly=True,
        ),
        spec(
            "pr_get",
            "Understand the job's GitHub PR: current revision, reviews, CI and observed merge blockers.",
            {**scope, "description_offset": {"type": "integer", "minimum": 0}},
            readonly=True,
        ),
        spec(
            "pr_open",
            "Open or adopt a GitHub PR and bind it to the job/work order. Reuse an existing PR for the branch.",
            {**scope, "branch": text, "base": text, "title": text, "summary": detail, "draft": {"type": "boolean"}},
        ),
        spec(
            "pr_update",
            "Update the job's GitHub PR description, title, draft/ready state, or close/reopen it.",
            {
                **scope,
                "title": text,
                "body": detail,
                "draft": {"type": "boolean"},
                "state": {"type": "string", "enum": ["open", "closed"]},
            },
        ),
        spec(
            "pr_diff",
            "Inspect GitHub PR changes: file summaries first, or a bounded patch for one path at a known revision.",
            {
                **scope,
                "path": {**text, "maxLength": 2000},
                "revision": revision,
                "page": page,
                "offset": {"type": "integer", "minimum": 0},
            },
            readonly=True,
        ),
        spec(
            "pr_review",
            "Review the inspected GitHub PR revision; comment, approve, or request changes with optional inline findings.",
            {
                **scope,
                "revision": revision,
                "decision": {"type": "string", "enum": ["comment", "approve", "request_changes"]},
                "body": detail,
                "findings": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {**text, "maxLength": 2000},
                            "line": ident,
                            "body": {**detail, "maxLength": 2000},
                            "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
                        },
                        "required": ["path", "line", "body"],
                        "additionalProperties": False,
                    },
                },
            },
            ("revision", "decision", "body"),
        ),
        spec(
            "pr_comment",
            "Post a progress/evidence comment on the job's GitHub PR.",
            {**scope, "body": detail},
            ("body",),
        ),
        spec(
            "pr_comments",
            "Read PR conversation, reviews, or inline comments with bounded previews; fetch a comment's full text in pages.",
            {
                **scope,
                "kind": {"type": "string", "enum": ["conversation", "review", "inline"]},
                "comment": ident,
                "page": page,
                "offset": {"type": "integer", "minimum": 0},
            },
            readonly=True,
        ),
        spec(
            "pr_threads",
            "Read GitHub review discussions or reply/resolve/reopen a thread; pr_comments(kind=inline,comment=databaseId) reads full text.",
            {
                **scope,
                "action": {"type": "string", "enum": ["read", "reply", "resolve", "reopen"]},
                "thread": text,
                "cursor": cursor,
                "body": detail,
            },
        ),
        spec(
            "pr_merge",
            "Merge the inspected GitHub PR revision using an allowed repository merge method and GitHub's rules.",
            {**scope, "revision": revision, "method": {"type": "string", "enum": ["merge", "squash", "rebase"]}},
            ("revision",),
        ),
        spec(
            "ci_status",
            "See GitHub CI for the PR's current head/test-merge revisions; identify pending or failed checks and runs.",
            {**scope, "run": ident, "page": page},
            readonly=True,
        ),
        spec(
            "ci_failures",
            "Diagnose a GitHub CI run: failed steps, annotations and relevant log excerpts, with full logs on demand.",
            {
                **scope,
                "run": ident,
                "check": ident,
                "attempt": ident,
                "page": page,
                "log_offset": {"type": "integer", "minimum": 0},
                "annotation_page": page,
            },
            ("run",),
            readonly=True,
        ),
        spec(
            "ci_workflows",
            "Discover runnable GitHub workflows for the selected repository before starting CI.",
            {"job": ident, "repository": repository, "page": page},
            readonly=True,
        ),
        spec(
            "ci_run",
            "Request a GitHub workflow on the job's published branch; report acceptance, then observe the actual run.",
            {
                **scope,
                "workflow": text,
                "ref": text,
                "inputs": {
                    "type": "object",
                    "maxProperties": 25,
                    "additionalProperties": {"type": "string", "maxLength": 2000},
                },
            },
            ("workflow",),
        ),
        spec(
            "ci_rerun",
            "Retry failed GitHub CI checks (or the entire run) at that run's original revision.",
            {**scope, "run": ident, "failed_only": {"type": "boolean"}},
            ("run",),
        ),
        spec(
            "ci_cancel",
            "Request cancellation of a GitHub CI run; subsequent status confirms when it stops.",
            {**scope, "run": ident},
            ("run",),
        ),
        spec(
            "ci_watch",
            "Watch this PR revision or CI run and receive durable live messages as CI changes or finishes.",
            {**scope, "run": ident, "timeout": {"type": "integer", "minimum": 60, "maximum": 86400}},
        ),
        spec(
            "ci_watch_stop",
            "Stop one of this member's GitHub CI watches; CI itself continues.",
            {"watch": ident},
            ("watch",),
        ),
        spec(
            "ci_artifacts",
            "Find GitHub CI outputs for a run, with expiry and revision information.",
            {**scope, "run": ident, "page": page},
            ("run",),
            readonly=True,
        ),
        spec(
            "ci_artifact_save",
            "Retain a GitHub CI archive or a named report inside it as job evidence, with source/run/revision provenance.",
            {
                **scope,
                "artifact": ident,
                "entry": {**text, "maxLength": 2000},
                "name": text,
                "entry_offset": {"type": "integer", "minimum": 0},
            },
            ("artifact",),
        ),
    ]


NAMES = {row["name"] for row in capabilities(lambda name, *args, **kwargs: {"name": name})}
