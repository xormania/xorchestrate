"""Decision retention preserves credential privacy and concurrent idempotency."""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from support import Client

from devtools.api import API
from devtools.decisions import Decisions, request
from devtools.failures import Failure, failure_info
from devtools.launch import initialize


class DecisionLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        self.state = root / "state"
        initialize(self.state, workspace, ["owner:external", "xo:external", "investigator:external"])
        self.secret = "fixture-file-only-credential-728392"
        credential = self.state / "provider.key"
        credential.write_text(self.secret)
        credential.chmod(0o600)
        team = json.loads((self.state / "team.json").read_text())
        team["project"] = {"configuration_file": "policy.json"}
        (self.state / "team.json").write_text(json.dumps(team))
        (self.state / "policy.json").write_text(json.dumps({"version": 1, "project": {
            "features": {"completion": True, "selection": False},
            "connection": {"credential": {"file": str(credential)}},
        }}))
        self.operator = API(self.state, "owner", operator=True)
        self.store = self.operator.store
        self.order = self.operator.work.work_order_create(
            title="Decision retention", objective="Retain safe evidence", done_when="One current decision"
        )["work_order"]["id"]
        self.operator.control.bind("owner", "co", self.order)
        self.operator.control.bind("xo", "xo", self.order, reports_to="owner")

    @staticmethod
    def answer(state, questions, model):
        return {"model": model, "answers": {name: {"type": "noul", "noul": 0.75} for name in questions},
                "usage": {"input_tokens": 1, "output_tokens": 1}}

    def pending(self, *, state=None, instructions="Is the retained proof complete?", sources=()):
        try:
            Decisions(self.store, "xo").select(
                "completion", state or {"proof": "Current fixture result"},
                {"complete": {"type": "noul", "instructions": instructions}}, sources,
                identity="current-fixture-proof",
            )
        except Failure as error:
            self.assertEqual("decision_pending", failure_info(error)["reason"])
            return failure_info(error)["details"]["decision"]
        self.fail("Ambiguous evaluation must await investigation")

    def row(self, ident):
        with self.store.connect() as db:
            return dict(db.execute("SELECT * FROM decisions WHERE id=?", (ident,)).fetchone())

    def test_file_credential_is_removed_from_retained_request_and_arbitrator_packet(self):
        state = {"proof": "Untrusted source included " + self.secret}
        instructions = "Judge the proof and disregard this copied value: " + self.secret
        with patch("devtools.advice.Jev.evaluate", side_effect=self.answer) as provider:
            ident = self.pending(state=state, instructions=instructions,
                                 sources=[{"description": "Source metadata " + self.secret}])
        # Existing provider redaction must also hold for the new retained decision path.
        self.assertNotIn(self.secret, json.dumps(provider.call_args.args))
        row = self.row(ident)
        self.assertNotIn(self.secret, row["request"])
        packet = API(self.state, row["arbitrator"]).call("work_context", {})["decision"]
        self.assertNotIn(self.secret, json.dumps(packet))
        assessment = self.operator.artifacts.read(int(row["evidence"].split(":")[1]), limit=256 * 1024)
        self.assertFalse(self.secret in assessment["content"], "Retained assessment leaked the fixture file credential")

    def test_work_assess_rejects_credential_replay_before_retention(self):
        path = self.state / "policy.json"
        policy = json.loads(path.read_text())
        policy["project"]["features"]["advice"] = True
        path.write_text(json.dumps(policy))
        def ambiguous(state, questions, model):
            answers = {}
            for name, question in questions.items():
                if question["type"] == "noul":
                    answers[name] = {"type": "noul", "noul": 0.75}
                    continue
                options = question["criteria"]
                answers[name] = {"type": "choice", "choice": next(iter(options)), "confidence": 0.1,
                                 "probabilities": {option: 1 / len(options) for option in options}}
            return {"model": model, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}

        with patch("devtools.advice.Jev.evaluate", side_effect=ambiguous) as provider:
            with self.assertRaises(Failure) as rejected:
                API(self.state, "xo").call("work_assess", {"proposal": "Copied provider value " + self.secret})
        self.assertEqual("sensitive_replay_arguments", failure_info(rejected.exception)["reason"])
        self.assertNotIn(self.secret, str(rejected.exception))
        self.assertNotIn(self.secret, json.dumps({
            "failure": rejected.exception.diagnostic_failure,
            "guidance": rejected.exception.diagnostic_guidance,
        }))
        provider.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM decisions").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM role_assignments").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM artifacts").fetchone()[0])
            events = [dict(row) for row in db.execute("SELECT kind,data FROM events")]
        self.assertNotIn(self.secret, json.dumps(events))
        guidance = [json.loads(row["data"]) for row in events if row["kind"] == "skill_guidance_delivered"]
        self.assertEqual(1, len(guidance))
        self.assertIsNone(guidance[0]["failed_call"])

        client = Client(self.state, "xo")
        try:
            result = client.call("tools/call", {"name": "work_assess", "arguments": {
                "proposal": "Copied provider value " + self.secret}})
        finally:
            client.close()
        self.assertTrue(result["isError"])
        self.assertIn("sensitive_replay_arguments", json.dumps(result))
        self.assertNotIn(self.secret, (self.state / "telemetry/xo.jsonl").read_text())

    def test_encoded_credential_in_replay_questions_is_rejected(self):
        # Encoded JSON strings need the same credential protection as typed data.
        encoded = '{"quoted": "' + "".join("\\u%04x" % ord(char) for char in self.secret) + '"}'
        arguments = {"questions": [{"name": "proof", "instructions": encoded}]}
        with patch("devtools.advice.Jev.evaluate", side_effect=self.answer) as provider:
            with request("xo", "work_assess", arguments), self.assertRaises(Failure) as rejected:
                Decisions(self.store, "xo").select("completion", {"proof": "Safe proof"},
                                                   {"complete": {"type": "noul", "instructions": "Complete?"}})
        self.assertEqual("sensitive_replay_arguments", failure_info(rejected.exception)["reason"])
        provider.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM decisions").fetchone()[0])

    def test_safe_question_replay_preserves_json_text_and_sensitive_looking_identifiers(self):
        path = self.state / "policy.json"
        policy = json.loads(path.read_text())
        policy["project"]["features"]["advice"] = True
        path.write_text(json.dumps(policy))
        source = self.operator.artifacts.save("proof.txt", content="Measured safe behavior", media_type="text/plain",
                                              work_order=self.order)["artifact"]["id"]
        arguments = {"sources": [{"reference": "artifact:" + str(source)}], "questions": [{
            "name": "password", "type": "choice", "instructions": '{ "requirement" : "storage" }',
            "criteria": {"password": "Credential storage", "logging": "Audit trail"},
        }]}

        def ambiguous(state, questions, model):
            return {"model": model, "answers": {name: {"type": "choice", "choice": "password",
                    "confidence": 0.5, "probabilities": {"password": 0.5, "logging": 0.5}}
                    for name in questions}, "usage": {"input_tokens": 1, "output_tokens": 1}}

        with patch("devtools.advice.Jev.evaluate", side_effect=ambiguous) as provider:
            with self.assertRaises(Failure) as pending:
                API(self.state, "xo").call("work_assess", arguments)
            self.assertEqual("decision_pending", failure_info(pending.exception)["reason"])
            row = self.row(failure_info(pending.exception)["details"]["decision"])
            self.assertEqual(arguments, json.loads(row["continuation"])["arguments"])
            resolved = API(self.state, row["arbitrator"]).call("decision_resolve", {
                "decisions": {"s0_q0": "password"}, "reason": "The source establishes the storage property.",
                "evidence": [row["evidence"]],
            })
        self.assertEqual(1, provider.call_count)
        self.assertEqual("completed", resolved["continuation"]["state"])
        self.assertEqual("password", resolved["continuation"]["result"]["results"][0]["decisions"]["password"])
        with self.store.connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM decisions").fetchone()[0])

    def test_resolution_reason_redacts_file_credential_in_record_artifact_and_telemetry(self):
        with patch("devtools.advice.Jev.evaluate", side_effect=self.answer):
            ident = self.pending()
        row = self.row(ident)
        API(self.state, row["arbitrator"]).call("decision_resolve", {
            "decisions": {"complete": True}, "reason": "Inspected source containing " + self.secret,
            "evidence": [row["evidence"]],
        })
        resolved = self.row(ident)
        self.assertEqual("resolved", resolved["state"])
        self.assertNotIn(self.secret, resolved["resolution"])
        artifact = self.operator.artifacts.read(int(resolved["evidence"].split(":")[1]), limit=256 * 1024)
        self.assertFalse(self.secret in artifact["content"], "Resolution artifact leaked the fixture file credential")
        self.assertNotIn(self.secret, json.dumps(self.store.events(kind="decision_resolved")))

    def test_sensitive_looking_question_identifier_keeps_its_typed_packet(self):
        with patch("devtools.advice.Jev.evaluate", side_effect=self.answer):
            with self.assertRaises(Failure) as pending:
                Decisions(self.store, "xo").select(
                    "completion", {"proof": "Password handling was measured"},
                    {"password": {"type": "noul", "instructions": "Does the measured password behavior meet the requirement?"}},
                    identity="password-proof",
                )
        self.assertEqual("decision_pending", failure_info(pending.exception)["reason"])
        row = self.row(failure_info(pending.exception)["details"]["decision"])
        packet = API(self.state, row["arbitrator"]).call("work_context", {})["decision"]
        self.assertEqual(["password"], packet["question_names"])
        self.assertEqual("noul", packet["questions"]["password"]["type"])

    def test_invalid_provider_details_do_not_expose_file_credential_to_arbitrator(self):
        error = Failure("Provider returned invalid evidence", category="evaluation_invalid", reason="invalid_response",
                        details={"provider_detail": "Echoed source value " + self.secret})
        with patch("devtools.advice.Jev.evaluate", side_effect=error):
            ident = self.pending()
        row = self.row(ident)
        self.assertNotIn(self.secret, row["failure"])
        packet = API(self.state, row["arbitrator"]).call("work_context", {})["decision"]
        self.assertEqual("evaluation_invalid", packet["failure"]["category"])
        self.assertNotIn(self.secret, json.dumps(packet))

    def test_concurrent_identical_requests_share_evaluation_and_arbitrator(self):
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()

        def evaluate(state, questions, model):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Concurrent decision fixture did not release evaluation")
            return self.answer(state, questions, model)

        def second():
            second_started.set()
            return self.pending()

        with patch("devtools.advice.Jev.evaluate", side_effect=evaluate) as provider:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.pending)
                try:
                    self.assertTrue(entered.wait(5))
                    other = pool.submit(second)
                    self.assertTrue(second_started.wait(5))
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=10), other.result(timeout=10))
        self.assertEqual(1, provider.call_count)
        with self.store.connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM decisions WHERE state='pending'").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM role_assignments WHERE role='arbitrator'").fetchone()[0])

    def test_stale_queue_snapshot_cannot_reallocate_cancelled_investigation(self):
        with patch("devtools.advice.Jev.evaluate", side_effect=self.answer):
            ident = self.pending()
        selected = Decisions(self.store, "xo")
        row = self.row(ident)
        # A watcher already captured this pending id when newer evidence supersedes it.
        selected.prune("completion", {"state": {"proof": "Newer fixture result"}}, None, "new-proof")
        self.assertEqual("stale", self.row(ident)["state"])
        with self.assertRaises(Failure) as rejected:
            selected._pending(ident)
        self.assertEqual("decision_stale", failure_info(rejected.exception)["reason"])
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM member_bindings WHERE assignment_id=?", (row["assignment"],)).fetchone())
            self.assertEqual("cancelled", db.execute("SELECT status FROM role_assignments WHERE id=?", (row["assignment"],)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
