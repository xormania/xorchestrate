"""Source-bound request populations and scripted provider outcomes."""
from __future__ import annotations


def population(count=4, *, source_size=100):
    candidates, sources, tasks = [], [], []
    for index in range(count):
        candidate_id, unit_id = f"candidate-{index}", f"unit-{index}"
        source = f"def work_{index}(port):\n    return port.read({str(index)!r})\n" + "# evidence\n" * source_size
        sources.append({"unit_id": unit_id, "path": f"sample/work_{index}.py", "role": "core", "source": source,
                        "support_for": [candidate_id], "sha256": "source-" + str(index)})
        candidates.append({"candidate_id": candidate_id, "unit_id": unit_id, "path": f"sample/work_{index}.py",
                           "role": "core", "contract": {"role": "core", "declaration": {"allowed_roles": ["core"]}}})
        tasks.append({"task_id": candidate_id + ":semantic", "candidate_id": candidate_id,
                      "question": f"Does complete candidate {candidate_id} violate the declared role contract?"})
    context = {"candidates": candidates, "sources": sources, "before_sources": [], "missing_inputs": [],
               "profile": {"id": "generic.packet", "roles": {"core": {"allowed_roles": ["core"]}}},
               "subject": {"source_sha256": "whole-repo-subject"},
               "source_scope": {"selected": [item["path"] for item in sources], "changed_ranges": []}}
    return tasks, context


def aligned_choices(payload, attempt):
    outcomes = ("concern", "no_concern", "insufficient_evidence", "concern")
    answers = {}
    for key, question in payload["questions"].items():
        index = next(index for index in range(4) if f"candidate-{index}" in question["instructions"])
        outcome = outcomes[index]
        answers[key] = {"type": "choice", "choice": outcome, "confidence": .8,
                        "probabilities": {name: int(name == outcome) for name in question["criteria"]}}
    return {"status": 200, "body": {"model": payload["model"], "answers": answers,
                                     "usage": {"input_tokens": 13, "output_tokens": 5}}}


def transient_then_success(status):
    def reply(payload, attempt):
        if attempt == 1:
            return {"status": status, "body": {"error": {"code": "temporary", "message": "private diagnostic body"}}}
        return {"status": 200, "body": None}
    return reply


def overflow_unless_single(payload, attempt):
    if len(payload["questions"]) > 1:
        return {"status": 400, "body": {"error": {"code": "max_tokens_exceeded", "message": "private diagnostic body"}},
                "headers": {"x-typesafe-request-id": "fixture-request-01"}}
    return {"status": 200, "body": None}
