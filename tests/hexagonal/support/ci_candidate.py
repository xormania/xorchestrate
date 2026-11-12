"""Independent Git and GitHub observations for candidate-evidence contracts."""

import io
import json
import stat
import subprocess
import zipfile
from copy import deepcopy
from pathlib import Path


def git(root, *arguments):
    return subprocess.run(["git", *arguments], cwd=root, check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def commit(root, files, message):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--message", message)
    return git(root, "rev-parse", "HEAD")


def candidate_repository(root, *, extra_files=None):
    """Create a real two-parent integration, with observable source/base identities."""
    root.mkdir()
    git(root, "init", "--quiet", "--initial-branch=master")
    git(root, "config", "user.name", "candidate fixture")
    git(root, "config", "user.email", "candidate@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    initial = commit(root, {
        ".github/workflows/ci.yml": '{"name":"CI","jobs":{}}\n',
        "contracts/testing.json": '{"schema_version":1,"id":"candidate-fixture","families":{}}\n',
        "uv.lock": 'version = 1\nrevision = 1\n',
        "pyproject.toml": '[project]\nname = "candidate-fixture"\nversion = "0.1.0"\n',
        "scripts/ci/checker.py": '"""Fixture checker input."""\n',
        "source.py": "VALUE = 1\n",
        **(extra_files or {}),
    }, "initial")
    git(root, "checkout", "--quiet", "-b", "feature")
    source = commit(root, {"source.py": "VALUE = 2\n"}, "source")
    git(root, "checkout", "--quiet", "master")
    tested_base = commit(root, {"base.py": "BASE = 1\n"}, "base advanced")
    git(root, "merge", "--quiet", "--no-ff", "feature", "--message", "test integration")
    tested_commit = git(root, "rev-parse", "HEAD")
    return {
        "root": root, "initial": initial, "source": source, "tested_base": tested_base,
        "tested_commit": tested_commit, "tree": git(root, "rev-parse", "HEAD^{tree}"),
        "parents": [tested_base, source],
    }


def github_observation(repository, *, event_name="pull_request", attempt=1):
    """GitHub event/run metadata has a separate origin from Git checkout observations."""
    owner = {"id": 1430398736, "full_name": "xormania/xorchestrate"}
    event = {
        "repository": owner, "number": 17, "action": "synchronize",
        "pull_request": {
            "number": 17,
            "head": {"sha": repository["source"], "ref": "feature", "repo": owner},
            "base": {"sha": repository["tested_base"], "ref": "master"},
        },
    }
    env = {
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_SHA": repository["tested_commit"],
        "GITHUB_REF": "refs/pull/17/merge",
        "GITHUB_REPOSITORY": owner["full_name"],
        "GITHUB_REPOSITORY_ID": str(owner["id"]),
        "GITHUB_RUN_ID": "42778000000",
        "GITHUB_RUN_ATTEMPT": str(attempt),
        "GITHUB_WORKFLOW_REF": "xormania/xorchestrate/.github/workflows/ci.yml@refs/pull/17/merge",
        "GITHUB_WORKFLOW_SHA": repository["tested_commit"],
        "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
        "ImageOS": "ubuntu24", "ImageVersion": "20261110.1.0",
    }
    return event, env


def lane_runtime(*, tmux=None):
    return {
        "python": "3.12.16", "implementation": "CPython", "architecture": "x86_64",
        "platform": "Linux-6.8", "uv": "uv 0.13.7", "tmux": tmux,
        "runner": {"os": "Linux", "image": "ubuntu24", "version": "20261110.1.0"},
        "mode": "protocol-fixture",
    }


