"""Work-order integration and delivery using the existing workspace and GitHub services."""

from __future__ import annotations

import hashlib
import io

from .artifacts import Artifacts
from .codex_reviews import CodexReviews
from .github import GitHub
from .role_packages import adapter, require_capability
from .store import digest, stamp
from .work import Work, record
from .workspaces import Workspaces, git


class Orders:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.spaces = Workspaces(store, member)

    def ensure_in(self, ident, *, refresh=False):
        """Prepare under the repository lock; refreshing also requires the order lock."""
        from .retirement import register

        with self.store.connect() as db:
            old = db.execute("SELECT * FROM order_workspaces WHERE work_order=?", (ident,)).fetchone()
            work = record(db, "work_orders", ident)
        checkout = self.spaces.checkout()
        if old:
            workspace = {**dict(old), "job": None}
            self.spaces._verify(workspace, checkout)
            if refresh:
                self._sync_in(workspace)
            return workspace
        if work["status"] in {"done", "cancelled"}:
            raise ValueError("This work order is finished.")
        target = self.spaces.target()
        base = self.spaces.upstream(target)
        generation = hashlib.sha256(work["created"].encode()).hexdigest()[:8]
        name = f"{self.spaces.team()}-{ident}-{generation}"
        branch = "work-order/" + name
        root = self.spaces.parent() / ("wo-" + name)
        if not root.exists():
            exists = git(checkout, "show-ref", "--verify", "refs/heads/" + branch, check=False).returncode == 0
            git(checkout, "worktree", "add", *([] if exists else ["-b", branch]), str(root), branch if exists else base)
        workspace = dict(work_order=ident, job=None, root=str(root), branch=branch, target=target, base_revision=base)
        self.spaces._verify(workspace, checkout)
        if git(root, "rev-parse", "HEAD").stdout.decode().strip() != base or self.spaces.changes(root):
            raise ValueError("Unrecorded integration workspace differs from development; preserved for inspection.")
        remote = self.spaces.remote()
        if git(checkout, "remote", "get-url", remote, check=False).returncode == 0:
            # Initial publication introduces no new commits: it is the fetched development revision.
            git(root, "push", remote, "HEAD:refs/heads/" + branch)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO order_workspaces(work_order,root,branch,target,base_revision) VALUES (?,?,?,?,?)",
                (ident, str(root), branch, target, base),
            )
            Work(self.store, self.member).update_in(
                db, "work_orders", ident, None, repository=self.spaces.config.get("repository", ""), branch=branch
            )
            Work(self.store, self.member).attach(db, ident, None, "worktree", str(root))
            register(db, workspace, ident)
        return workspace

    def scope(self, role=None):
        scope = self.spaces.scope()
        if scope["job"] or adapter(scope["role"]) not in {"xo", "planner"} or role and adapter(scope["role"]) != role:
            raise ValueError("This outcome requires the current work-order command role.")
        return scope

    @staticmethod
    def settled(db, ident):
        if db.execute(
            "SELECT 1 FROM jobs WHERE work_order=? AND status NOT IN ('done','cancelled')", (ident,)
        ).fetchone():
            raise ValueError("Jobs remain unfinished. Have XO accept or explicitly cancel them first.")
        if db.execute("SELECT 1 FROM role_assignments WHERE work_order=? AND status='active'", (ident,)).fetchone():
            raise ValueError("Role assignments remain active. Have their coordinator complete or explicitly cancel them first.")

    def sync(self, workspace):
        with self.spaces.lock("repository"):
            return self._sync_in(workspace)

    def _sync_in(self, workspace):
        """Refresh while the caller holds the order and repository locks."""
        self.spaces._verify(workspace, self.spaces.checkout())
        if self.spaces.changes(workspace["root"]):
            raise ValueError("The integration workspace has unexpected changes. They were preserved for inspection.")
        revision = self.spaces.upstream(workspace["branch"])
        git(workspace["root"], "merge", "--ff-only", revision)
        if git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip() != revision:
            raise ValueError("The integration workspace has unexpected local commits; preserved for inspection.")
        return {"state": "refreshed", "revision": revision}

    def submit(self, scope, workspace, summary):
        from .development import Development, configured

        require_capability(self.scope("xo")["role"], "work_submit")
        with self.store.connect() as db:
            self.settled(db, scope["work_order"])
        revision = self.sync(workspace)["revision"]
        if not git(workspace["root"], "diff", "--quiet", workspace["base_revision"], "HEAD", check=False).returncode:
            return {
                "state": "no_changes",
                "message": "No source change to publish; XO can conclude the retained results.",
            }
        provider = GitHub(self.store, self.member)
        context = provider._context(job=None)
        if context["work"]["pull_request"]:
            pr = provider._pr(context)
            self.match(pr["head"]["ref"], pr["base"]["ref"], workspace)
            if pr.get("merged") or pr["state"] == "closed":
                return {"state": "closed", "message": "XO must inspect the published result before concluding."}
        validation = Development(self.store, self.member).checks(workspace["root"]) if configured(self.store) else None
        if validation and (not validation["passed"] or validation["uncommitted"]):
            return {"state": "needs_attention", "validation": validation}
        self.spaces.scope(write=True)
        # Integration branches are updated by job PRs, never by a direct source push here.
        with self.spaces.lock("repository"):
            latest = self.spaces.upstream(workspace["branch"])
        if latest != revision:
            raise ValueError("Integrated work changed during validation. Submit again to check the current result.")
        diff = git(
            workspace["root"], "diff", "--no-ext-diff", "--no-textconv", workspace["base_revision"], revision
        ).stdout
        evidence = Artifacts(self.store, self.member).ingest(
            "work-order-" + revision[:12] + ".diff",
            io.BytesIO(diff),
            "text/plain",
            work_order=scope["work_order"],
            job=None,
        )["artifact"]["id"]
        with self.store.connect() as db:
            jobs = list(
                db.execute("SELECT title,pull_request,status FROM jobs WHERE work_order=?", (scope["work_order"],))
            )
        body = (
            summary
            + "\n\nContributions:\n"
            + "\n".join(f"- {row['title']}: {row['pull_request'] or row['status']}" for row in jobs)
            + "\n\nEvidence: artifact:"
            + str(evidence)
        )
        opened = provider.pr_open(context, base=workspace["target"], summary=body, draft=False)
        if opened["pr"]["revision"] != revision:
            raise ValueError("The PR changed during publication. Inspect and validate its current revision.")
        watch = provider.ci_watch(context, reviews=True, timeout=86400)
        reviews = CodexReviews(provider).request(context, revision)
        self.spaces.control.report("Submitted integrated work: " + opened["pr"]["url"], kind="result")
        return {
            "state": "submitted",
            "pr": opened["pr"]["url"],
            "validation": validation,
            "evidence": "artifact:" + str(evidence),
            "ci": watch,
            "reviews": reviews,
        }

    @staticmethod
    def match(branch, target, workspace):
        if branch != workspace["branch"] or target != workspace["target"]:
            raise ValueError("The PR no longer matches the managed work-order branch and target.")

    def inspect(self, section="review", path=None, offset=None, page=1):
        scope = self.scope()
        require_capability(scope["role"], "work_inspect")
        with self.spaces.lock(self.spaces.scope_lock(scope)):
            with self.store.connect() as db:
                work = record(db, "work_orders", scope["work_order"])
            if not work["pull_request"]:
                return {"state": "not_submitted", "message": "XO submits integrated work after the jobs are finished."}
            provider = GitHub(self.store, self.member)
            context = provider._context(job=None)
            if section in {"ci", "feedback"}:
                from .feedback import packet, retain

                result = packet(provider, context, section, page, offset)
                if section == "feedback":
                    result["codex"] = CodexReviews(provider).status(context, result["revision"], page=page)
                    result["next_page"] = result["next_page"] or result["codex"]["next_page"]
                return retain(
                    self.store,
                    self.member,
                    {"work_order": work["id"], "id": None},
                    section,
                    result,
                )
            result = provider.pr_get(context)
            revision = result["pr"]["revision"]
            result["codex"] = CodexReviews(provider).status(context, revision)
            result["changes"] = provider.pr_diff(context, revision=revision, path=path, offset=offset or 0, page=page)
            self.scope()
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO order_inspections(member,work_order,revision,updated) VALUES (?,?,?,?) "
                    "ON CONFLICT(member,work_order) DO UPDATE SET revision=excluded.revision,updated=excluded.updated",
                    (self.member, work["id"], revision, stamp()),
                )
            return result

    def respond(self, text, review=None, resolve=False):
        scope = self.scope("xo")
        require_capability(scope["role"], "review_respond")
        if scope["paused"]:
            raise ValueError("Work is paused.")
        if not review or not review.startswith("thread:"):
            raise ValueError("Choose a thread reference from monitor(section=\"feedback\").")
        with self.spaces.lock(self.spaces.scope_lock(scope)):
            provider = GitHub(self.store, self.member)
            context = provider._context(job=None)
            pr = provider._pr(context)
            if pr["state"] != "open":
                raise ValueError("The work-order PR is closed.")
            thread = review.removeprefix("thread:")
            key = digest([context["repository"], context["pr"], thread, pr["head"]["sha"], text])
            marker = "<!-- agentscient-response:" + key + " -->"
            cursor, existing = None, False
            while True:
                row = provider.pr_threads(context, thread=thread, cursor=cursor)["thread"]
                existing |= any(c["viewerDidAuthor"] and marker in c["body"] for c in row["comments"]["nodes"])
                pages = row["comments"]["pageInfo"]
                if existing or not pages["hasNextPage"]:
                    break
                cursor = pages["endCursor"]
            self.spaces.scope(write=True)
            provider._unchanged(context, pr)
            if not existing:
                provider.pr_threads(context, thread=thread, action="reply",
                                    body=marker + f"\nAt `{pr['head']['sha']}`:\n\n" + text)
            provider._unchanged(context, pr)
            self.spaces.scope(write=True)
            if resolve:
                provider.pr_threads(context, thread=thread, action="resolve")
            receipt = self.spaces.control.report(f"PR {review}: {text}", kind="result")
            return {"review": review, "resolved": resolve or row["isResolved"], "evidence": receipt["reference"]}

    def finish(self, scope):
        """Called under the work-order lock by XO's conclude outcome."""
        require_capability(self.scope("xo")["role"], "conclude")
        if scope["paused"]:
            raise ValueError("Work is paused.")
        with self.store.connect() as db:
            self.settled(db, scope["work_order"])
            if db.execute(
                "SELECT 1 FROM operations WHERE work_order=? AND state IN ('queued','running')", (scope["work_order"],)
            ).fetchone():
                raise ValueError("A development operation is still active.")
            workspace = db.execute(
                "SELECT * FROM order_workspaces WHERE work_order=?", (scope["work_order"],)
            ).fetchone()
            work = record(db, "work_orders", scope["work_order"])
            inspected = db.execute(
                "SELECT revision FROM order_inspections WHERE member=? AND work_order=?",
                (self.member, scope["work_order"]),
            ).fetchone()
        if not workspace:
            from .completion import Completion

            return {"_completion": Completion(self.store, self.member).require("work_order")}
        workspace = dict(workspace)
        if not work["pull_request"]:
            self.sync(workspace)
            if git(workspace["root"], "diff", "--quiet", workspace["base_revision"], "HEAD", check=False).returncode:
                raise ValueError("XO must submit the integrated work before concluding it.")
            from .completion import Completion

            return {"_completion": Completion(self.store, self.member).require("work_order")}
        provider = GitHub(self.store, self.member)
        context = provider._context(job=None)
        current = provider.pr_get(context)
        self.match(current["pr"]["branch"], current["pr"]["base"], workspace)
        revision = current["pr"]["revision"]
        if not inspected or inspected["revision"] != revision:
            raise ValueError("Monitor section review for the current integrated result before concluding it.")
        if current["pr"]["state"] == "merged":
            # Reconcile a lost merge response even when GitHub already deleted the source branch.
            self.spaces._verify(workspace, self.spaces.checkout())
            if self.spaces.changes(workspace["root"]):
                raise ValueError("The integration workspace has unexpected changes; preserved.")
            if git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip() != revision:
                git(workspace["root"], "fetch", "--no-tags", self.spaces.remote(), revision)
                git(workspace["root"], "merge", "--ff-only", revision)
                if git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip() != revision:
                    raise ValueError("The integration workspace has unexpected local commits; preserved.")
        elif self.sync(workspace)["revision"] != revision:
            raise ValueError("The work-order PR changed after inspection. Monitor it again.")
        if current["pr"]["state"] != "merged" and current["ci"]["state"] != "passed":
            raise ValueError("CI has not passed for the current integrated PR revision.")
        CodexReviews(provider).require_ready(context, revision)
        from .completion import Completion

        gate = Completion(self.store, self.member).require(
            "work_order", revision=revision,
            mechanical={"ci": {"passed": current["ci"]["state"] == "passed", "revision": revision},
                        "workspace": {"clean": True, "revision": revision}},
        )
        Completion(self.store, self.member).verify(gate)
        self.scope("xo")
        merged = provider.pr_merge(context, revision=revision)
        if not merged["merged"]:
            raise ValueError("GitHub did not merge the work-order PR. Inspect its current review and CI.")
        return {"revision": revision, "merge_commit": merged["commit"], "pr": work["pull_request"], "_completion": gate}
