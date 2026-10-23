"""Shared advice arrangements without test methods."""

import json
from unittest.mock import patch

from devtools.api import API
from devtools.jev import MODEL
from fixtures.workspaces import WorkspaceFixture


def answer(state, questions, model=MODEL):
    answers = {}
    for name, question in questions.items():
        kind = question["type"]
        if kind == "noul":
            answers[name] = {"type": kind, "noul": 0.8}
        else:
            options = list(question["criteria"]) if kind == "choice" else list(map(str, range(len(question["criteria"]))))
            selected = "unknown" if "unknown" in options else options[0]
            answers[name] = {"type": kind, "confidence": 0.9,
                             "probabilities": {key: float(key == selected) for key in options},
                             "choice" if kind == "choice" else "score": selected if kind == "choice" else 0}
            if kind == "score":
                answers[name]["legend"] = dict(enumerate(question["criteria"]))
                answers[name]["legend"] = {str(k): v for k, v in answers[name]["legend"].items()}
    return {"model": model, "answers": answers, "usage": {"input_tokens": 600, "output_tokens": 40}}


class AdviceFixture:

    def setUp(self):
        WorkspaceFixture.setUp(self)
        self.configure({})
        self.xo = API(self.state, "xo")
        self.requests = []

        def evaluate(state, questions, model):
            self.requests.append((state, questions, model))
            return answer(state, questions, model)

        provider = patch("devtools.advice.Jev.evaluate", side_effect=evaluate)
        self.provider = provider.start()
        self.addCleanup(provider.stop)

    def configure(self, value):
        config = json.loads((self.state / "team.json").read_text())
        if value is None:
            config["project"].pop("configuration_file", None)
        else:
            config["project"]["configuration_file"] = "configuration.json"
            settings = {**value, "features": {"advice": True, "selection": False,
                                              "filtering": False, "completion": False,
                                              **value.get("features", {})}}
            (self.state / "configuration.json").write_text(json.dumps({"version": 1, "project": settings}))
        (self.state / "team.json").write_text(json.dumps(config))

    def assess(self, sources=None, questions=None, api=None):
        return (api or self.api).call("work_assess", {
            "sources": sources or [{"path": "src/Example.php"}],
            "questions": questions or [{"name": "relevant", "instructions": "Does this implement the required behavior?"}],
        })

    def retained(self, result):
        return json.loads(self.api.artifacts.read(int(result["evidence"].split(":")[1]), limit=256 * 1024)["content"])
