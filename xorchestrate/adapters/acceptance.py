"""Replaceable acceptance policies, independent of evaluation and authorization."""
import hashlib
import json

from ..model import OrchestrationError


class RequiredChecksAcceptance:
    """Accept sufficient current evidence, optionally requiring an assessment.

The application validates mandatory proof and dependency freshness regardless
of a favorable policy answer. Deployments may supply any compatible policy.
There is no required model, score threshold, role or organizational hierarchy.
"""

    def __init__(self, options=None):
        options = {} if options is None else options
        if not isinstance(options, dict) or set(options) - {"require_assessment", "assessment_outcomes"}:
            raise OrchestrationError("Unknown required-checks acceptance options.", "configuration_error")
        if type(options.get("require_assessment", False)) is not bool:
            raise OrchestrationError("require_assessment must be boolean.", "configuration_error")
        outcomes = options.get("assessment_outcomes", ["pass", "accept", "accepted", "sufficient"])
        if not isinstance(outcomes, list) or not outcomes or any(not isinstance(value, str) or not value for value in outcomes):
            raise OrchestrationError("assessment_outcomes must be nonempty names.", "configuration_error")
        self.require_assessment = options.get("require_assessment", False)
        self.assessment_outcomes = tuple(outcomes)
        self.identity = "required-checks-v1:" + hashlib.sha256(json.dumps({
            "require_assessment": self.require_assessment, "assessment_outcomes": outcomes},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def evaluate(self, snapshot):
        reasons = list(snapshot["issues"])
        assessment = snapshot.get("assessment")
        if self.require_assessment and not assessment:
            reasons.append({"code": "assessment_required"})
        if assessment and (assessment.get("unresolved") or assessment.get("conclusion", {}).get("outcome") not in self.assessment_outcomes):
            reasons.append({"code": "assessment_unfavorable"})
        return {"accepted": not reasons, "reasons": reasons}

    def close(self):
        pass
