"""Observed current PR and replacement workflow for stale-event scenarios."""

from copy import deepcopy

from tests.hexagonal.support.ci_reuse import CurrentPRObservations, current_pull_request


class ReplacementAPI(CurrentPRObservations):
    def __init__(self, context):
        pr = current_pull_request(context)
        pr["head"]["sha"] = "f" * 40
        pr["merge_commit_sha"] = "e" * 40
        super().__init__(context, pr)
        self.refs[self.source_key]["object"]["sha"] = pr["head"]["sha"]
        self.run = {"id": context["producer"]["run_id"] + 1, "event": "pull_request", "path": ".github/workflows/ci.yml",
            "head_sha": pr["head"]["sha"], "status": "queued", "conclusion": None, "run_attempt": 1,
            "display_title": f"CI:pull_request:synchronize:{pr['number']}:{pr['head']['sha']}:{pr['base']['sha']}",
            "pull_requests": [{"number": pr["number"], "head": {"sha": pr["head"]["sha"]}}]}
        self.listing = [deepcopy(self.run)]
        self.change_after_lookup = None

    def __call__(self, context, token, path):
        if path.startswith("/actions/workflows/"):
            self.requests.append((context["repository"]["full_name"], path))
            return {"workflow_runs": deepcopy(self.listing)}
        if path == f"/actions/runs/{self.run['id']}":
            self.requests.append((context["repository"]["full_name"], path))
            if self.change_after_lookup:
                self.pr["head"]["sha"] = self.change_after_lookup
            return deepcopy(self.run)
        return super().__call__(context, token, path)
