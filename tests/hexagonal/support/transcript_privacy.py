"""Actual Claude transcript ingestion through collector and public payload reads."""

import json

from tests.hexagonal.support.native import peer_options
from tests.hexagonal.support.telemetry import Fixture


class TranscriptPrivacyFixture:
    def __init__(self, root):
        self.subject = Fixture(root, local_options={"artifact_threshold_bytes": 64},
            sessions={"native": {"adapter": "claude", "options": peer_options("claude", state=root / "state",
                                                                               application_tools=True)}})
        self.app = self.subject.app
        public = self.subject.create_work("outer transcript work remains visible")
        self.secret = "INDEPENDENT-PRIVATE-PARTIAL-TRANSCRIPT-REPLY"
        private = self.subject.create_work(self.secret)
        session = self.app.call("start_session", {"driver": "native", "work_id": public,
            "specification": {}, "idempotency_key": "transcript-privacy-start"})["session"]
        self.backend = session["backend"]
        self.driver = self.app.sessions["native"]
        self.live = self.driver._get(self.backend)
        self.source = root / (self.backend["native_id"] + ".jsonl")
        self.public = public
        self.subject.restrict_work(private)
        first = self.record("assistant", {"type": "tool_use", "id": "app-call", "name": "mcp__xorchestrate__get_work",
            "input": {"work_id": private}})
        self.result = self.record("user", {"type": "tool_result", "tool_use_id": "app-call",
            "content": [{"type": "text", "text": self.secret}]})
        self.initial = first + self.result[:-1]

    def record(self, kind, block):
        return (json.dumps({"sessionId": self.backend["native_id"], "type": kind,
            "message": {"role": kind, "content": [block]}}) + "\n").encode()

    def capture_partial(self):
        self.source.write_bytes(self.initial)
        self.driver._hook(self.live, {"hook_event_name": "SessionStart", "session_id": self.backend["native_id"],
            "model": "fixture-model", "transcript_path": str(self.source)})
        assert self.driver.drain_telemetry(timeout=2), "source task must finish at the real collector boundary"

    def complete_record(self):
        with self.source.open("ab") as stream:
            stream.write(b"\n")
        self.driver.poll(self.backend)
        assert self.driver.drain_telemetry(timeout=2), "completed source record must reach the collector"

    def public_reads(self):
        rows = [row for row in self.subject.events(actor="observer", work_id=self.public)
                if row["event_type"] in {"native.transcript", "native.transcript_tail"}]
        assert rows, "the public query must actually return the outer source observations"
        exact = []
        for row in rows:
            pieces, offset = [], 0
            while True:
                part = self.subject.payload(row["event_id"], actor="observer", offset=offset, limit=16384)
                pieces.append(self.subject.payload_bytes(part))
                if part["next_offset"] is None:
                    break
                offset = part["next_offset"]
            exact.append({"event_type": row["event_type"], "payload": json.loads(b"".join(pieces))})
        return rows + exact

    def close(self):
        self.subject.close()
