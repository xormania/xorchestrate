"""Native GitHub observations for cancellation scenarios, without network effects."""

from collections import Counter
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

from tests.hexagonal.support.ci_reuse import candidate_context, current_pull_request


class QueueAPI:
    def __init__(self):
        self.context = candidate_context(300, action="synchronize")
        self.repository = deepcopy(self.context["repository"])
        self.pr = current_pull_request(self.context)
        self.pr["base"]["repo"] = deepcopy(self.repository)
        self.workflow = {"id": 71, "path": ".github/workflows/ci.yml", "state": "active"}
        self.ref = {"ref": "refs/heads/feature", "object": {"type": "commit", "sha": self.pr["head"]["sha"]}}
        self.runs = {300: self.run(300), 200: self.run(200, head="c" * 40)}
        self.requests = []
        self.counts = Counter()
        self.cancelled = []
        self.before_read = lambda *_: None
        self.before_cancel = lambda *_: None
        self.listing = None

    def run(self, identifier, *, head=None, action="synchronize", status="in_progress", conclusion=None):
        head = head or self.pr["head"]["sha"]
        # GitHub's PR associations can reflect the live PR, even for old runs.
        association = {"number": self.pr["number"], "head": deepcopy(self.pr["head"]),
                       "base": deepcopy(self.pr["base"])}
        for side in ("head", "base"):
            association[side]["repo"].pop("full_name")
        return {"id": identifier, "run_attempt": 1, "workflow_id": self.workflow["id"],
                "path": self.workflow["path"], "event": "pull_request", "head_sha": head,
                "head_branch": "feature", "repository": deepcopy(self.repository),
                "head_repository": deepcopy(self.repository), "pull_requests": [association],
                "display_title": f"CI:pull_request:{action}:17:{head}:{self.pr['base']['sha']}",
                "status": status, "conclusion": conclusion}

    def get(self, path):
        self.requests.append(path)
        self.counts[path] += 1
        self.before_read(self, path, self.counts[path])
        parsed = urlsplit(path)
        if parsed.path == "/actions/workflows/ci.yml":
            return deepcopy(self.workflow)
        if parsed.path.startswith("/actions/runs/"):
            return deepcopy(self.runs[int(parsed.path.rsplit("/", 1)[1])])
        if parsed.path == "/actions/workflows/ci.yml/runs":
            query = parse_qs(parsed.query)
            assert query.get("event") == ["pull_request"]
            page = int(query.get("page", ["1"])[0])
            rows = list(self.runs.values()) if self.listing is None else self.listing
            return {"total_count": len(rows), "workflow_runs": deepcopy(rows if page == 1 else [])}
        if parsed.path == "/pulls/17":
            return deepcopy(self.pr)
        if parsed.path == "/git/ref/heads/feature":
            return deepcopy(self.ref)
        raise AssertionError("Unexpected queue API read: " + path)

    def cancel(self, run_id):
        self.before_cancel(self, run_id)
        self.cancelled.append(run_id)

