"""GitHub HTTP peer with real qualified receipts and digest-bound ZIP archives."""

import base64
import hashlib
import io
import json
import shutil
from copy import deepcopy
from urllib.parse import urlsplit

from scripts.ci import result as aggregate
from tests.hexagonal.support.ci_candidate import artifact_zip, lane_runtime
from tests.hexagonal.support.ci_receipts import complete_receipts, write_json
from tests.hexagonal.support.ci_reuse import candidate_context, current_pull_request, metadata_title


class ProducerAPI:
    def __init__(self, root):
        self.root = root
        self.current = candidate_context(301)
        self.metadata = {}
        self.results = {}
        self.payloads = {}
        self.requests = []
        self.next_artifact = 5000
        self.original = self.full_execution(101)

    @property
    def runtime(self):
        return {lane: lane_runtime(tmux="tmux 3.4" if lane == "tmux-acceptance-evidence" else None)
            for lane in self.lanes if lane != "static-checks-python-3.12"}

    def _metadata(self, context, jobs):
        identifier = context["producer"]["run_id"]
        run = {"id": identifier, "run_number": identifier, "run_attempt": 1, "event": "pull_request",
            "head_sha": context["source_sha"], "repository": deepcopy(context["repository"]),
            "head_repository": deepcopy(context["source_repository"]), "head_branch": context["pr"]["source_ref"],
            "path": ".github/workflows/ci.yml", "check_suite_id": identifier + 10000,
            "status": "completed", "conclusion": "success", "run_started_at": f"2026-11-10T12:{identifier // 100:02d}:{identifier % 100:02d}Z",
            "attempt_started_at": f"2026-11-10T12:{identifier // 100:02d}:{identifier % 100:02d}Z",
            "display_title": metadata_title(context, "edited" if identifier != 101 else "synchronize"),
            "pull_requests": [{"number": context["pr"]["number"],
                "head": {"sha": context["source_sha"], "ref": context["pr"]["source_ref"], "repo": deepcopy(context["source_repository"])},
                "base": {"sha": context["tested_base_sha"], "ref": context["pr"]["target_ref"], "repo": deepcopy(context["repository"])}}]}
        jobs = deepcopy(jobs) + [{"id": identifier * 100 + 99, "name": "CI result", "run_attempt": 1,
            "status": "completed", "conclusion": "success"}]
        for job in jobs:
            job.update(run_id=identifier, head_sha=context["source_sha"], runner_id=job["id"],
                runner_name=f"fixture-runner-{job['id']}", created_at="2026-11-10T12:00:00Z",
                started_at="2026-11-10T12:00:01Z", completed_at="2026-11-10T12:00:02Z",
                steps=[{"name": "Run declared checks", "number": 1, "status": "completed", "conclusion": "success",
                    "started_at": "2026-11-10T12:00:01Z", "completed_at": "2026-11-10T12:00:02Z"}])
        return {"run": run, "check": {"id": run["check_suite_id"], "head_sha": context["source_sha"], "app": {"id": 15368}},
            "git": {"sha": context["checkout"]["commit"], "tree": {"sha": context["checkout"]["tree"]},
                "parents": [{"sha": parent} for parent in context["checkout"]["parents"]]},
            "workflow_bytes_sha256": hashlib.sha256(self.workflow_bytes).hexdigest(),
            "jobs": jobs, "artifacts": []}

    def _archive(self, identifier, name, directory):
        entries = [(path.relative_to(directory).as_posix(), path.read_bytes(), "file")
            for path in sorted(directory.rglob("*")) if path.is_file()]
        payload = artifact_zip(entries)
        artifact = {"id": self.next_artifact, "name": name, "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "expired": False, "workflow_run": {"id": identifier, "head_sha": self.current["source_sha"]}}
        self.next_artifact += 1
        self.payloads[artifact["id"]] = payload
        self.metadata[identifier]["artifacts"].append(artifact)
        return artifact

    def _lane(self, identifier, lane, context, directory):
        name = lane + "--attempt-1"
        write_json(directory / "producer.json", {"schema_version": 1, "lane": lane, "context": context,
            "run_attempt": 1, "runtime": lane_runtime(tmux="tmux 3.4" if lane == "tmux-acceptance-evidence" else None)})
        return self._archive(identifier, name, directory)

    def full_execution(self, identifier):
        artifacts = self.root / str(identifier) / "receipts"
        needs, workflow, catalog = complete_receipts(artifacts)
        workflow["jobs"].update({"changes": {"name": "Static checks"},
            "verification": {"name": "Behavioral fixtures"}, "tmux-acceptance": {"name": "Terminal fixtures"}})
        workflow["jobs"]["package"]["name"] = "Installed package"
        workflow["jobs"]["application-tests"]["name"] = "Application tests shard ${{ matrix.shard }}/${{ matrix.shards }}"
        self.workflow, self.catalog = workflow, catalog
        self.workflow_bytes = json.dumps(workflow, sort_keys=True).encode()
        self.current["inputs"]["workflow_sha256"] = hashlib.sha256(self.workflow_bytes).hexdigest()
        self.lanes = {"static-checks-python-3.12": "Static checks",
            "framework-tests-python-3.12-shard-1-of-2": "Application tests shard 1/2",
            "framework-tests-python-3.12-shard-2-of-2": "Application tests shard 2/2",
            "calibration-python-3.12": "Behavioral fixtures", "composed-protocol-evidence": "Behavioral fixtures",
            "tmux-acceptance-evidence": "Terminal fixtures", "package-acceptance-evidence": "Installed package"}
        context = deepcopy(self.current)
        context["producer"]["run_id"] = identifier
        context["event"]["action"] = "synchronize"
        job_names = list(dict.fromkeys(self.lanes.values()))
        jobs = [{"id": identifier * 100 + index, "name": name, "run_attempt": 1,
            "status": "completed", "conclusion": "success"} for index, name in enumerate(job_names, 1)]
        self.metadata[identifier] = self._metadata(context, jobs)
        for path in artifacts.rglob("candidate.json"):
            write_json(path, {"commit": context["checkout"]["commit"], "tree": context["checkout"]["tree"], "status": ""})
        package = artifacts / "package-acceptance-evidence/run/report.json"
        package_report = json.loads(package.read_text())
        package_report["candidate"] = {"commit": context["checkout"]["commit"], "tree": context["checkout"]["tree"], "status": ""}
        write_json(package, package_report)
        for lane in self.lanes:
            directory = artifacts / (lane + "--attempt-1")
            shutil.move(str(artifacts / lane), directory)
            self._lane(identifier, lane, context, directory)
        captured = artifacts / "candidate-context--attempt-1"
        write_json(captured / "context.json", context)
        self._archive(identifier, captured.name, captured)
        result = aggregate.evaluate(needs, event="pull_request", source_sha=context["source_sha"],
            tested_sha=context["checkout"]["commit"], workflow=workflow, artifacts=artifacts,
            catalog=catalog, context=context, metadata=self.metadata[identifier])
        if result["status"] != "passed":
            raise AssertionError("Full-execution fixture did not qualify: " + repr(result["errors"]))
        self.results[identifier] = result
        self.rewrite_result(identifier)
        return context

    def summary(self, identifier=201, *, original_id=101):
        context = deepcopy(self.current)
        context["producer"]["run_id"] = identifier
        jobs = [{"id": identifier * 100 + 1, "name": "Static checks", "run_attempt": 1,
            "status": "completed", "conclusion": "success"}]
        self.metadata[identifier] = self._metadata(context, jobs)
        artifacts = self.root / str(identifier) / "receipts"
        static = artifacts / "static-checks-python-3.12--attempt-1"
        write_json(static / "static/report.json", {"schema_version": 2, "suite": "static", "status": "passed"})
        write_json(static / "static/candidate.json", {"commit": context["checkout"]["commit"],
            "tree": context["checkout"]["tree"], "status": ""})
        self._lane(identifier, "static-checks-python-3.12", context, static)
        captured = artifacts / "candidate-context--attempt-1"
        write_json(captured / "context.json", context)
        self._archive(identifier, captured.name, captured)
        manifest = json.loads((static / "producer.json").read_text())
        static_refs = aggregate.candidate_evidence.qualify_lineage(context, {static.name: manifest},
            self.metadata[identifier], {"static-checks-python-3.12": "Static checks"})
        original = self.results[original_id]
        heavy_refs = [row for row in original["evidence_references"] if row["lane"] != "static-checks-python-3.12"]
        needs = deepcopy(original["jobs"])
        needs["paths"]["outputs"]["reuse"] = "true"
        for name in ("application-tests", "application", "verification", "tmux-acceptance", "package"):
            needs[name]["result"] = "skipped"
        original_artifact = next(row for row in self.metadata[original_id]["artifacts"]
            if row["name"] == "ci-result-evidence--attempt-1")
        self.results[identifier] = {"schema_version": 2, "status": "passed", "candidate_context": context,
            "documentation_only": False, "jobs": needs, "reuse": {
                "original_context": deepcopy(original["candidate_context"]),
                "result_artifact": {key: original_artifact[key] for key in ("id", "name", "digest")}},
            "evidence_references": static_refs + [{**deepcopy(row), "decision": "reused",
                "equivalence": "exact-candidate-v1", "consumer_run_id": identifier} for row in heavy_refs]}
        self.rewrite_result(identifier)
        return context

    def rewrite_result(self, identifier, *, attempt=1):
        metadata = self.metadata[identifier]
        name = f"ci-result-evidence--attempt-{attempt}"
        metadata["artifacts"] = [row for row in metadata["artifacts"] if row["name"] != name]
        directory = self.root / str(identifier) / ("aggregate" if attempt == 1 else f"aggregate-{attempt}")
        write_json(directory / "result.json", self.results[identifier])
        return self._archive(identifier, name, directory)

    def native_rerun(self, identifier=101, *, actual_aggregate=False, publish_aggregate=False):
        """Reproduce API copies independently of the resolver's implementation."""
        metadata = self.metadata[identifier]
        copied = []
        for original in metadata["jobs"]:
            observed = deepcopy(original)
            observed.update(id=original["id"] + 1000000, run_attempt=2, created_at="2026-11-10T12:05:00Z")
            if actual_aggregate and original["name"] == "CI result":
                observed.update(started_at="2026-11-10T12:05:01Z", completed_at="2026-11-10T12:05:02Z")
                observed["steps"][0].update(started_at="2026-11-10T12:05:01Z", completed_at="2026-11-10T12:05:02Z")
            copied.append(observed)
        metadata["jobs"].extend(copied)
        metadata["run"].update(run_attempt=2, run_started_at="2026-11-10T12:05:00Z", attempt_started_at="2026-11-10T12:05:00Z")
        if publish_aggregate:
            context = self.results[identifier]["candidate_context"]
            self.results[identifier] = aggregate.evaluate(self.results[identifier]["jobs"], event="pull_request",
                source_sha=context["source_sha"], tested_sha=context["checkout"]["commit"], workflow=self.workflow,
                artifacts=self.root / str(identifier) / "receipts", catalog=self.catalog, context=context, metadata=metadata)
            if self.results[identifier]["status"] != "passed":
                raise AssertionError("Native aggregate fixture did not qualify its own receipts")
            self.rewrite_result(identifier, attempt=2)

    def rewrite_lane(self, identifier, lane):
        name = lane + "--attempt-1"
        metadata = self.metadata[identifier]
        metadata["artifacts"] = [row for row in metadata["artifacts"] if row["name"] != name]
        return self._archive(identifier, name, self.root / str(identifier) / "receipts" / name)

    def inspect(self, run_id, destination):
        from scripts.ci import reuse

        return reuse.inspect_producer(self.current, self.metadata[run_id]["run"], self.workflow,
            "fixture-token", destination, aggregate.evaluate, self.catalog, static_qualifier=aggregate.qualify_static)

    def urlopen(self, request, timeout):
        url = urlsplit(request.full_url)
        prefix = "/repos/xormania/xorchestrate"
        if url.scheme != "https" or url.netloc != "api.github.com" or not url.path.startswith(prefix):
            raise AssertionError("Unexpected evidence origin")
        path = url.path.removeprefix(prefix)
        self.requests.append(path)
        parts = path.strip("/").split("/")
        if parts[:2] == ["actions", "artifacts"]:
            return io.BytesIO(self.payloads[int(parts[2])])
        if parts[:2] == ["actions", "runs"]:
            metadata = self.metadata[int(parts[2])]
            if parts[-1] == "jobs":
                value = {"jobs": [row for row in metadata["jobs"] if row.get("run_attempt") == int(parts[4])]}
            elif parts[-1] == "artifacts":
                value = {"artifacts": metadata["artifacts"]}
            else:
                value = metadata["run"]
        elif parts[0] == "check-suites":
            value = next(row["check"] for row in self.metadata.values() if row["check"]["id"] == int(parts[1]))
        elif parts[:2] == ["git", "commits"]:
            value = self.metadata[101]["git"]
        elif parts[:3] == ["git", "ref", "heads"]:
            from urllib.parse import unquote

            ref = unquote("/".join(parts[3:]))
            if ref == self.current["pr"]["source_ref"]:
                sha = self.current["source_sha"]
            elif ref == self.current["pr"]["target_ref"]:
                sha = self.current["tested_base_sha"]
            else:
                raise OSError("Canonical branch is unavailable")
            value = {"ref": "refs/heads/" + ref, "object": {"type": "commit", "sha": sha}}
        elif parts[:2] == ["contents", ".github"]:
            value = {"encoding": "base64", "content": base64.b64encode(self.workflow_bytes).decode()}
        elif parts[0] == "pulls":
            value = current_pull_request(self.current)
        elif parts[:2] == ["actions", "workflows"]:
            value = {"workflow_runs": [row["run"] for row in self.metadata.values()]}
        else:
            raise AssertionError("Unexpected evidence API request: " + path)
        return io.BytesIO(json.dumps(value).encode())
