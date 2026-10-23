"""Shared delivery arrangements without test methods."""

from unittest.mock import patch

from devtools.api import API
from devtools.github import GitHub, short_pr
from devtools.workspaces import git
from fixtures.workspaces import WorkspaceFixture


class DeliveryFixture:
    setUp_base = WorkspaceFixture.setUp
    prepared = WorkspaceFixture.prepared

    def setUp(self):
        self.setUp_base()
        self.workspace = self.prepared()
        (self.workspace / "src/Example.php").write_text("Delivered implementation\n")
        self.api.call("work_save", {"note": "Implementation"})
        self.managed = self.api.workspaces.ensure()
        self.job = self.api.control.snapshot()["job"]
        self.order = self.api.control.snapshot()["work_order"]
        self.head = git(self.workspace, "rev-parse", "HEAD").stdout.decode().strip()
        git(self.workspace, "push", "origin", self.managed["branch"])
        self.api.work.job_update(job=self.job, pull_request="https://github.com/example/project/pull/1")
        test = self
        context_provider = GitHub(self.store, "coder")

        class Provider:
            ci = "passed"
            uncertain = False
            reviews = []
            merges = []
            pr = {
                "number": 1,
                "html_url": "https://github.com/example/project/pull/1",
                "title": "Feature",
                "state": "open",
                "merged": False,
                "draft": False,
                "head": {"sha": test.head, "ref": test.managed["branch"]},
                "base": {"sha": test.managed["base_revision"], "ref": test.managed["target"]},
                "mergeable": True,
                "mergeable_state": "clean",
            }
            _revision = staticmethod(GitHub._revision)

            def _context(self, **kwargs):
                return context_provider._context(**kwargs)

            def _pr(self, context):
                return self.pr

            def pr_get(self, context):
                return {"pr": short_pr(self.pr), "ci": {"state": self.ci}, "reviews": [], "observed_blockers": []}

            def pr_diff(self, context, revision=None, **kwargs):
                self._revision(self.pr, revision)
                return {"revision": revision, "files": [{"filename": "src/Example.php"}]}

            def _all(self, endpoint):
                return self.reviews

            def pr_review(self, context, revision, decision, body):
                self._revision(self.pr, revision)
                test.assertEqual("comment", decision)
                self.reviews.append(
                    {"body": body, "html_url": self.pr["html_url"] + "#review-" + str(len(self.reviews) + 1)}
                )
                if self.uncertain:
                    self.uncertain = False
                    raise ConnectionError("Provider reply lost after publication")
                return {"url": self.reviews[-1]["html_url"]}

            def pr_merge(self, context, revision):
                self._revision(self.pr, revision)
                self.merges.append(revision)
                git(test.remote, "update-ref", "refs/heads/" + self.pr["base"]["ref"], revision)
                self.pr.update(merged=True, state="closed", merge_commit_sha=revision)
                return {"merged": True, "commit": revision}

        self.provider = Provider()
        mock = patch("devtools.delivery.GitHub", lambda store, member: self.provider)
        mock.start()
        self.addCleanup(mock.stop)
        self.api = API(self.state, "coder")
        self.xo = API(self.state, "xo")

    def auditor(self, role="audit"):
        item = {
            "name": "Feature",
            "role": role,
            "objective": "Review the implementation",
            "done_when": "Findings are supported",
            "constraints": "Read-only source",
        }
        first = self.xo.call("orchestrate", {"work": [item]})
        second = self.xo.call("orchestrate", {"work": [item]})
        self.assertEqual(first, second)
        with self.store.connect() as db:
            member = db.execute(
                "SELECT member FROM member_bindings WHERE job=? AND role=?", (self.job, role)
            ).fetchone()["member"]
        return API(self.state, member)
