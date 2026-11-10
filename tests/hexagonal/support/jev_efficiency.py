"""Choice-provider adapters for batching and exact-request reuse contracts."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from xorchestrate.model import Assessment, OrchestrationError

CRITERIA = {"concern": "Concrete concern", "no_concern": "No supported concern", "insufficient_evidence": "Cannot decide"}


@contextmanager
def http_choices():
    state = {"requests": [], "choices": {}, "response": None, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(payload)
            answers = {}
            for key, question in payload["questions"].items():
                choice = state["choices"].get(key, "no_concern")
                answers[key] = {"type": "choice", "choice": choice, "confidence": .8,
                                "probabilities": {outcome: int(outcome == choice) for outcome in question["criteria"]}}
            response = state["response"]
            if response is None:
                response = {"model": payload["model"], "answers": answers,
                            "usage": {"input_tokens": 13, "output_tokens": 5}}
            self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state["options"] = {"endpoint": f"http://127.0.0.1:{server.server_port}/choice", "allow_http": True,
                        "model": "fixture-choice", "criteria": CRITERIA}
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class CountingEvaluator:
    """Return known valid choices and explicit fresh-call usage for cache contracts."""

    def __init__(self):
        self.endpoint = "https://fixture.invalid/choice"
        self.model = "fixture-choice"
        self.criteria = dict(CRITERIA)
        self.calls = []
        self.error = False

    def _answer(self):
        if self.error:
            raise OrchestrationError("Fixture unavailable", "evaluation_unavailable")
        return Assessment("concern", .8, "", (), {"model": self.model,
                          "probabilities": {key: int(key == "concern") for key in self.criteria},
                          "usage": {"input_tokens": 13, "output_tokens": 5}})

    def assess(self, question, context):
        self.calls.append((question, context))
        return self._answer()

    def assess_many(self, questions, context):
        self.calls.append((questions, context))
        answers = {key: self._answer() for key in questions}
        for index, answer in enumerate(answers.values()):
            answer.details.update(usage_scope="batch", batch_question_ids=list(questions))
            if index:
                answer.details.pop("usage")
        return answers


def tasks():
    return [{"task_id": "candidate:semantic", "question": "Does this candidate violate its role?"},
            {"task_id": "candidate:ports", "question": "Does this candidate couple a port to an adapter?"}]
