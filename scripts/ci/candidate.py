"""Bind CI receipts to observed Git inputs and GitHub's actual lane producers.

The bounded HTTP/archive readers adapt Xorder's ``scripts/validation_base.py``.
This module qualifies execution evidence; it does not decide PR readiness.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SCHEMA = 1
WORKFLOW = ".github/workflows/ci.yml"
MAX_JSON = 4 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_ATTEMPTS = 20


def _positive(value):
    return type(value) is int and value > 0


def _sha(value, length=40):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None


def _git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True).strip()


def _hash(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Validation input must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(root):
    selected = sorted(path for directory in ("scripts/ci", "scripts/testkit")
                      for path in (root / directory).rglob("*.py"))
    if not selected:
        raise ValueError("Validation checker inputs are missing")
    checkers = {path.relative_to(root).as_posix(): _hash(path) for path in selected}
    return {"workflow_sha256": _hash(root / WORKFLOW),
            "catalog_sha256": _hash(root / "contracts/testing.json"),
            "lock_sha256": _hash(root / "uv.lock"),
            "profile_sha256": _hash(root / "pyproject.toml"),
            "checker_sha256": hashlib.sha256(json.dumps(checkers, sort_keys=True).encode()).hexdigest()}


def _repository(value):
    return (isinstance(value, dict) and _positive(value.get("id"))
            and isinstance(value.get("full_name"), str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["full_name"]) is not None)


def validate_context(context):
    """Reject incomplete or contradictory provenance, without claiming freshness."""
    if not isinstance(context, dict) or type(context.get("schema_version")) is not int or context["schema_version"] != SCHEMA:
        raise ValueError("Unsupported candidate context schema")
    if not _repository(context.get("repository")) or not _repository(context.get("source_repository")):
        raise ValueError("Invalid candidate repository identity")
    checkout, event, producer, inputs = (context.get(key) for key in ("checkout", "event", "producer", "inputs"))
    if not all(isinstance(value, dict) for value in (checkout, event, producer, inputs)):
        raise ValueError("Candidate context is incomplete")
    parents = checkout.get("parents")
    if (not _sha(context.get("source_sha")) or not _sha(checkout.get("commit")) or not _sha(checkout.get("tree"))
            or checkout.get("status") != "" or not isinstance(parents, list) or len(parents) > 16
            or not all(_sha(parent) for parent in parents)):
        raise ValueError("Invalid or dirty actual checkout identity")
    if (not all(_sha(inputs.get(key), 64) for key in
                ("workflow_sha256", "catalog_sha256", "lock_sha256", "profile_sha256", "checker_sha256"))
            or not _positive(producer.get("run_id")) or not _positive(producer.get("run_attempt"))
            or not _sha(producer.get("workflow_sha")) or producer.get("check_sha") != context["source_sha"]):
        raise ValueError("Invalid candidate input or producer identity")
    workflow_prefix = context["repository"]["full_name"] + "/" + WORKFLOW + "@"
    if not isinstance(producer.get("workflow_ref"), str) or not producer["workflow_ref"].startswith(workflow_prefix):
        raise ValueError("Candidate producer is not the CI workflow")
    if event.get("name") == "pull_request":
        pr = context.get("pr")
        if (not isinstance(pr, dict) or not _positive(pr.get("number"))
                or not all(isinstance(pr.get(key), str) and pr[key] for key in ("source_ref", "target_ref"))
                or checkout.get("kind") != "pr_merge" or len(parents) != 2
                or parents[1] != context["source_sha"] or context.get("tested_base_sha") != parents[0]):
            raise ValueError("PR merge parents do not prove the source and tested base")
        if not _sha(event.get("reported_base_sha")):
            raise ValueError("PR event base identity is missing")
    elif event.get("name") in {"push", "workflow_dispatch"}:
        if (context.get("pr") is not None or checkout.get("kind") != "branch"
                or context.get("tested_base_sha") is not None or context["source_sha"] != checkout["commit"]):
            raise ValueError("Branch execution cannot claim PR integration coverage")
    else:
        raise ValueError("Unsupported CI event")
    return context


def capture_context(root, event, env):
    """Capture the actual checkout once; retain event base as a separate observation."""
    root = Path(root).resolve()
    repository = event.get("repository", {})
    repository = {key: repository.get(key) for key in ("id", "full_name")}
    if (repository.get("full_name") != env.get("GITHUB_REPOSITORY")
            or str(repository.get("id")) != env.get("GITHUB_REPOSITORY_ID")):
        raise ValueError("Event and execution repositories disagree")
    name = env.get("GITHUB_EVENT_NAME")
    pr = event.get("pull_request") if name == "pull_request" else None
    source_repository = pr["head"]["repo"] if pr else repository
    source_sha = pr["head"]["sha"] if pr else env.get("GITHUB_SHA")
    parents = _git(root, "show", "-s", "--format=%P", "HEAD").split()
    context = {
        "schema_version": SCHEMA, "repository": repository,
        "source_repository": {key: source_repository.get(key) for key in ("id", "full_name")},
        "pr": {"number": pr.get("number", event.get("number")), "source_ref": pr["head"]["ref"],
               "target_ref": pr["base"]["ref"]} if pr else None,
        "event": {"name": name, "action": event.get("action"),
                  "reported_base_sha": pr["base"]["sha"] if pr else None},
        "source_sha": source_sha,
        "checkout": {"commit": _git(root, "rev-parse", "HEAD"), "tree": _git(root, "rev-parse", "HEAD^{tree}"),
                     "parents": parents, "status": _git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                     "kind": "pr_merge" if pr else "branch"},
        "tested_base_sha": parents[0] if pr and len(parents) == 2 and parents[1] == source_sha else None,
        "inputs": _inputs(root),
        "producer": {"workflow_ref": env.get("GITHUB_WORKFLOW_REF"), "workflow_sha": env.get("GITHUB_WORKFLOW_SHA"),
                     "run_id": int(env["GITHUB_RUN_ID"]), "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                     "check_sha": source_sha},
    }
    if context["checkout"]["commit"] != env.get("GITHUB_SHA"):
        raise ValueError("Actual checkout differs from the event execution commit")
    return validate_context(context)


def runtime_manifest(lane, env):
    """Record resolved per-lane tools; unknown inputs cannot establish equivalence."""
    def version(command):
        executable = shutil.which(command)
        return subprocess.check_output([executable, "--version"], text=True).strip() if executable else None
    tmux = shutil.which("tmux")
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "architecture": platform.machine(), "platform": platform.system(), "uv": version("uv"),
            "tmux": subprocess.check_output([tmux, "-V"], text=True).strip()
            if lane == "tmux-acceptance-evidence" and tmux else None,
            "runner": {"os": env.get("RUNNER_OS"), "image": env.get("ImageOS"), "version": env.get("ImageVersion")},
            "mode": "protocol-fixture"}


def capture_lane(root, context, lane, env, *, runtime=None):
    validate_context(context)
    root = Path(root).resolve()
    if (not isinstance(lane, str) or not re.fullmatch(r"[a-z0-9.-]+", lane)
            or int(env["GITHUB_RUN_ID"]) != context["producer"]["run_id"]
            or _git(root, "rev-parse", "HEAD") != context["checkout"]["commit"]
            or _git(root, "rev-parse", "HEAD^{tree}") != context["checkout"]["tree"]
            or _git(root, "show", "-s", "--format=%P", "HEAD").split() != context["checkout"]["parents"]
            or _git(root, "status", "--porcelain=v1", "--untracked-files=all") != ""
            or _inputs(root) != context["inputs"]):
        raise ValueError("Downstream checkout disagrees with the captured candidate")
    return {"schema_version": SCHEMA, "context": context, "lane": lane,
            "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
            "runtime": runtime if runtime is not None else runtime_manifest(lane, env)}


def _same_run_context(first, second):
    left, right = copy.deepcopy(validate_context(first)), copy.deepcopy(validate_context(second))
    left["producer"].pop("run_attempt")
    right["producer"].pop("run_attempt")
    return left == right


def qualify_lineage(context, producers, metadata, expected):
    """Choose the newest actual execution of each lane, including partial reruns."""
    validate_context(context)
    run, check, commit = metadata["run"], metadata["check"], metadata["git"]
    if not all(isinstance(value, dict) for value in (run, check, commit)):
        raise ValueError("GitHub producer metadata is incomplete")
    producer = context["producer"]
    if (run.get("id") != producer["run_id"] or not _positive(run.get("run_attempt"))
            or run["run_attempt"] < producer["run_attempt"] or run.get("event") != context["event"]["name"]
            or run.get("head_sha") != context["source_sha"]
            or not _repository(run.get("repository"))
            or {key: run["repository"][key] for key in ("id", "full_name")} != context["repository"]
            or run.get("path", "").split("@")[0] != WORKFLOW
            or check.get("head_sha") != producer["check_sha"] or check.get("id") != run.get("check_suite_id")
            or check.get("app", {}).get("id") != 15368
            or commit.get("sha") != context["checkout"]["commit"]
            or commit.get("tree", {}).get("sha") != context["checkout"]["tree"]
            or [parent.get("sha") for parent in commit.get("parents", [])] != context["checkout"]["parents"]
            or metadata.get("workflow_bytes_sha256") != context["inputs"]["workflow_sha256"]):
        raise ValueError("GitHub run/check association disagrees with the tested candidate")
    if context["pr"] is not None:
        remote = run.get("head_repository", {})
        if (not isinstance(remote, dict) or run.get("head_branch") != context["pr"]["source_ref"]
                or {key: remote.get(key) for key in ("id", "full_name")} != context["source_repository"]):
            raise ValueError("GitHub PR source repository/ref disagrees with the candidate")
        edges = run.get("pull_requests")
        if not isinstance(edges, list):
            raise ValueError("GitHub PR association is missing")
        matches = [edge for edge in edges if isinstance(edge, dict)
                   and all(isinstance(edge.get(key), dict) and isinstance(edge[key].get("repo"), dict)
                           for key in ("head", "base"))
                   and edge.get("number") == context["pr"]["number"]
                   and edge.get("head", {}).get("sha") == context["source_sha"]
                   and edge.get("head", {}).get("ref") == context["pr"]["source_ref"]
                   and edge.get("head", {}).get("repo", {}).get("id") == context["source_repository"]["id"]
                   and edge.get("base", {}).get("ref") == context["pr"]["target_ref"]
                   and edge.get("base", {}).get("repo", {}).get("id") == context["repository"]["id"]]
        if len(matches) != 1:
            raise ValueError("GitHub PR number/target association disagrees with the candidate")
    references = []
    for lane, name in expected.items():
        jobs = [job for job in metadata["jobs"] if job.get("name") == name]
        if not jobs or any(not _positive(job.get("run_attempt")) for job in jobs):
            raise ValueError("Missing authoritative lane execution: " + lane)
        newest = max(job["run_attempt"] for job in jobs)
        selected = [job for job in jobs if job["run_attempt"] == newest]
        if (len(selected) != 1 or newest > run["run_attempt"] or not _positive(selected[0].get("id"))
                or selected[0].get("status") != "completed" or selected[0].get("conclusion") != "success"):
            raise ValueError("Newest authoritative lane execution did not succeed: " + lane)
        artifact_name = f"{lane}--attempt-{newest}"
        artifacts = [item for item in metadata["artifacts"] if item.get("name") == artifact_name]
        if len(artifacts) != 1:
            raise ValueError("Missing or duplicate authoritative artifact: " + lane)
        artifact = artifacts[0]
        digest = artifact.get("digest", "")
        if (not _positive(artifact.get("id")) or artifact.get("expired") is not False
                or not isinstance(digest, str) or not digest.startswith("sha256:") or not _sha(digest[7:], 64)
                or artifact.get("workflow_run", {}).get("id") != run["id"]
                or artifact.get("workflow_run", {}).get("head_sha") != run["head_sha"]):
            raise ValueError("Artifact producer identity is invalid: " + lane)
        manifest = producers.get(artifact_name)
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != SCHEMA or manifest.get("lane") != lane
                or not _positive(manifest.get("run_attempt"))
                or manifest.get("run_attempt") != newest or not isinstance(manifest.get("runtime"), dict)
                or not _same_run_context(context, manifest.get("context"))):
            raise ValueError("Lane manifest contradicts its authoritative producer: " + lane)
        references.append({"lane": lane, "decision": "executed", "run_id": run["id"], "run_attempt": newest,
                           "job_id": selected[0]["id"], "artifact_id": artifact["id"], "artifact_name": artifact_name,
                           "artifact_digest": digest, "runtime": manifest["runtime"],
                           "original_context": manifest["context"]})
    return references


def expected_lanes(workflow, *, documentation_only=False):
    """Use the existing lane inventory; this is not a second suite catalog."""
    jobs = workflow["jobs"]
    result = {"static-checks-python-3.12": jobs["changes"]["name"]}
    if documentation_only:
        return result
    count = jobs["application-tests"]["strategy"]["matrix"]["shards"][0]
    for index in range(1, count + 1):
        name = jobs["application-tests"]["name"].replace("${{ matrix.shard }}", str(index)).replace("${{ matrix.shards }}", str(count))
        result[f"framework-tests-python-3.12-shard-{index}-of-{count}"] = name
    result.update({"calibration-python-3.12": jobs["verification"]["name"],
                   "composed-protocol-evidence": jobs["verification"]["name"],
                   "tmux-acceptance-evidence": jobs["tmux-acceptance"]["name"]})
    if "package" in jobs:
        result["package-acceptance-evidence"] = jobs["package"]["name"]
    return result


def api_bytes(url, token, limit):
    """Restrict credential origin; redirected artifact downloads receive no token."""
    origin = urlsplit(url)
    if (origin.scheme != "https" or origin.hostname != "api.github.com" or origin.port not in (None, 443)
            or origin.username or origin.password):
        raise ValueError("Evidence download requires the GitHub API origin")
    request = Request(url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    request.add_unredirected_header("Authorization", "Bearer " + token)
    with urlopen(request, timeout=30) as response:  # nosec B310 -- HTTPS API origin checked above
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Evidence response exceeds its byte limit")
    return payload


def collect_metadata(context, token):
    """Enumerate bounded complete job/attempt/artifact lists, or fail qualification."""
    validate_context(context)
    prefix = "https://api.github.com/repos/" + context["repository"]["full_name"]
    run_id = context["producer"]["run_id"]
    def get(path):
        return json.loads(api_bytes(prefix + path, token, MAX_JSON))
    def pages(path, key):
        items = []
        for page in range(1, 21):
            value = get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            batch = value.get(key)
            if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
                raise ValueError("Malformed GitHub evidence collection")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError("GitHub evidence collection is incomplete at the page limit")
    run = get(f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise ValueError("Malformed GitHub run metadata")
    attempt = run.get("run_attempt")
    if not _positive(attempt) or attempt > MAX_ATTEMPTS:
        raise ValueError("Run attempts exceed the qualification limit")
    jobs = [job for number in range(1, attempt + 1)
            for job in pages(f"/actions/runs/{run_id}/attempts/{number}/jobs", "jobs")]
    workflow = get("/contents/" + WORKFLOW + "?ref=" + context["producer"]["workflow_sha"])
    if (not isinstance(workflow, dict) or workflow.get("encoding") != "base64"
            or not isinstance(workflow.get("content"), str)):
        raise ValueError("Immutable workflow contents are unavailable")
    workflow_bytes = base64.b64decode("".join(workflow["content"].splitlines()), validate=True)
    if not workflow_bytes or len(workflow_bytes) > 256 * 1024:
        raise ValueError("Immutable workflow contents exceed their byte limit")
    return {"run": run, "check": get(f"/check-suites/{run['check_suite_id']}"), "jobs": jobs,
            "git": get("/git/commits/" + context["checkout"]["commit"]),
            "workflow_bytes_sha256": hashlib.sha256(workflow_bytes).hexdigest(),
            "artifacts": pages(f"/actions/runs/{run_id}/artifacts", "artifacts")}


def extract_artifact(payload, directory, *, digest):
    """Verify immutable archive bytes and reject ambiguous/unsafe ZIP members."""
    if len(payload) > MAX_ARCHIVE or "sha256:" + hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Artifact archive digest or byte limit disagrees")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members, names, size = archive.infolist(), set(), 0
        if not members or len(members) > MAX_MEMBERS:
            raise ValueError("Artifact member count exceeds its limit")
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            size += member.file_size
            canonical = path.as_posix() + ("/" if member.is_dir() else "")
            if (member.orig_filename != member.filename or member.filename != canonical or member.filename in names
                    or path.is_absolute() or ".." in path.parts or "\\" in member.filename
                    or not path.parts or ":" in path.parts[0] or stat.S_ISLNK(mode)
                    or member.flag_bits & 1 or member.file_size > MAX_ARCHIVE or size > MAX_EXPANDED
                    or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ValueError("Artifact contains unsafe, duplicate or oversized members")
            names.add(member.filename)
        files = {member.filename for member in members if not member.is_dir()}
        for member in members:
            path = PurePosixPath(member.filename)
            if (member.is_dir() and path.as_posix() in files) or any(parent.as_posix() in files for parent in path.parents):
                raise ValueError("Artifact file and directory paths conflict")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = directory / member.filename
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)


def download_evidence(context, metadata, token, directory, expected):
    """Download exact API artifact IDs, checking their archive digests before use."""
    prefix = "https://api.github.com/repos/" + context["repository"]["full_name"] + "/actions/artifacts/"
    names = {f"candidate-context--attempt-{context['producer']['run_attempt']}"}
    for lane, name in expected.items():
        attempts = [job["run_attempt"] for job in metadata["jobs"]
                    if job.get("name") == name and _positive(job.get("run_attempt"))]
        if attempts:
            names.add(f"{lane}--attempt-{max(attempts)}")
    # Optional advice is retained separately and cannot qualify required lanes.
    advice = [item for item in metadata["artifacts"] if item.get("name", "").startswith("architecture-evidence--attempt-")]
    if advice:
        names.add(max(advice, key=lambda item: item["id"])["name"])
    for item in metadata["artifacts"]:
        if item.get("name") not in names:
            continue
        name, identifier = item["name"], item.get("id")
        if not re.fullmatch(r"[a-z0-9.-]+--attempt-[1-9][0-9]*", name) or not _positive(identifier):
            raise ValueError("Invalid selected artifact identity")
        try:
            payload = api_bytes(prefix + str(identifier) + "/zip", token, MAX_ARCHIVE)
            extract_artifact(payload, Path(directory) / name, digest=item.get("digest"))
            if name.startswith("architecture-evidence--attempt-"):
                (Path(directory) / name).rename(Path(directory) / "architecture-evidence")
        except (OSError, ValueError, zipfile.BadZipFile):
            if not name.startswith("architecture-evidence--attempt-"):
                raise


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    context = sub.add_parser("context")
    context.add_argument("--root", type=Path, default=Path.cwd())
    context.add_argument("--output", type=Path, required=True)
    context.add_argument("--github-output", type=Path)
    lane = sub.add_parser("lane")
    lane.add_argument("--root", type=Path, default=Path.cwd())
    lane.add_argument("--lane", required=True)
    lane.add_argument("--output", type=Path, required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--artifacts", type=Path, required=True)
    collect.add_argument("--workflow", type=Path, required=True)
    collect.add_argument("--needs", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "context":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        value = capture_context(args.root, event, os.environ)
        write_json(args.output, value)
        if args.github_output:
            with args.github_output.open("a") as stream:
                stream.write("context=" + json.dumps(value, separators=(",", ":")) + "\n")
    elif args.command == "lane":
        value = capture_lane(args.root, json.loads(os.environ["CI_CANDIDATE_CONTEXT"]), args.lane, os.environ)
        write_json(args.output, value)
    else:
        try:
            value = json.loads(os.environ["CI_CANDIDATE_CONTEXT"])
            metadata = collect_metadata(value, os.environ["GITHUB_TOKEN"])
            needs = json.loads(args.needs.read_text())
            docs = (value["event"]["name"] == "pull_request" and needs.get("paths", {}).get("result") == "success"
                    and needs["paths"].get("outputs", {}).get("code") == "false")
            download_evidence(value, metadata, os.environ["GITHUB_TOKEN"], args.artifacts,
                              expected_lanes(json.loads(args.workflow.read_text()), documentation_only=docs))
            write_json(args.output, metadata)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
            write_json(args.output, {"error": "evidence_collection_unavailable", "error_type": type(error).__name__})
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
