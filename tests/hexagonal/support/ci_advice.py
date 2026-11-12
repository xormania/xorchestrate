"""A real receipt/archive peer for trusted post-CI advice scenarios."""

import io
import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

from tests.hexagonal.support.ci_reuse_api import ProducerAPI


class AdviceAPI(ProducerAPI):
    def __init__(self, root):
        super().__init__(root)
        self.reservations = []
        self.consumers = {}
        self.event = {"repository": {**self.original["repository"], "default_branch": "master"},
                      "workflow_run": deepcopy(self.metadata[101]["run"])}
        self.env = {"GITHUB_EVENT_NAME": "workflow_run", "GITHUB_REPOSITORY": "xormania/xorchestrate",
                    "JEV_TRUSTED_SHA": "f" * 40, "GITHUB_RUN_ID": "701", "GITHUB_RUN_ATTEMPT": "1"}

    def reserve(self, *, expired=False):
        self.reservations = [{"id": 8001, "name": "jev-reservation-source-101-1", "expired": expired,
                              "workflow_run": {"id": 701}}]
        self.consumers[701] = {"id": 701, "path": ".github/workflows/jev.yml", "head_branch": "master",
                               "event": "workflow_run", "repository": deepcopy(self.original["repository"])}

    def get(self, url):
        with self.urlopen(SimpleNamespace(full_url=url), timeout=10) as response:
            return json.loads(response.read())

    def urlopen(self, request, timeout):
        path = urlsplit(request.full_url).path
        if path.endswith("/actions/artifacts"):
            self.requests.append("/actions/artifacts")
            return io.BytesIO(json.dumps({"artifacts": self.reservations,
                                          "total_count": len(self.reservations)}).encode())
        if any(path.endswith(f"/actions/runs/{identifier}") for identifier in self.consumers):
            identifier = int(path.rsplit("/", 1)[-1])
            self.requests.append(f"/actions/runs/{identifier}")
            return io.BytesIO(json.dumps(self.consumers[identifier]).encode())
        response = super().urlopen(request, timeout)
        if path.endswith("/artifacts"):
            value = json.loads(response.read())
            value["total_count"] = len(value["artifacts"])
            for row in value["artifacts"]:
                row["size_in_bytes"] = len(self.payloads[row["id"]])
            return io.BytesIO(json.dumps(value).encode())
        return response


def assessment_checkouts(root):
    """Distinct real Git trees expose candidate policy/code as untrusted data."""
    from scripts.ci import candidate
    from tests.hexagonal.support.ci_candidate import candidate_repository, github_observation, write_json

    profile = {"schema_version": 1, "id": "trusted-policy", "version": 1, "source_roots": ["pkg"],
               "roles": {"core": {"modules": ["pkg.*"], "allowed_roles": ["core"],
                                  "allowed_external": [], "allow_dynamic": False}}}
    options = {"model": "trusted-model", "post_test": {"model": "trusted-triage-model"}}
    source = candidate_repository(root / "source", extra_files={
        "pkg/work.py": 'raise RuntimeError("candidate Python must never execute")\n',
        "scripts/architecture/diagnostics.py": 'raise RuntimeError("candidate collector must never execute")\n',
        "contracts/hexagonal.json": json.dumps({**profile, "id": "candidate-policy"}),
        "contracts/hexagonal-jev.json": json.dumps({"model": "candidate-model"}),
    })
    trusted = candidate_repository(root / "trusted", extra_files={
        "contracts/hexagonal.json": json.dumps(profile), "contracts/hexagonal-jev.json": json.dumps(options)})
    event, env = github_observation(source)
    context = candidate.capture_context(source["root"], event, env)
    work = root / "work"
    result_path = work / "artifacts/ci-result-evidence--attempt-1/result.json"
    write_json(result_path, {"candidate_context": context, "status": "failed"})
    write_json(work / "metadata.json", {"id": "authenticated-metadata"})
    plan = {"provider_needed": True, "kind": "source", "candidate_context": context,
            "trusted_sha": trusted["tested_commit"], "consumer_run_id": 701,
            "source": {"run_id": context["producer"]["run_id"], "source_sha": context["source_sha"]},
            "result_path": str(result_path)}
    return SimpleNamespace(source=source["root"], trusted=trusted["root"], plan=plan, work=work,
                           profile=profile, options=options)
