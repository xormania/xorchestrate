"""Review subjects reuse application composition and spy on the repository port."""

import json
from dataclasses import asdict
from unittest.mock import patch

from agent_telemetry.domain import CaptureContext, Event, observation_digest

from tests.hexagonal.support.application import Provider, Sessions
from tests.hexagonal.support.telemetry import Fixture
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import Capability, OrchestrationError


class ReviewProvider(Provider):
    def discover(self):
        self.discoveries += 1
        return [Capability("change", "Controlled source scope", {"type": "object"})]


class ReviewFixture(Fixture):
    def __init__(self, root, **options):
        super().__init__(root, local_options={"sources": ["application", "native", "capability",
            "evaluator", "process", "fixture", "late"], **options})

    def invocation(self, state):
        self.app.providers["tools"] = ReviewProvider()
        work = self.create_work("independently readable work")
        address = {"missing-provider": "missing/change", "spoofed-operation": "private/change"}.get(state, "tools/change")
        canary = "private invocation argument canary"
        arguments = {"capability": address, "arguments": {"value": 1, "private_note": canary + "n" * 2048},
            "work_id": work, "idempotency_key": "review-invocation"}
        if state == "spoofed-operation":
            reference = self.app.call("invoke", {"capability": "tools/change", "arguments": {"value": 1},
                "work_id": work, "idempotency_key": "review-reference"})
            arguments["operation_id"] = reference["operation_id"]
        if state == "rejected":
            self.app.guardrail = RuleGuardrail({"rules": [{"actions": ["invoke"], "actors": ["owner"],
                "providers": ["tools"], "effect": "deny"}]})
        try:
            self.app.call("invoke", arguments)
            outcome = "completed"
        except OrchestrationError as error:
            outcome = error.code
        if state == "spoofed-operation":
            assert len(self.app.providers["tools"].calls) == 1, "The rejected malformed request must not execute an effect"
        self.app.guardrail = RuleGuardrail()
        rows = self.events(event_type="application.requested", work_id=work)
        retained = [self.app.telemetry.get_event(row["event_id"]) for row in rows]
        requested = next(row for row in retained if row["payload"].get("action") == "invoke"
            and row["payload"]["arguments"]["capability"] == address)
        assert requested["payload"]["arguments"]["arguments"]["private_note"].startswith(canary)
        return requested["event_id"], canary, address, work, outcome

    def deny_invocation(self, selector, address):
        value = address.partition("/")[0] if selector == "providers" else address
        self.app.guardrail = RuleGuardrail({"rules": [{"actors": ["observer"], selector: [value], "effect": "deny"}]})

    def work_readable(self, work):
        return self.app.call("get_work", {"work_id": work}, actor="observer")["work"]["id"] == work

    def capture_artifact_usage(self):
        result = self.capture("fixture.retained_usage", {"usage": {"input_tokens": 0, "output_tokens": None},
            "retained_content": "u" * 2048}, event_id="retained-usage")
        assert result["status"] == "recorded"
        assert self.artifact_reference("retained-usage")["size_bytes"] > 512

    def populate_byte_limited_status(self):
        for index in range(20):
            result = self.capture("fixture.prefix", {"retained_content": "x" * 20000}, event_id=f"status-{index:02d}")
            assert result["status"] == "recorded"
        latest = self.capture("late.sample", {"marker": "latest authorized source"}, event_id="status-latest")
        assert latest["status"] == "recorded"
        return latest

    def _resource(self, resource, label):
        if resource == "work_id":
            return self.create_work("sparse source " + label)
        if resource == "session_id":
            self.app.sessions["agent"] = Sessions()
            return self.app.call("start_session", {"driver": "agent", "specification": {},
                "idempotency_key": "sparse-" + label})["session"]["id"]
        self.app.providers["tools"] = ReviewProvider()
        return self.app.call("invoke", {"capability": "tools/change", "arguments": {"value": 1},
            "idempotency_key": "sparse-" + label})["operation_id"]

    def sparse_filtered_pages(self, resource):
        wanted, other = self._resource(resource, "wanted"), self._resource(resource, "other")
        count = {"rows": 0}
        horizon = 20001

        def row(index):
            identity = wanted if index == horizon else other
            scope = {"actor": "owner", resource: identity}
            event = Event(f"sparse-{index}", "fixture.sparse", json.dumps({"index": index}),
                CaptureContext("project-tests", "dataset-tests", "collector-tests", "fixture-sparse",
                    task_id=scope.get("work_id"), session_id=scope.get("session_id")),
                source_json=json.dumps({"xorchestrate": scope}))
            return {**asdict(event), "sequence": index, "digest": observation_digest(event),
                "captured_at": "2026-11-07T12:00:00Z", "unresolved_links": []}

        class Repository:
            def query(self, *, after=0, limit=100, **filters):
                rows = [row(index) for index in range(after + 1, min(after + limit, horizon) + 1)]
                count["rows"] += len(rows)
                return rows

            def status(self):
                return {"status": "ready", "max_sequence": horizon, "event_count": horizon}

            def close(self):
                pass

        batches, after = [], 0
        with patch.object(self.app.telemetry._repository_type, "inspect", side_effect=lambda *args, **kwargs: Repository()):
            for _ in range(32):
                before = count["rows"]
                page = self.event_page(event_type="fixture.sparse", limit=1, after=after, **{resource: wanted})
                batches.append({"page": page, "raw_rows": count["rows"] - before})
                if page["events"] or page["next_after"] <= after:
                    break
                after = page["next_after"]
        return batches, wanted

    def capture_large_usage(self):
        source = {"usage": {"input_tokens": 0, "output_tokens": None, "retained_detail": "x" * 350000}}
        captured = self.capture("fixture.retained_usage", source, event_id="large-valid-usage")
        assert captured["status"] == "recorded"
        assert self.artifact_reference("large-valid-usage")["size_bytes"] > 262144
        return source

    def bounded_usage(self, budget):
        arguments = {"event_type": "fixture.retained_usage"}
        if budget is not None:
            arguments["max_bytes"] = budget
        try:
            return self.usage(**arguments)
        except OrchestrationError as error:
            return {"error": error.code}

    def complete_payload(self, event_id):
        chunks, offset = [], 0
        for _ in range(32):
            part = self.payload(event_id, offset=offset, limit=32768)
            chunk = self.payload_bytes(part)
            assert len(chunk) <= 32768
            chunks.append(chunk)
            if part["next_offset"] is None:
                return json.loads(b"".join(chunks))
            assert part["next_offset"] > offset
            offset = part["next_offset"]
        raise AssertionError("The finite raw measurement must have a bounded continuation")
