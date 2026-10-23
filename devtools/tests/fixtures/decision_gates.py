"""Shared decision gates arrangements without test methods."""

import json

from devtools.api import API
from devtools.failures import Failure, failure_info
from fixtures import completion as test_completion
from fixtures.completion import CompletionFixture


class CompletionDecisionFixture:
    setUp = CompletionFixture.setUp
    configure = CompletionFixture.configure
    report = CompletionFixture.report
    operation = CompletionFixture.operation
    assignment = CompletionFixture.assignment

    def ambiguous(self, state, questions, model):
        self.requests.append((state, questions, model))
        result = test_completion.answer(state, questions, model)
        result["answers"]["done_when"]["noul"] = 0.75
        return result

    def pending(self, **arguments):
        with self.assertRaises(Failure) as blocked:
            self.gate.require("job", job=self.job, **arguments)
        self.assertEqual("decision_pending", failure_info(blocked.exception)["reason"])
        with self.store.connect() as db:
            return dict(db.execute("SELECT * FROM decisions WHERE id=?", (
                failure_info(blocked.exception)["details"]["decision"],
            )).fetchone())

    def resolve(self, pending, proof, value=True):
        return API(self.state, pending["arbitrator"]).call("decision_resolve", {
            "decisions": {name: value for name in json.loads(pending["unresolved"])},
            "reason": "Inspected the retained expected and observed behavior against the completion conditions.",
            "evidence": [proof["evidence"]],
        })