def artifact_zip(entries):
    """Construct archive bytes independently of the evidence reader."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents, kind in entries:
            member = zipfile.ZipInfo(name)
            member.create_system = 3
            member.external_attr = ((stat.S_IFLNK if kind == "symlink" else stat.S_IFREG) | 0o600) << 16
            archive.writestr(member, contents)
    return output.getvalue()


def lane_lineage(captured):
    """Two original successes plus one successful retry; original attempt was red."""
    context = deepcopy(captured)
    logical = ("static-checks-python-3.12", "framework-tests-python-3.12-shard-1-of-2",
               "framework-tests-python-3.12-shard-2-of-2")
    names = ("Static checks", "Application tests shard 1/2 (Python 3.12)",
             "Application tests shard 2/2 (Python 3.12)")
    expected = dict(zip(logical, names, strict=True))
    manifests, artifacts, jobs = {}, [], []
    for index, (lane, job_name) in enumerate(expected.items(), 1):
        attempt = 2 if index == 3 else 1
        name = lane + "--attempt-" + str(attempt)
        manifests[name] = {"schema_version": 1, "context": deepcopy(context), "lane": lane,
                           "run_attempt": attempt, "runtime": lane_runtime()}
        artifacts.append({"id": 900 + index, "name": name, "digest": "sha256:" + str(index) * 64,
                          "expired": False, "workflow_run": {
                              "id": context["producer"]["run_id"], "head_sha": context["source_sha"],
                          }})
        jobs.append({"id": 100 + index, "name": job_name, "run_attempt": attempt,
                     "status": "completed", "conclusion": "success"})
    jobs.insert(2, {"id": 99, "name": names[2], "run_attempt": 1,
                    "status": "completed", "conclusion": "failure"})
    metadata = {
        "run": {"id": context["producer"]["run_id"], "run_attempt": 2,
                "event": "pull_request", "head_sha": context["source_sha"],
                "repository": deepcopy(context["repository"]), "path": ".github/workflows/ci.yml",
                "check_suite_id": 600, "head_branch": context["pr"]["source_ref"],
                "head_repository": deepcopy(context["source_repository"]),
                "pull_requests": [{"number": context["pr"]["number"],
                                   "head": {"ref": context["pr"]["source_ref"], "sha": context["source_sha"],
                                            "repo": {"id": context["source_repository"]["id"]}},
                                   "base": {"ref": context["pr"]["target_ref"],
                                            "sha": context["event"]["reported_base_sha"],
                                            "repo": {"id": context["repository"]["id"]}}}]},
        "check": {"id": 600, "head_sha": context["producer"]["check_sha"], "app": {"id": 15368}},
        "git": {"sha": context["checkout"]["commit"], "tree": {"sha": context["checkout"]["tree"]},
                "parents": [{"sha": parent} for parent in context["checkout"]["parents"]]},
        "jobs": jobs, "artifacts": artifacts,
        "workflow_bytes_sha256": context["inputs"]["workflow_sha256"],
    }
    return context, manifests, metadata, expected


def native_partial_lineage(captured):
    """Model GitHub's copied completed job records during a static-only rerun.

    Actual producer records and uploaded artifacts are independent inputs. New
    wrapper IDs/attempt numbers do not rewrite original execution timestamps.
    """
    context, _, metadata, expected = lane_lineage(captured)
    manifests, artifacts, original = {}, [], []
    for index, (lane, name) in enumerate(expected.items(), 1):
        runner = 200 + index
        original.append({
            "id": 100 + index, "run_id": context["producer"]["run_id"], "name": name,
            "run_attempt": 1, "head_sha": context["source_sha"],
            "status": "completed", "conclusion": "success",
            "created_at": "2026-11-10T00:00:00Z", "started_at": "2026-11-10T00:01:00Z",
            "completed_at": "2026-11-10T00:02:00Z", "runner_id": runner,
            "runner_name": "runner-" + str(runner), "runner_group_id": 0,
            "steps": [{"number": 1, "name": "Execute " + lane, "status": "completed",
                       "conclusion": "success", "started_at": "2026-11-10T00:01:10Z",
                       "completed_at": "2026-11-10T00:01:50Z"}],
        })
        attempt = 2 if index == 1 else 1
        artifact_name = f"{lane}--attempt-{attempt}"
        manifests[artifact_name] = {
            "schema_version": 1, "context": deepcopy(context), "lane": lane,
            "run_attempt": attempt, "runtime": lane_runtime(),
        }
        artifacts.append({"id": 900 + index, "name": artifact_name,
                          "digest": "sha256:" + str(index) * 64, "expired": False,
                          "workflow_run": {"id": context["producer"]["run_id"], "head_sha": context["source_sha"]}})
    rerun = deepcopy(original[0])
    rerun.update(id=1101, run_attempt=2, created_at="2026-11-10T00:10:00Z",
                 started_at="2026-11-10T00:11:00Z", completed_at="2026-11-10T00:12:00Z",
                 runner_id=301, runner_name="runner-301")
    rerun["steps"][0].update(started_at="2026-11-10T00:11:10Z", completed_at="2026-11-10T00:11:50Z")
    wrappers = []
    for record in original[1:]:
        wrapper = deepcopy(record)
        wrapper.update(id=record["id"] + 1000, run_attempt=2,
                       created_at="2026-11-10T00:10:00Z", runner_group_id=None)
        wrappers.append(wrapper)
    metadata.update(jobs=[*original, rerun, *wrappers], artifacts=artifacts)
    return context, manifests, metadata, expected



def strict_documentation_receipts(root, context):
    context, manifests, metadata, expected = lane_lineage(context)
    lane = "static-checks-python-3.12"
    expected = {lane: expected[lane]}
    # An applicable static lane from the first attempt is retained unchanged.
    for name, manifest in manifests.items():
        if manifest["lane"] == lane:
            write_json(root / name / "producer.json", manifest)
            write_json(root / name / "static/report.json", {
                "schema_version": 2, "suite": "static", "status": "passed",
            })
            write_json(root / name / "static/candidate.json", {
                "commit": context["checkout"]["commit"], "tree": context["checkout"]["tree"], "status": "",
            })
    write_json(root / "candidate-context--attempt-1/context.json", context)
    needs = {name: {"result": "success", "outputs": {}} for name in (
        "paths", "changes", "architecture-status",
    )}
    needs["paths"]["outputs"]["code"] = "false"
    needs.update({name: {"result": "skipped", "outputs": {}} for name in (
        "application-tests", "application", "verification", "tmux-acceptance",
    )})
    workflow = {"jobs": {"changes": {"name": "Static checks"}}}
    return context, metadata, needs, workflow
