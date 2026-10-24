"""Owner requirements, measurements and acceptance in the active application.

Plans and contributions are deliberately separate records from the requirement
basis. A provider response is an observation from a configured measurement
boundary; caller prose and evaluator confidence are not mechanical proof.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .model import Capability, Conflict, OrchestrationError


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _view(record):
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


def _same(left, right):
    # Python equality conflates JSON booleans with numbers.
    return _hash(left) == _hash(right)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate measurement field")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite measurement value")


_STRING = {"type": "string", "minLength": 1}
_OBJECT = {"type": "object"}
_REVISION = {"type": "integer", "minimum": 0}
_OUTCOMES = {"type": "array", "items": _STRING, "uniqueItems": True, "minItems": 1}
_CHECK = {"type": "object", "additionalProperties": False, "required": ["capability", "outcomes"], "properties": {
    "capability": _STRING, "outcomes": _OUTCOMES, "bindings": _OBJECT,
}}


def _action(description, properties, required, read=False):
    return Capability("", description, {"type": "object", "additionalProperties": False,
        "properties": properties, "required": list(required)}, {"readOnlyHint": read})


ACCEPTANCE_ACTIONS = {
    "set_requirements": _action("Explicitly revise the owner objective and required outcomes, independently of plans.", {
        "work_id": _STRING, "expected_revision": _REVISION, "objective": _STRING,
        "outcomes": {"type": "object", "minProperties": 1, "additionalProperties": _STRING},
        "checks": {"type": "object", "additionalProperties": _CHECK},
    }, ("work_id", "expected_revision", "objective", "outcomes", "checks")),
    "set_candidate": _action("Select the exact candidate identity to be measured for this work.", {
        "work_id": _STRING, "expected_revision": _REVISION,
        "candidate": {"type": "object", "minProperties": 1, "required": ["identity"], "properties": {"identity": _STRING}},
    }, ("work_id", "expected_revision", "candidate")),
    "create_contribution": _action("Record a subordinate contribution without replacing owner requirements.", {
        "work_id": _STRING, "goal": _STRING, "outcomes": _OUTCOMES, "metadata": _OBJECT,
    }, ("work_id", "goal", "outcomes")),
    "update_contribution": _action("Revise a contribution or cancel it without cancelling required outcomes.", {
        "work_id": _STRING, "contribution_id": _STRING, "expected_version": {"type": "integer", "minimum": 1},
        "changes": {"type": "object", "minProperties": 1, "additionalProperties": False, "properties": {
            "goal": _STRING, "outcomes": _OUTCOMES, "status": _STRING, "metadata": _OBJECT,
        }},
    }, ("work_id", "contribution_id", "expected_version", "changes")),
    "run_measurement": _action("Execute a required check through its configured provider and retain actual returned bindings.", {
        "work_id": _STRING, "check_id": _STRING, "measurement_key": _STRING, "arguments": _OBJECT,
    }, ("work_id", "check_id", "measurement_key")),
    "accept_work": _action("Decide acceptance from current bound measurements and configured policy.", {
        "work_id": _STRING, "assessment_id": _STRING,
    }, ("work_id",)),
    "get_acceptance": _action("Read current coverage and applicable acceptance; retain superseded decisions as history.", {
        "work_id": _STRING,
    }, ("work_id",), read=True),
}


def _observation(raw):
    """Unwrap provider transport envelopes without inventing measurement fields."""
    if not isinstance(raw, dict) or raw.get("isError"):
        return None
    if "structuredContent" in raw:
        raw = raw["structuredContent"]
    elif "content" in raw:
        blocks = raw["content"]
        if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict) or blocks[0].get("type") != "text":
            return None
        try:
            raw = json.loads(blocks[0]["text"], object_pairs_hook=_unique, parse_constant=_invalid_constant)
        except (KeyError, ValueError, TypeError, RecursionError):
            return None
    return raw.get("measurement") if isinstance(raw, dict) and isinstance(raw.get("measurement"), dict) else None


class AcceptanceMixin:
    """Use cases sharing the application's store and authorization boundary."""

    def _set_requirements(self, args, actor, context):
        outcomes, checks = args["outcomes"], args["checks"]
        if any(not key for key in outcomes) or any(not key for key in checks):
            raise OrchestrationError("Outcome and check identifiers must be nonempty.")
        for check in checks.values():
            if set(check["outcomes"]) - set(outcomes):
                raise OrchestrationError("A check may cover only declared required outcomes.", "unknown_outcome")
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            current = tx.get("requirements", args["work_id"])
            if (current.version if current else 0) != args["expected_revision"]:
                raise Conflict("Requirements changed; explicitly revise their current basis.")
            data = {"work_id": args["work_id"], "objective": args["objective"],
                "outcomes": deepcopy(outcomes), "checks": deepcopy(checks)}
            data["identity"] = _hash(data)
            data.update({"revised_by": actor, "revised_at": self.clock()})
            record = tx.put("requirements", args["work_id"], data, current.version if current else None)
            self._create(tx, "requirement_revision", actor, {**data, "revision": record.version})
            self._event(tx, "requirements.revised", actor, work_id=args["work_id"], revision=record.version, identity=data["identity"])
            return {"requirements": _view(record)}

    def _set_candidate(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            current = tx.get("candidate", args["work_id"])
            if (current.version if current else 0) != args["expected_revision"]:
                raise Conflict("Candidate selection changed.")
            data = {"work_id": args["work_id"], "candidate": deepcopy(args["candidate"]),
                "identity": _hash(args["candidate"]), "selected_by": actor, "selected_at": self.clock()}
            record = tx.put("candidate", args["work_id"], data, current.version if current else None)
            self._create(tx, "candidate_revision", actor, {**data, "revision": record.version})
            self._event(tx, "candidate.selected", actor, work_id=args["work_id"], revision=record.version)
            return {"candidate": _view(record)}

    def _create_contribution(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            basis = self._require(tx, "requirements", args["work_id"])
            if set(args["outcomes"]) - set(basis.data["outcomes"]):
                raise OrchestrationError("Contribution refers to an undeclared outcome.", "unknown_outcome")
            record = self._create(tx, "contribution", actor, {**args, "status": "open", "requirement_revision": basis.version})
            return {"contribution": _view(record)}

    def _update_contribution(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            current = self._require(tx, "contribution", args["contribution_id"])
            if current.data["work_id"] != args["work_id"]:
                raise OrchestrationError("Contribution belongs to another work item.", "wrong_scope")
            basis = self._require(tx, "requirements", args["work_id"])
            if set(args["changes"].get("outcomes", [])) - set(basis.data["outcomes"]):
                raise OrchestrationError("Contribution refers to an undeclared outcome.", "unknown_outcome")
            record = tx.put("contribution", current.key, {**current.data, **deepcopy(args["changes"])}, args["expected_version"])
            self._event(tx, "contribution.updated", actor, work_id=args["work_id"], contribution_id=current.key)
            return {"contribution": _view(record)}

    def _policy_identity(self):
        policy = getattr(self, "acceptance", None)
        identity = getattr(policy, "identity", None)
        if policy is None:
            return None
        if not isinstance(identity, str) or not identity:
            raise OrchestrationError("Acceptance policy must expose its material configuration identity.", "configuration_error")
        return identity

    def _assessment_configuration_identity(self):
        """Bind inference configuration only when an assessment is consumed.

        Mechanical measurement applicability does not depend on an evaluator
        that was neither selected nor used as acceptance evidence.
        """
        configuration = {"triage": self.assessment.get("triage"), "escalate": self.assessment.get("escalate"),
            "confidence_threshold": self.assessment.get("confidence_threshold", 0.8),
            "uncertain_outcomes": self.assessment.get("uncertain_outcomes", ["uncertain", "borderline", "review"])}
        selected = {name for name in (configuration["triage"], configuration["escalate"]) if name}
        identities = getattr(self, "evaluator_configurations", {})
        return _hash({"assessment": configuration, "evaluators": {name: identities.get(name) for name in sorted(selected)}})

    def _acceptance_snapshot(self, tx, work_id):
        requirements, candidate = tx.get("requirements", work_id), tx.get("candidate", work_id)
        measurements = sorted((_view(item) for item in tx.list("measurement") if item.data["work_id"] == work_id), key=lambda item: item["sequence"])
        operations = {}
        for item in measurements:
            operation = tx.get("operation", item["operation_id"])
            operations[item["operation_id"]] = _view(operation) if operation else None
        snapshot = {"requirements": _view(requirements) if requirements else None,
            "candidate": _view(candidate) if candidate else None, "measurements": measurements,
            "operations": operations, "policy_identity": self._policy_identity(),
            "assessment_configuration_identity": self._assessment_configuration_identity(),
            "provider_configurations": self._measurement_providers(requirements.data if requirements else None)}
        snapshot["binding"] = self._acceptance_binding(snapshot)
        snapshot["issues"], snapshot["coverage"] = self._measurement_issues(snapshot)
        return snapshot

    @staticmethod
    def _acceptance_binding(snapshot):
        requirement, candidate = snapshot["requirements"], snapshot["candidate"]
        return {"requirements": {"identity": requirement["identity"], "revision": requirement["version"]} if requirement else None,
            "candidate": {"identity": candidate["identity"], "revision": candidate["version"]} if candidate else None,
            "measurements": [[item["id"], item["version"], item.get("raw_digest")] for item in snapshot["measurements"]],
            "operations": [[key, value["version"] if value else None] for key, value in sorted(snapshot["operations"].items())],
            "policy_identity": snapshot["policy_identity"], "provider_configurations": snapshot["provider_configurations"]}

    def _measurement_providers(self, requirements):
        names = {check["capability"].partition("/")[0] for check in requirements["checks"].values()} if requirements else set()
        configured = getattr(self, "provider_configurations", {})
        return {name: configured.get(name) for name in sorted(names)}

    def _acceptance_context(self, work_id):
        with self.store.transaction() as tx:
            snapshot = self._acceptance_snapshot(tx, work_id)
            snapshot["contributions"] = [_view(item) for item in tx.list("contribution") if item.data["work_id"] == work_id]
            return snapshot

    def _verify_acceptance_context(self, tx, context):
        """Fence requirement/candidate changes after configured authorization."""
        previous = context.get("acceptance_basis")
        if previous is None:
            return
        work_id = context["work_id"]
        for kind in ("requirements", "candidate"):
            before, current = previous.get(kind), tx.get(kind, work_id)
            if (before is None) != (current is None) or (before and before["version"] != current.version):
                raise Conflict("Acceptance authorization basis changed; evaluate current requirements and candidate.")
        if previous.get("policy_identity") != self._policy_identity():
            raise Conflict("Acceptance policy changed after authorization.")
        if previous.get("provider_configurations") != self._measurement_providers(previous.get("requirements")):
            raise Conflict("Measurement provider configuration changed after authorization.")

    @staticmethod
    def _measurement_issues(snapshot):
        requirement, candidate = snapshot["requirements"], snapshot["candidate"]
        issues, coverage = [], {}
        if not requirement:
            return [{"code": "requirements_missing"}], coverage
        coverage = {outcome: {"covered": False, "checks": []} for outcome in requirement["outcomes"]}
        if not candidate:
            return [{"code": "candidate_missing"}], coverage
        for check_id, check in requirement["checks"].items():
            matching = [item for item in snapshot["measurements"] if item["check_id"] == check_id
                and item["requirements_identity"] == requirement["identity"]
                and item["requirement_revision"] == requirement["version"]
                and item["candidate_identity"] == candidate["identity"]
                and item["candidate_revision"] == candidate["version"]
                and item.get("provider_identity") == snapshot["provider_configurations"].get(check["capability"].partition("/")[0])]
            if not matching:
                previous = any(item["check_id"] == check_id for item in snapshot["measurements"])
                issues.append({"code": "measurement_stale" if previous else "measurement_missing", "check_id": check_id})
                continue
            unresolved = [item for item in matching if item["status"] in {"pending", "unknown"}
                or (_observation(item.get("raw_response")) or {}).get("status") in {"pending", "running", "unknown", "uncertain"}]
            if unresolved:
                issues.append({"code": "measurement_unresolved", "check_id": check_id,
                    "measurement_ids": [item["id"] for item in unresolved]})
                if any(item["status"] == "completed" for item in unresolved):
                    issues.append({"code": "measurement_incomplete", "check_id": check_id})
                continue
            item = matching[-1]
            operation = snapshot["operations"].get(item["operation_id"])
            if (not operation or operation["status"] != "completed" or operation.get("external_result_origin") != "adapter"
                    or operation.get("work_id") != requirement["work_id"]
                    or operation.get("action") != "invoke"
                    or operation.get("arguments", {}).get("capability") != check["capability"]
                    or not _same(operation.get("arguments", {}).get("arguments", {}).get("measurement"), item["request_binding"])
                    or not _same(operation.get("external_result"), item.get("raw_response"))
                    or item.get("raw_digest") != _hash(item.get("raw_response"))):
                issues.append({"code": "measurement_unverified", "check_id": check_id})
                continue
            observed = _observation(item.get("raw_response"))
            expected = {"work_id": requirement["work_id"], "check_id": check_id,
                "requirements_identity": requirement["identity"], "requirement_revision": requirement["version"],
                "candidate": candidate["candidate"], "bindings": check.get("bindings", {})}
            if not observed or any(not _same(observed.get(key), value) for key, value in expected.items()):
                issues.append({"code": "measurement_binding_mismatch", "check_id": check_id})
                continue
            if observed.get("status") != "completed":
                issues.append({"code": "measurement_incomplete", "check_id": check_id, "observed_status": observed.get("status")})
                continue
            if observed.get("passed") is not True:
                issues.append({"code": "required_check_failed", "check_id": check_id})
                continue
            observed_outcomes = observed.get("outcomes", {})
            if not isinstance(observed_outcomes, dict) or any(observed_outcomes.get(outcome) is not True for outcome in check["outcomes"]):
                issues.append({"code": "required_outcome_failed", "check_id": check_id})
                continue
            for outcome in check["outcomes"]:
                coverage[outcome]["covered"] = True
                coverage[outcome]["checks"].append(check_id)
        for outcome, status in coverage.items():
            if not status["covered"]:
                issues.append({"code": "outcome_uncovered", "outcome": outcome})
        return issues, coverage

    def _run_measurement(self, args, actor, context):
        key = _hash([actor, "measurement", args["measurement_key"]])
        fingerprint = _hash(args)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            existing = tx.get("measurement", key)
            if existing:
                if existing.data["fingerprint"] != fingerprint:
                    raise Conflict("Measurement key already describes a different request.")
                operation = tx.get("operation", existing.data["operation_id"])
                if (existing.data["status"] in {"pending", "unknown"} and operation
                        and operation.data["status"] == "completed" and operation.data.get("external_result_origin") == "adapter"):
                    raw = operation.data["external_result"]
                    existing = tx.put("measurement", key, {**existing.data, "status": "completed",
                        "raw_response": deepcopy(raw), "raw_digest": _hash(raw), "finished_at": self.clock()}, existing.version)
                    self._event(tx, "measurement.recovered", actor, work_id=args["work_id"], measurement_id=key)
                return {"measurement": _view(existing), "replayed": True}
            requirement = self._require(tx, "requirements", args["work_id"])
            candidate = self._require(tx, "candidate", args["work_id"])
            check = requirement.data["checks"].get(args["check_id"])
            if check is None:
                raise OrchestrationError("Unknown required check.", "unknown_check")
            binding = {"work_id": args["work_id"], "check_id": args["check_id"],
                "requirements_identity": requirement.data["identity"], "requirement_revision": requirement.version,
                "candidate": deepcopy(candidate.data["candidate"]), "bindings": deepcopy(check.get("bindings", {})),
                "outcomes": list(check["outcomes"])}
            invoke_key = "measurement:" + key
            operation_id = _hash([actor, invoke_key])
            sequence = 1 + max((item.data["sequence"] for item in tx.list("measurement") if item.data["work_id"] == args["work_id"]), default=0)
            record = self._create(tx, "measurement", actor, {
                "work_id": args["work_id"], "check_id": args["check_id"], "fingerprint": fingerprint,
                "requirements_identity": requirement.data["identity"], "requirement_revision": requirement.version,
                "candidate_identity": candidate.data["identity"], "candidate_revision": candidate.version,
                "provider_identity": getattr(self, "provider_configurations", {}).get(check["capability"].partition("/")[0]),
                "request_binding": binding, "operation_id": operation_id, "status": "pending", "sequence": sequence,
            }, key)
        try:
            result = self.call("invoke", {"work_id": args["work_id"], "capability": check["capability"],
                "arguments": {"measurement": binding, "arguments": deepcopy(args.get("arguments", {}))},
                "idempotency_key": invoke_key}, actor=actor)
            raw = result["invocation"]["result"]
        except Exception as error:
            with self.store.transaction() as tx:
                current = self._require(tx, "measurement", key)
                operation = tx.get("operation", operation_id)
                uncertain = operation is not None and operation.data["status"] in {"pending", "unknown", "completed_unapplied"}
                tx.put("measurement", key, {**current.data, "status": "unknown" if uncertain else "failed",
                    "error": {"code": getattr(error, "code", type(error).__name__)}, "finished_at": self.clock()}, current.version)
                self._event(tx, "measurement.unresolved" if uncertain else "measurement.failed", actor, work_id=args["work_id"], measurement_id=key)
            raise
        with self.store.transaction() as tx:
            current = self._require(tx, "measurement", key)
            record = tx.put("measurement", key, {**current.data, "status": "completed", "raw_response": deepcopy(raw),
                "raw_digest": _hash(raw), "finished_at": self.clock()}, current.version)
            self._event(tx, "measurement.completed", actor, work_id=args["work_id"], measurement_id=key, operation_id=operation_id)
            return {"measurement": _view(record), "replayed": False}

    def _accept_work(self, args, actor, context):
        if getattr(self, "acceptance", None) is None:
            raise OrchestrationError("No acceptance policy is configured.", "acceptance_unavailable")
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            snapshot = self._acceptance_snapshot(tx, args["work_id"])
            assessment = self._require(tx, "assessment", args["assessment_id"]) if args.get("assessment_id") else None
        if assessment:
            snapshot["assessment"] = _view(assessment)
            if assessment.data.get("work_id") != args["work_id"] or assessment.data.get("acceptance_binding") != snapshot["binding"]:
                snapshot["issues"].append({"code": "assessment_stale"})
            if assessment.data.get("assessment_configuration_identity") != snapshot["assessment_configuration_identity"]:
                snapshot["issues"].append({"code": "assessment_configuration_stale"})
        else:
            snapshot["assessment"] = None
        decision = self.acceptance.evaluate(deepcopy(snapshot))
        if not isinstance(decision, dict) or type(decision.get("accepted")) is not bool or not isinstance(decision.get("reasons", []), list):
            raise OrchestrationError("Acceptance policy returned an invalid decision.", "acceptance_invalid")
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            current = self._acceptance_snapshot(tx, args["work_id"])
            if current["binding"] != snapshot["binding"]:
                raise Conflict("Acceptance dependencies changed during the decision.")
            if assessment:
                latest = self._require(tx, "assessment", assessment.key)
                if (latest.version != assessment.version
                        or current["assessment_configuration_identity"] != snapshot["assessment_configuration_identity"]):
                    raise Conflict("Assessment or its configuration changed during the decision.")
            accepted = decision["accepted"] and not snapshot["issues"]
            data = {"work_id": args["work_id"], "accepted": accepted, "binding": deepcopy(snapshot["binding"]),
                "issues": deepcopy(snapshot["issues"]), "policy_decision": deepcopy(decision), "coverage": snapshot["coverage"],
                "assessment_id": assessment.key if assessment else None, "assessment_version": assessment.version if assessment else None,
                "assessment_configuration_identity": snapshot["assessment_configuration_identity"] if assessment else None,
                "sequence": 1 + max((item.data["sequence"] for item in tx.list("acceptance_decision") if item.data["work_id"] == args["work_id"]), default=0)}
            record = self._create(tx, "acceptance_decision", actor, data)
            self._event(tx, "work.accepted" if accepted else "work.acceptance_rejected", actor, work_id=args["work_id"], decision_id=record.key)
            return {"acceptance": _view(record), "current": True}

    def _work_acceptance_view(self, tx, work_id):
        snapshot = self._acceptance_snapshot(tx, work_id)
        decisions = sorted((_view(item) for item in tx.list("acceptance_decision") if item.data["work_id"] == work_id), key=lambda item: item["sequence"])
        for item in decisions:
            item["current"] = item["binding"] == snapshot["binding"]
            if item.get("assessment_id"):
                assessment = tx.get("assessment", item["assessment_id"])
                item["current"] = item["current"] and bool(assessment and assessment.version == item["assessment_version"]
                    and item.get("assessment_configuration_identity") == snapshot["assessment_configuration_identity"]
                    and assessment.data.get("assessment_configuration_identity") == snapshot["assessment_configuration_identity"])
        latest = decisions[-1] if decisions else None
        return {"requirements": snapshot["requirements"], "candidate": snapshot["candidate"],
            "coverage": snapshot["coverage"], "acceptance_issues": snapshot["issues"],
            "measurements": snapshot["measurements"], "acceptance_history": decisions,
            "accepted": bool(latest and latest["current"] and latest["accepted"]),
            "contributions": [_view(item) for item in tx.list("contribution") if item.data["work_id"] == work_id]}

    def _get_acceptance(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return self._work_acceptance_view(tx, args["work_id"])
