"""Complete event packets and scripted transport for advisory contracts."""
from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).parents[3]
HEAD = "a" * 40
BASE = "b" * 40
REPOSITORY = "example/project"


def corpus():
    return json.loads((ROOT / "tests/fixtures/jev-events/cases.json").read_text())


def event_case(identifier):
    case = deepcopy(next(case for case in corpus()["cases"] if case["id"] == identifier))
    evidence = case["evidence"]
    if case["kind"] == "review":
        evidence["source"]["sha256"] = hashlib.sha256(evidence["source"]["text"].encode()).hexdigest()
    packet = {"schema_version": 1, "kind": case["kind"],
              "source": {"repository": REPOSITORY, "pr_number": 12, "head_sha": HEAD},
              "evidence": evidence}
    return packet, case["expected"]


def scripted_response(choices):
    """Return a provider-shaped answer selected independently by a test."""
    def reply(payload, attempt):
        answers = {}
        for key, question in payload["questions"].items():
            options = question["criteria"]
            choice = choices(question, attempt) if callable(choices) else choices
            assert choice in options, (choice, options)
            answers[key] = {"type": "choice", "choice": choice, "confidence": 1.0,
                            "probabilities": {option: int(option == choice) for option in options}}
        return {"status": 200, "body": {"model": payload["model"], "answers": answers,
                                        "usage": {"input_tokens": 17, "output_tokens": 3}}}
    return reply


def provider_factory(provider):
    from xorchestrate.adapters.evaluation import TypeSafeEvaluator

    def factory(options):
        provider.setdefault("factory_options", []).append(deepcopy(options))
        transport = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options = {key: value for key, value in options.items() if key != "credential_env"}
        return TypeSafeEvaluator({**options, **transport})
    return factory


def policy():
    return json.loads((ROOT / "contracts/jev-events.json").read_text())


class GitHubEventFixture:
    """Read-only GitHub responses with explicit per-request source identity."""

    def __init__(self):
        repository = {"full_name": REPOSITORY}
        self.pr = {"number": 12, "state": "open", "draft": True, "title": "Retain receipts",
                   "body": "Write the receipt before reporting success.",
                   "head": {"sha": HEAD, "ref": "feature/receipts", "repo": repository},
                   "base": {"sha": BASE, "ref": "master", "repo": repository}}
        self.source = "def persist(store, receipt):\n    return {'status': 'success'}\n"
        self.path = "sample/receipt.py"
        self.files = [{"filename": self.path, "status": "modified", "sha": "c" * 40,
                       "patch": "@@ -1,2 +1,2 @@\n def persist(store, receipt):\n-    store.write(receipt)\n+    return {'status': 'success'}"}]
        self.comments = [{"id": 41, "pull_request_review_id": 51, "path": self.path,
                          "body": "The receipt is not persisted before reporting success.",
                          "commit_id": HEAD, "original_commit_id": HEAD, "line": 2}]
        self.jobs = [{"id": 71, "name": "Application tests", "conclusion": "failure",
                      "steps": [{"name": "Run tests", "conclusion": "failure"}]}]
        self.calls = []

    def event(self, action="opened"):
        return {"action": action, "number": 12, "repository": {"full_name": REPOSITORY},
                "pull_request": deepcopy(self.pr),
                "review": {"id": 51, "state": "changes_requested", "commit_id": HEAD,
                           "body": "Receipt persistence is missing."}}

    def api(self, path):
        self.calls.append(path)
        endpoint = path.split("?", 1)[0]
        if endpoint.endswith("/pulls/12"):
            return deepcopy(self.pr)
        if endpoint.endswith("/pulls/12/files"):
            return deepcopy(self.files)
        if endpoint.endswith("/comments"):
            return deepcopy(self.comments)
        if endpoint.endswith("/attempts/2/jobs"):
            return {"jobs": deepcopy(self.jobs)}
        if "/contents/" in endpoint:
            assert self.path in endpoint, "COLLECTOR_REQUESTS_OBSERVED_PATH"
            assert self.pr["head"]["sha"] in path, "COLLECTOR_PINS_CONTENT_TO_CURRENT_HEAD"
            return {"type": "file", "encoding": "base64", "path": self.path,
                    "content": base64.b64encode(self.source.encode()).decode(), "size": len(self.source.encode())}
        raise AssertionError(f"Unexpected GitHub read: {path}")

    def failure_event(self):
        return {"repository": {"full_name": REPOSITORY}, "workflow_run": {
            "id": 61, "run_attempt": 2, "head_sha": HEAD, "path": ".github/workflows/ci.yml",
            "conclusion": "failure", "head_repository": {"full_name": REPOSITORY},
            "pull_requests": [{"number": 12, "head": {"sha": HEAD}}]}}
