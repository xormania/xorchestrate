"""Assessment through configured evaluators, with explicit escalation of uncertainty.

An assessment is retained evidence bound to the exact inputs evaluated; it never
authorizes an action. The host supplies storage, record creation and evidence
callbacks plus live suppliers for the evaluators and assessment configuration.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Callable

from .acceptance import assessment_input_binding, assessment_work_input
from .model import JSON, Assessment, OrchestrationError, Record, public_error_message, record_view
from .ports import Evaluator, StateStore

_UNCERTAIN_OUTCOMES = ["uncertain", "borderline", "review"]


def validate_assessment(assessment: Assessment) -> None:
    if not isinstance(assessment, Assessment) or not isinstance(assessment.outcome, str) or not assessment.outcome:
        raise OrchestrationError("The evaluator did not return an assessment.", "evaluation_invalid")
    confidence = assessment.confidence
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise OrchestrationError("Assessment confidence must be between zero and one, or unknown.", "evaluation_invalid")


class WorkAssessment:
    def __init__(
        self, store: StateStore, *, evaluators: Callable[[], dict[str, Evaluator]], configuration: Callable[[], JSON],
        create: Callable[..., Record], evidence: Callable[..., Record],
    ):
        self.store = store
        self.evaluators = evaluators
        self.configuration = configuration
        self.create = create
        self.evidence = evidence

    def _unresolved(self, assessment: Assessment, threshold) -> bool:
        return (assessment.confidence is None or assessment.confidence < threshold
                or assessment.outcome in self.configuration().get("uncertain_outcomes", _UNCERTAIN_OUTCOMES))

    def assess(self, args: JSON, actor: str, context: JSON) -> JSON:
        configuration, evaluators = self.configuration(), self.evaluators()
        triage = configuration.get("triage")
        if not triage:
            raise OrchestrationError("No triage evaluator is configured.", "evaluation_unavailable")
        if triage not in evaluators:
            raise OrchestrationError(f"Evaluator {triage} is unavailable.", "evaluation_unavailable")
        evaluation_context = {**deepcopy(context), "request_context": deepcopy(args.get("context", {}))}
        # Display titles and write bookkeeping must not silently become material
        # evaluator inputs. Everything supplied from mutable work/evidence is
        # bound to its observed content and rechecked when consumed.
        evaluation_context["work"] = assessment_work_input(context.get("work"), configuration)
        input_binding = assessment_input_binding(evaluation_context, configuration)
        evaluations = []
        first = evaluators[triage].assess(args["question"], deepcopy(evaluation_context))
        validate_assessment(first)
        evaluations.append({"evaluator": triage, **asdict(first), "evidence": list(deepcopy(first.evidence))})
        threshold = self.configuration().get("confidence_threshold", 0.8)
        uncertain = self._unresolved(first, threshold)
        heavier = self.configuration().get("escalate")
        unresolved = uncertain
        escalation_error = None
        if uncertain and heavier:
            try:
                evaluators = self.evaluators()
                if heavier not in evaluators:
                    raise OrchestrationError(f"Evaluator {heavier} is unavailable.", "evaluation_unavailable")
                assessment = evaluators[heavier].assess(args["question"], {**deepcopy(evaluation_context), "triage": deepcopy(evaluations[0])})
                validate_assessment(assessment)
                evaluations.append({"evaluator": heavier, **asdict(assessment), "evidence": list(deepcopy(assessment.evidence))})
                unresolved = self._unresolved(assessment, threshold)
            except Exception as error:
                escalation_error = {"code": getattr(error, "code", "evaluation_unavailable"), "message": public_error_message(error)}
        data = {"question": args["question"], "work_id": args.get("work_id"), "evaluations": evaluations,
                "conclusion": deepcopy(evaluations[-1]), "unresolved": unresolved, "escalation_error": escalation_error}
        with self.store.transaction() as tx:
            # The evidence describes exactly the snapshot evaluated, even when the
            # work changes during inference. It confers no permission on later work.
            data["work_version"] = context["work"]["version"] if context.get("work") else None
            data["input_binding"] = input_binding
            data["request_context"] = deepcopy(args.get("context", {}))
            data["acceptance_binding"] = deepcopy((context.get("acceptance_basis") or {}).get("binding"))
            data["assessment_configuration_identity"] = (context.get("acceptance_basis") or {}).get("assessment_configuration_identity")
            record = self.create(tx, "assessment", actor, data)
            if args.get("work_id"):
                self.evidence(tx, actor, args["work_id"], data, {"type": "assessment", "assessment_id": record.key})
            return {"assessment": record_view(record)}
