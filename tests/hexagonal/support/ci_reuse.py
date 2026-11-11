"""Authenticated evidence doubles for the pure reuse decision boundary.

Git checkout and artifact authentication are exercised by the shared candidate
contracts; this fixture supplies their already-qualified output independently.
"""

from copy import deepcopy
from urllib.parse import unquote

from scripts.ci.reuse import select_run
from tests.hexagonal.support.ci_candidate import lane_runtime

WORKFLOW = ".github/workflows/ci.yml"
TMUX_LANE = "tmux-acceptance-evidence"
TEST_LANE = "framework-tests-python-3.12-shard-1-of-4"


def candidate_context(run_id=301, *, action="edited"):
    repository = {"id": 1430398736, "full_name": "xormania/xorchestrate"}
    source, base, merge, tree = (letter * 40 for letter in "abcd")
    return {"schema_version": 1, "repository": repository, "source_repository": deepcopy(repository),
        "pr": {"number": 17, "source_ref": "feature", "target_ref": "master"},
        "source_sha": source, "tested_base_sha": base,
        "checkout": {"kind": "pr_merge", "commit": merge, "tree": tree, "parents": [base, source], "status": ""},
        "event": {"name": "pull_request", "action": action, "reported_base_sha": base},
        "producer": {"run_id": run_id, "run_attempt": 1, "check_sha": source,
            "workflow_ref": "xormania/xorchestrate/.github/workflows/ci.yml@refs/pull/17/merge", "workflow_sha": merge},
        "inputs": {key + "_sha256": "e" * 64 for key in ("workflow", "catalog", "lock", "profile", "checker")}}


def metadata_title(context, action):
    return (f"CI:pull_request:{action}:{context['pr']['number']}:{context['source_sha']}:"
            f"{context['event']['reported_base_sha']}")


def current_pull_request(context):
    return {"number": context["pr"]["number"], "state": "open", "draft": True,
        "head": {"sha": context["source_sha"], "ref": context["pr"]["source_ref"],
            "repo": deepcopy(context["source_repository"])},
        "base": {"sha": context["tested_base_sha"], "ref": context["pr"]["target_ref"]},
        "merge_commit_sha": context["checkout"]["commit"]}


class CurrentPRObservations:
    """Independent PR snapshot and canonical branch responses, with explicit owners."""

    def __init__(self, context, pr=None):
        self.context = deepcopy(context)
        self.pr = current_pull_request(context) if pr is None else deepcopy(pr)
        self.requests = []
        self.source_key = (context["source_repository"]["full_name"], "refs/heads/" + context["pr"]["source_ref"])
        self.target_key = (context["repository"]["full_name"], "refs/heads/" + context["pr"]["target_ref"])
        self.refs = {key: {"ref": key[1], "object": {"type": "commit", "sha": sha}} for key, sha in (
            (self.source_key, context["source_sha"]), (self.target_key, context["tested_base_sha"]))}

    def __call__(self, context, token, path):
        repository = context["repository"]["full_name"]
        self.requests.append((repository, path))
        if path == f"/pulls/{self.context['pr']['number']}":
            assert repository == self.context["repository"]["full_name"], "PR observations belong to the target repository"
            return deepcopy(self.pr)
        prefix = "/git/ref/heads/"
        if path.startswith(prefix):
            key = (repository, "refs/heads/" + unquote(path.removeprefix(prefix)))
            if key not in self.refs:
                raise OSError("Canonical branch is unavailable")
            result = self.refs[key]
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)
        raise AssertionError("Unexpected current-candidate API request: " + path)


class ReuseEvidence:
    def __init__(self):
        self.current = candidate_context()
        self.runtime = {TEST_LANE: lane_runtime(), TMUX_LANE: lane_runtime(tmux="tmux 3.4")}
        self.bundles = {}
        self.inspected = []

    def current_run(self):
        return {"id": 301, "run_number": 4, "run_attempt": 1, "event": "pull_request", "path": WORKFLOW,
            "head_sha": self.current["source_sha"], "status": "in_progress", "conclusion": None,
            "attempt_started_at": "2026-11-10T12:00:04Z"}

    def add_run(self, identifier=101, number=1, *, status="completed", conclusion="success",
                action="synchronize", jobs=None, context=None, attempt=1, started_at=None):
        original = deepcopy(context) if context is not None else candidate_context(identifier, action=action)
        original["producer"]["run_attempt"] = attempt
        lanes = {lane: {"schema_version": 1, "lane": lane, "run_attempt": attempt, "context": deepcopy(original),
            "runtime": deepcopy(runtime)} for lane, runtime in self.runtime.items()}
        references = [{"lane": lane, "decision": "executed", "run_id": identifier, "run_attempt": attempt,
            "job_id": 1000 + index, "artifact_id": 2000 + index, "artifact_name": lane + f"--attempt-{attempt}",
            "artifact_digest": "sha256:" + "f" * 64, "runtime": deepcopy(manifest["runtime"]),
            "original_context": deepcopy(original)} for index, (lane, manifest) in enumerate(lanes.items())]
        self.bundles[identifier] = {"context": original, "qualified": True,
            "result": {"schema_version": 2, "status": "passed", "candidate_context": deepcopy(original),
                "evidence_references": deepcopy(references)},
            "jobs": [{"id": 1000, "name": "Application tests", "status": "completed", "conclusion": "success"}]
            if jobs is None else deepcopy(jobs), "lanes": lanes, "references": references}
        return {"id": identifier, "run_number": number, "run_attempt": attempt, "event": "pull_request", "path": WORKFLOW,
            "head_sha": original["source_sha"], "status": status, "conclusion": conclusion,
            "display_title": metadata_title(original, action),
            "attempt_started_at": started_at or f"2026-11-10T12:00:{number:02d}Z"}

    def inspect(self, run):
        self.inspected.append(run["id"])
        value = self.bundles[run["id"]]
        if isinstance(value, Exception):
            raise value
        return value

    def select(self, runs, *, enabled=True):
        return select_run(runs, self.current, self.inspect, runtime=self.runtime, enabled=enabled)

    def prepared(self):
        run = self.add_run()
        plan = self.select([run])
        plan["result_artifact"] = {"id": 3000, "digest": "sha256:" + "f" * 64}
        return plan, self.bundles[run["id"]]
