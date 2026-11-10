"""Revision-aware PR preparation through replaceable hosting and workspace ports.

The application journal owns invocation replay. These operations add narrow,
target-scoped custody and progress to that same operation, without a second queue.
GitHub remains the authority for readiness and integration.
"""
from copy import deepcopy

from .lifecycles import default_definitions
from .model import Capability, OrchestrationError, canonical_digest
from .outcomes import before_effect
from .ports import PullRequestHost, PullRequestWorkspace, StateStore
from .state import StateEngine

TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
SHA = {"type": "string", "pattern": "^[0-9a-f]{40}$"}
REPOSITORY = {"type": "string", "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", "maxLength": 200}


def _capability(name, description, fields, required, read=False):
    return Capability(name, description, {"type": "object", "properties": fields,
        "required": required, "additionalProperties": False}, {"readOnlyHint": read})


class PullRequestOperations:
    """Capability provider for observed handoffs and recoverable draft preparation."""

    def __init__(self, host: PullRequestHost, workspace: PullRequestWorkspace, store: StateStore, clock, *, state_engine=None):
        self.host, self.workspace, self.store, self.clock = host, workspace, store, clock
        self.state_engine = state_engine if state_engine is not None else StateEngine(default_definitions())

    def discover(self):
        observed = {"repository": REPOSITORY, "number": {"type": "integer", "minimum": 1},
                    "expected_head": SHA}
        return [
            _capability("pr.inspect", "Observe the current candidate, checks and review blockers. Use a new invocation key for a fresh observation.",
                observed, ["repository", "number"], True),
            _capability("pr.handoff", "Produce an observed candidate handoff without changing draft/readiness state.",
                {**observed, "verification": {"type": "array", "items": TEXT, "maxItems": 100},
                 "review": {"type": "object"}}, ["repository", "number"], True),
            _capability("branch.refresh", "Checkpoint explicitly selected tracked work, then merge the identified target in an owned workspace.",
                {"workspace": TEXT, "expected_head": SHA, "target": SHA,
                 "paths": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 4096}, "maxItems": 1000, "uniqueItems": True}},
                ["workspace", "expected_head", "target"]),
            _capability("pr.open_draft", "Publish a checked revision and open or adopt its compatible draft. Existing owner text is preserved.",
                {"repository": REPOSITORY, "workspace": TEXT, "branch": TEXT, "base": TEXT,
                 "expected_head": SHA, "title": {"type": "string", "minLength": 1, "maxLength": 256},
                 "body": {"type": "string", "maxLength": 60000},
                 "review": {"type": "object", "properties": {
                     "head": SHA, "tree": SHA, "scope": TEXT, "reviewer": TEXT,
                     "findings": {"type": "array", "maxItems": 1000},
                     "verification": {"type": "array", "items": TEXT, "maxItems": 100}},
                     "required": ["head", "tree", "scope", "reviewer", "findings", "verification"],
                     "additionalProperties": False}},
                ["repository", "workspace", "branch", "base", "expected_head", "title", "body", "review"]),
        ]

    def invoke(self, name, arguments, context):
        if name in {"pr.inspect", "pr.handoff"}:
            result = self.inspect(arguments)
            if name == "pr.handoff":
                result.update({"verification": deepcopy(arguments.get("verification", [])),
                    "review_evidence": self._review_applicability(arguments.get("review"), result),
                    "readiness_changed": False})
            return result
        if name == "branch.refresh":
            return self.refresh(arguments, context)
        if name == "pr.open_draft":
            return self.open_draft(arguments, context)
        with before_effect():
            raise OrchestrationError("Unknown PR operation.", "not_found")

    def _review_applicability(self, review, observation):
        """Bind the supplied assertion to source content; do not certify its author."""
        result = {"status": "unavailable", "independence_verified": False, "record": deepcopy(review)}
        candidate = observation.get("candidate")
        if review is None:
            return result
        result["status"] = "unverified"
        if not isinstance(review, dict) or not candidate or not review.get("head") or not review.get("tree"):
            return result
        if (review["head"] != candidate.get("head")
                or (review.get("base") is not None and review["base"] != candidate.get("base"))):
            result["status"] = "historical"
            return result
        try:
            source = self.host.branch(candidate["source_repository"], candidate["head"])
            if source and source.get("sha") == review["head"]:
                result["status"] = "applicable" if source.get("tree") == review["tree"] else "historical"
        except Exception:
            pass
        return result

    @staticmethod
    def _candidate(snapshot):
        return {key: deepcopy(snapshot.get(key)) for key in
            ("repository", "number", "head", "base", "merge", "head_ref", "base_ref", "source_repository")}

    @staticmethod
    def _stable(snapshot):
        return canonical_digest({key: value for key, value in snapshot.items() if key != "observed_at"})

    def inspect(self, arguments):
        started = self.clock()
        try:
            first = self.host.snapshot(arguments["repository"], arguments["number"])
            final = self.host.snapshot(arguments["repository"], arguments["number"])
        except Exception:
            return {"status": "incomplete", "technical_validation": "unknown",
                "blockers": ["hosting_observation_unavailable"],
                "observation": {"started_at": started, "finished_at": self.clock()}}
        changed = self._stable(first) != self._stable(final)
        changed = changed or (arguments.get("expected_head") is not None and arguments["expected_head"] != final.get("head"))
        complete = first.get("complete") is True and final.get("complete") is True
        blockers = []
        if final.get("state") != "open":
            blockers.append("pr_closed")
        if final.get("mergeable") is False:
            blockers.append("merge_conflicts")
        if any(item.get("resolved") is not True for item in final.get("threads", [])):
            blockers.append("unresolved_review_threads")
        validation = final.get("validation", "unknown")
        if validation != "passed":
            blockers.append("checks_" + validation)
        if final.get("provenance", {}).get("status") != "qualified":
            validation = "unknown"
            blockers.append("candidate_evidence_unqualified")
        if not complete:
            blockers.append("incomplete_observation")
            validation = "unknown"
        if changed:
            blockers.append("observation_superseded")
            validation = "unknown"
        return {"status": "superseded" if changed else "observed" if complete else "incomplete",
            "candidate": self._candidate(final), "technical_validation": validation,
            "draft": final.get("draft"), "blockers": blockers, "snapshot": deepcopy(final),
            "observation": {"started_at": started, "finished_at": self.clock()},
            "next_action": "inspect_again" if changed or not complete else "review_observed_state"}

    def _claim(self, context, target, intent):
        """A crash never releases unknown custody merely because a worker vanished."""
        key = context.get("operation_id")
        with self.store.transaction() as tx:
            operation = tx.get("operation", key) if key else None
            if operation is None or operation.data.get("status") != "pending":
                raise OrchestrationError("PR mutations require their active application operation.", "pr_operation_required")
            target_key = canonical_digest(target)
            for prior in tx.list("operation"):
                if (prior.key != key and prior.data.get("pr_target") == target_key
                        and prior.data.get("status") in {"pending", "unknown", "completed_unapplied"}):
                    raise OrchestrationError("A prior operation owns this target; inspect and reconcile it first.",
                        "pr_target_busy", {"operation_id": prior.key})
            self.state_engine.apply(tx, "operation", key, "progress", {**operation.data, "pr_target": target_key,
                "pr_intent": deepcopy(intent), "pr_progress": {"step": "prepared"}}, operation.version,
                {"actor": context.get("actor", operation.data["actor"]), "at": self.clock()})
        return key

    def _progress(self, key, step, **confirmed):
        with self.store.transaction() as tx:
            operation = tx.get("operation", key)
            if operation is None or operation.data.get("status") != "pending":
                raise OrchestrationError("Operation custody changed during preparation.", "pr_operation_changed")
            progress = {**operation.data.get("pr_progress", {}), **deepcopy(confirmed), "step": step}
            self.state_engine.apply(tx, "operation", key, "progress", {**operation.data, "pr_progress": progress},
                operation.version, {"actor": operation.data["actor"], "at": self.clock()})

    @staticmethod
    def _same_head(identity, expected):
        if identity.get("head") != expected:
            raise OrchestrationError("The candidate head changed; inspect the new revision.", "pr_candidate_changed")

    def refresh(self, arguments, context):
        with before_effect():
            identity = self.workspace.identity(arguments["workspace"])
            self._same_head(identity, arguments["expected_head"])
            key = self._claim(context, ["branch", identity["repository"].lower(), identity["branch"]],
                {"action": "branch.refresh", **deepcopy(arguments)})
            self._progress(key, "refresh_dispatch")
        result = self.workspace.refresh(arguments["workspace"], arguments["expected_head"],
            arguments["target"], arguments.get("paths", []))
        self._progress(key, "refreshed", result=result)
        return {**result, "operation_id": key, "observed_at": self.clock()}

    @staticmethod
    def _compatible(pr, arguments):
        return (pr.get("state") == "open" and pr.get("draft") is True
            and pr.get("base_ref") == arguments["base"] and pr.get("head_ref") == arguments["branch"]
            and pr.get("repository") == arguments["repository"]
            and pr.get("source_repository") == arguments["repository"])

    def open_draft(self, arguments, context):
        with before_effect():
            identity = self.workspace.identity(arguments["workspace"])
            self._same_head(identity, arguments["expected_head"])
            review = arguments["review"]
            if (review.get("head") != identity["head"] or review.get("tree") != identity["tree"]
                    or not review.get("scope") or not review.get("reviewer")
                    or not isinstance(review.get("findings"), list) or not isinstance(review.get("verification"), list)):
                raise OrchestrationError("Review evidence must identify this checked source tree and scope.", "pr_review_required")
            if identity.get("repository") != arguments["repository"] or identity.get("branch") != arguments["branch"]:
                raise OrchestrationError("The owned workspace does not identify the requested source branch.", "pr_workspace_mismatch")
            found = self.host.find(arguments["repository"], arguments["branch"])
            if len(found) > 1 or (found and not self._compatible(found[0], arguments)):
                raise OrchestrationError("The existing PR has incompatible state or target; owner changes were preserved.", "pr_incompatible")
            key = self._claim(context, ["branch", identity["repository"].lower(), identity["branch"]],
                {"action": "pr.open_draft", **deepcopy(arguments)})
            self._progress(key, "publish_dispatch")
        published = self.workspace.publish(arguments["workspace"], arguments["expected_head"], arguments["branch"])
        self._progress(key, "published", published=published)
        remote = self.host.branch(arguments["repository"], arguments["branch"])
        if not remote or remote.get("sha") != identity["head"] or remote.get("tree") != identity["tree"]:
            return {"status": "superseded", "published": published, "operation_id": key}
        # Repeat lookup after push. Creating is the only operation which writes a body.
        found = self.host.find(arguments["repository"], arguments["branch"])
        if len(found) > 1 or (found and not self._compatible(found[0], arguments)):
            return {"status": "superseded", "published": published, "operation_id": key}
        adopted = bool(found)
        if found:
            pr = found[0]
        else:
            self._progress(key, "create_dispatch")
            pr = self.host.create(arguments["repository"], arguments["branch"], arguments["base"],
                arguments["title"], arguments["body"])
            self._progress(key, "created", pr_number=pr["number"])
        observation = self.inspect({"repository": arguments["repository"], "number": pr["number"],
            "expected_head": arguments["expected_head"]})
        final = observation.get("snapshot", {})
        compatible = self._compatible(final, arguments) and final.get("head") == identity["head"]
        status = "published" if observation["status"] == "observed" and compatible else "superseded"
        self._progress(key, "observed", pr_number=pr["number"], final_status=status)
        return {"status": status, "adopted": adopted, "pr_number": pr["number"],
            "published": published, "observation": observation, "operation_id": key}
