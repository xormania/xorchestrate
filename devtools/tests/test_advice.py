import copy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from unittest.mock import patch

from fixtures.advice import AdviceFixture, answer
from fixtures.commands import responsibility
from fixtures.delivery import DeliveryFixture
from fixtures.workspaces import WorkspaceFixture
from support import Client, adopt_plan, fixture_work

from devtools.api import API
from devtools.configuration import defaults as load_policy
from devtools.failures import failure_info
from devtools.jev import JevUnavailable, TypeSafeAdapter, checked_response
from devtools.store import SCHEMA_VERSION, Store, canonical, stamp
from devtools.workspaces import Workspaces


class AdviceTests(AdviceFixture, unittest.TestCase):


    def test_opt_in_role_catalogs_and_real_mcp_cached_result(self):
        self.configure(None)
        self.assertNotIn("work_assess", {r["name"] for r in self.api.tools()})
        with self.assertRaisesRegex(ValueError, "not available"):
            self.assess()
        self.configure({})
        self.assertNotIn("assignment_advise", {r["name"] for r in self.xo.tools()})
        self.assertNotIn("assignment_advise", {r["name"] for r in self.api.tools()})
        self.assertNotIn("work_assess", {r["name"] for r in API(self.state, "docs").tools()})
        first = self.assess()
        client = Client(self.state, "coder")
        self.addCleanup(client.close)
        result = client.tool("work_assess", {
            "sources": [{"path": "src/Example.php"}],
            "questions": [{"name": "relevant", "instructions": "Does this implement the required behavior?"}],
        })
        self.assertTrue(result["cache_hit"])
        self.assertEqual(first["evidence"], result["evidence"])
        self.assertEqual(1, len(self.requests))
        self.assertNotIn("original", json.dumps(result))
        retained = self.api.call("work_context", {"reference": result["evidence"]})
        self.assertIn("questions", json.loads(retained["content"]))

    def test_batch_typed_questions_and_partial_utf8_ranges(self):
        root = WorkspaceFixture.prepared(self)
        (root / "src/Example.php").write_text("aéé rest of file")
        result = self.assess(sources=[{"path": "src/Example.php", "limit": 4}], questions=[
            {"name": "relevant", "instructions": "Relevant?"},
            {"name": "layer", "type": "choice", "instructions": "Which layer?", "options": ["domain", "storage"]},
            {"name": "fit", "type": "score", "instructions": "How relevant?", "levels": ["unrelated", "related"]},
        ])
        source = result["results"][0]["source"]
        self.assertTrue(source["partial"])
        self.assertEqual(3, source["next_offset"])
        self.assertEqual("aé", self.requests[0][0]["sources"][0]["content"])
        self.assertEqual({"relevant", "layer", "fit"}, result["results"][0]["answers"].keys())
        self.assertEqual({"relevant": True, "layer": "domain", "fit": 0},
                         result["results"][0]["decisions"])
        self.assertIn("partial range", self.requests[0][1]["s0_q0"]["instructions"])
        self.assess(sources=[{"path": "src/Example.php", "offset": 3}])
        with self.assertRaisesRegex(ValueError, "character boundary"):
            self.assess(sources=[{"path": "src/Example.php", "offset": 2}])

    def test_planned_role_scope_and_shared_context_are_supplied_to_jev(self):
        # This fixture isolates requested decisions; completion is tested separately.
        self.configure({"features": {"advice": False}})
        co = API(self.state, "co")
        document = co.call("work_report", {"text": "Prepared specification", "evidence": {
            "name": "scope.md", "content": "Preserve the existing endpoint behavior"}})
        order = co.call("work_context", {})["work"]
        co.call("direct", {**{key: order[key] for key in ("title", "objective", "done_when", "constraints")},
                           "context": [document["evidence"]]})
        adopt_plan(self.xo, [{"name": "Feature", "objective": "Deliver the feature", "done_when": "Feature works",
                             "constraints": "Product changes only"}])
        assignment = {"name": "Feature", "job": "Feature", "role": "coder",
                      "objective": "Implement signed receipts", "done_when": "Receipt signatures validate",
                      "constraints": "Do not modify other endpoints"}
        self.xo.call("orchestrate", {"work": [assignment]})
        self.xo.call("steer", {"action": "resume", "work": "Feature", "assignment": "Feature"})
        self.configure({})
        self.api.call("work_assess", {"proposal": "Add signed receipts"})
        brief = self.requests[-1][0]["brief"]
        self.assertEqual({key: assignment[key] for key in ("name", "role", "objective", "done_when", "constraints")},
                         brief["role_assignment"])
        self.assertEqual([document["evidence"]], brief["context"])
        self.assertNotEqual(brief["job"]["objective"], brief["role_assignment"]["objective"])

    def test_identical_concurrent_evidence_requests_reuse_one_evaluation(self):
        evidence = self.api.artifacts.save("shared.txt", content="same diagnostic")
        sources = [{"reference": "artifact:" + str(evidence["artifact"]["id"])}]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.assess(sources=sources, api=API(self.state, "coder")), range(2)))
        self.assertEqual(1, len(self.requests))
        self.assertEqual(results[0]["evidence"], results[1]["evidence"])
        self.assertEqual({False, True}, {r["cache_hit"] for r in results})

    def test_cache_changes_with_source_question_model_and_work_scope(self):
        first = self.assess()
        self.assertTrue(self.assess()["cache_hit"])
        root = WorkspaceFixture.prepared(self)
        (root / "src/Example.php").write_text("new implementation\n")
        changed = self.assess()
        self.assertNotEqual(first["id"], changed["id"])
        self.assess(questions=[{"name": "relevant", "instructions": "Does it handle recovery?"}])
        self.configure({"model": "jev-1.14.0"})
        self.assess()
        self.xo.call("orchestrate", {"work": [responsibility("Feature") | {"objective": "Different requirement"}]})
        self.assess()
        self.assertEqual(5, len(self.requests))
        events = self.store.events(kind="jev_evaluation")["events"]
        self.assertEqual(5, len(events))
        self.assertTrue(all(row["data"]["outcome"] == "completed" for row in events))

    def test_concurrent_cache_wait_covers_configured_retries(self):
        policy = load_policy()
        policy["transport"].update(timeout=60, max_attempts=3, max_retry_delay=5)
        self.configure({"transport": policy["transport"]})
        evaluating, waiting = Event(), Event()
        original_lock = Workspaces.lock

        @contextmanager
        def checked_lock(spaces, name, timeout=0):
            if name.startswith("decision-"):
                self.assertGreaterEqual(timeout, 190)  # Three 60s attempts and two 5s backoffs.
                if evaluating.is_set():
                    waiting.set()
            with original_lock(spaces, name, timeout):
                yield

        def evaluate(state, questions, model):
            evaluating.set()
            self.assertTrue(waiting.wait(5), "Second request did not wait for the in-flight evaluation")
            return answer(state, questions, model)

        self.provider.side_effect = evaluate
        with patch.object(Workspaces, "lock", checked_lock), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.api.call, "work_assess", {"proposal": "Add logging"})
            self.assertTrue(evaluating.wait(5))
            second = pool.submit(API(self.state, "coder").call, "work_assess", {"proposal": "Add logging"})
            results = [first.result(), second.result()]
        self.assertEqual(1, self.provider.call_count)
        self.assertEqual(results[0]["evidence"], results[1]["evidence"])
        self.assertEqual([False, True], [result["cache_hit"] for result in results])

    def test_sources_reject_escape_symlinks_environment_and_nonregular_files(self):
        root = WorkspaceFixture.prepared(self)
        (root / "src/link.php").symlink_to(self.checkout / "README.md")
        (root / "src/indirect").symlink_to(self.checkout / "src", target_is_directory=True)
        (root / "src/.env.local").write_text("secret")
        (root / "src/binary").write_bytes(b"\0\xff")
        os.mkfifo(root / "src/pipe")
        for path in ("../README.md", "/etc/passwd", ".git", "src/link.php", "src/indirect/Example.php",
                     "src/.env.local", "src/pipe", "src/binary"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.assess(sources=[{"path": path}])
        self.assertEqual([], self.requests)

    def test_scope_checked_before_evidence_is_sent_and_co_cannot_assess(self):
        own = self.api.artifacts.save("diagnostic.txt", content="retained diagnostic")
        ref = "artifact:" + str(own["artifact"]["id"])
        self.assess(sources=[{"reference": ref}])
        self.configure({"features": {"advice": False}})
        fixture_work(self.xo, [responsibility("Other", "docs")])
        self.configure({})
        foreign = API(self.state, "docs").artifacts.save("private.txt", content="other job evidence")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.assess(sources=[{"reference": "artifact:" + str(foreign["artifact"]["id"])}])
        co = API(self.state, "co")
        self.assertNotIn("work_assess", {row["name"] for row in co.tools()})
        for sources in ([{"path": "src/Example.php"}], [{"reference": ref}]):
            with self.subTest(sources=sources), self.assertRaisesRegex(ValueError, "not available"):
                self.assess(sources=sources, api=co)
        from devtools.advice import Advice

        with self.assertRaisesRegex(ValueError, "CO relays"):
            Advice(self.store, "co").assess(sources=[{"reference": ref}], questions=[{"name": "fit", "instructions": "Fit?"}])
        with self.assertRaisesRegex(ValueError, "does not evaluate"):
            co.control.report("Accept it", evaluation={"reference": ref, "stage": "decision"})
        self.assertEqual(1, len(self.requests))

    def test_operation_evidence_uses_existing_scoped_reads_and_reports_partial(self):
        scope = self.api.control.snapshot()
        with self.store.connect() as db:
            ident = db.execute(
                "INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,created,updated) VALUES (?,?,?,?,?,?,'completed',?,?,?)",
                (self.api.member, scope["work_order"], scope["job"], canonical(scope), "work_check", "{}",
                 canonical({"diagnostic": "é" * 5000}), stamp(), stamp()),
            ).lastrowid
        result = self.assess(sources=[{"reference": f"operation:{ident}", "limit": 400}])
        source = result["results"][0]["source"]
        self.assertEqual("characters", source["offset_units"])
        self.assertEqual(400, source["next_offset"])
        self.assertTrue(source["partial"])

    def test_paused_and_rebound_work_rejects_decisions(self):
        self.xo.call("steer", {"action": "pause", "work": "Feature"})
        with self.assertRaisesRegex(ValueError, "active current work"):
            self.assess()
        self.xo.call("steer", {"action": "resume", "work": "Feature"})

        def replace_scope(state, questions, model):
            self.api.control.unbind("coder")
            return answer(state, questions, model)

        self.provider.side_effect = replace_scope
        with self.assertRaisesRegex(ValueError, "active current work"):
            self.assess()

    def test_provider_failure_assigns_arbitrator_without_fabricating_an_assessment(self):
        self.provider.side_effect = JevUnavailable("Jev is unavailable")
        with self.assertRaises(ValueError) as caught:
            self.assess()
        self.assertEqual("decision_pending", failure_info(caught.exception)["reason"])
        self.assertEqual("prepared", self.api.call("work_inspect", {})["workspace"])
        self.assertEqual([], self.api.artifacts.list(query="jev-")["artifacts"])
        self.assertEqual("unavailable", self.store.events(kind="jev_evaluation")["events"][0]["data"]["outcome"])
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM decisions WHERE member='coder'").fetchone()
            self.assertEqual("pending", row["state"])
            self.assertEqual({}, json.loads(row["answers"]))
            self.assertEqual({}, json.loads(row["result"]))
            arbitrator = row["arbitrator"]
        investigator = API(self.state, arbitrator)
        packet = investigator.call("work_context", {})["decision"]
        self.assertEqual({}, packet["evaluation"])
        self.assertEqual("evaluation_unavailable", packet["failure"]["category"])
        with self.assertRaises(ValueError) as retry:
            self.assess()
        self.assertEqual("decision_pending", failure_info(retry.exception)["reason"])
        self.assertEqual(1, self.provider.call_count)
        evidence = investigator.artifacts.save("source-investigation.md", content="Inspected the current source against the question.")
        resolved = investigator.call("decision_resolve", {
            "decisions": {"s0_q0": True}, "reason": "The source inspection establishes the required behavior.",
            "evidence": ["artifact:" + str(evidence["artifact"]["id"])],
        })
        self.assertEqual("completed", resolved["continuation"]["state"], resolved)
        result = resolved["continuation"]["result"]["results"][0]
        self.assertEqual({}, result["answers"])
        self.assertEqual({"relevant": True}, result["decisions"])
        self.assertEqual(1, self.provider.call_count)

    def test_secrets_are_filtered_before_provider_and_unknown_model_is_rejected(self):
        root = WorkspaceFixture.prepared(self)
        (root / "src/Example.php").write_text("Bearer abcdefghi\nprivate-demo-key-12345")
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "private-demo-key-12345"}):
            self.assess()
        sent = json.dumps(self.requests)
        self.assertNotIn("abcdefghi", sent)
        self.assertNotIn("private-demo-key-12345", sent)
        policy = load_policy()
        with self.assertRaisesRegex(ValueError, "pinned"):
            TypeSafeAdapter(policy["transport"], policy["connection"]).evaluate({}, {}, "jev-latest")

    def test_invalid_questions_and_sources_do_not_call_provider(self):
        bad = [
            {"sources": [{"path": "src/Example.php", "reference": "artifact:1"}]},
            {"sources": [{}]},
            {"questions": [{"name": "a", "type": "choice", "instructions": "Select"}]},
            {"questions": [{"name": "a", "instructions": "Select", "levels": ["one", "two"]}]},
            {"questions": [{"name": "a", "type": "choice", "instructions": "Select", "options": ["same", "same"]}]},
            {"questions": [{"name": "a", "instructions": "First"}, {"name": "a", "instructions": "Second"}]},
            {"sources": [{"path": "src/Example.php", "limit": 8001}]},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess(**value)
        self.assertFalse(self.requests)


    def test_replay_preserves_exact_sanitized_request_after_source_changes(self):
        root = WorkspaceFixture.prepared(self)
        (root / "src/Example.php").write_text("Uncommitted behavior; Bearer private-demo-key")
        result = self.assess()
        (root / "src/Example.php").write_text("Replacement behavior")
        saved = self.retained(result)
        state, questions, model = self.requests[-1]
        self.assertEqual({"state": state, "questions": questions, "model": model}, saved["request"])
        self.assertIn("Uncommitted behavior", json.dumps(saved))
        self.assertNotIn("private-demo-key", json.dumps(saved))
        current = self.api.control.snapshot()
        self.assertEqual({key: current[key] for key in saved["binding"]}, saved["binding"])
        self.assertNotIn("request", result)

    def test_scope_assessment_combines_sources_with_configured_rubric_and_invalidates_cache(self):
        root = WorkspaceFixture.prepared(self)
        (root / "src/Other.php").write_text("Other evidence")
        args = {"proposal": "Add logging while implementing the JSON response", "sources": [
            {"path": "src/Example.php"}, {"path": "src/Other.php"}]}
        first = self.api.call("work_assess", args)
        self.assertEqual("scope", first["purpose"])
        state, questions, _ = self.requests[-1]
        self.assertEqual(2, len(state["sources"]))
        self.assertEqual(load_policy()["scope"]["questions"], questions)
        self.assertTrue(self.api.call("work_assess", args)["cache_hit"])
        policy = load_policy()
        policy["scope"]["version"] += 1
        policy["scope"]["questions"]["scope"]["instructions"] += " Logging may be a separate proposal."
        self.configure({"scope": policy["scope"]})
        changed = self.api.call("work_assess", args)
        self.assertNotEqual(first["id"], changed["id"])
        self.assertEqual(2, self.retained(changed)["policy"]["scope"]["version"])
        for bad in ({}, {"proposal": "x", "questions": [{"name": "x", "instructions": "x"}]}):
            with self.assertRaises(ValueError):
                self.api.call("work_assess", bad)

    def test_sensitive_rubric_identifiers_preserve_schema_and_scrub_text(self):
        policy = load_policy()
        secret = "private-rubric-secret"
        policy["scope"]["questions"] = {
            "password": {"type": "noul", "instructions": "Check " + secret},
            "api_key": {"type": "choice", "instructions": "Select a credential category",
                        "criteria": {"password": "Check Bearer secret-token", "token": secret}},
            "access_token": {"type": "score", "instructions": "Assess handling",
                             "criteria": ["Absent", "Includes " + secret]},
        }
        self.configure({"scope": policy["scope"]})
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": secret}):
            result = self.api.call("work_assess", {"proposal": "Check credential handling"})
            cached = self.api.call("work_assess", {"proposal": "Check credential handling"})
        state, questions, model = self.requests[-1]
        self.assertEqual({"password", "api_key", "access_token"}, questions.keys())
        self.assertEqual("noul", questions["password"]["type"])
        self.assertEqual({"password", "token"}, questions["api_key"]["criteria"].keys())
        self.assertEqual("[REDACTED]", questions["api_key"]["criteria"]["token"])
        checked_response(answer(state, questions, model), questions, model)
        saved = self.retained(result)
        self.assertEqual(questions, saved["request"]["questions"])
        self.assertEqual(questions, saved["policy"]["scope"]["questions"])
        self.assertNotIn(secret, json.dumps(saved))
        self.assertNotIn("secret-token", json.dumps(saved))
        self.assertEqual(result["evidence"], cached["evidence"])
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(1, self.provider.call_count)

    def test_feedback_links_decision_outcome_and_retries_without_changing_assignments(self):
        result = self.api.call("work_assess", {"proposal": "Add logging"})
        before = self.api.control.snapshot()
        args = {"kind": "recommendation", "text": "Keep logging as a separate proposal.",
                "evaluation": {"reference": result["evidence"], "stage": "decision", "label": "deferred"}}
        decision = self.xo.call("work_report", args)
        self.assertTrue(self.xo.call("work_report", args)["reused"])
        outcome = self.xo.call("work_report", {"kind": "result", "text": "JSON service complete; no extra logging.",
            "evaluation": {"reference": result["evidence"], "stage": "outcome", "decision": decision["reference"], "label": "useful"}})
        read = self.xo.call("work_context", {"reference": outcome["reference"]})
        self.assertEqual(decision["reference"], read["evaluation"]["decision"])
        self.assertEqual(before, self.api.control.snapshot())
        self.assertEqual(2, len(self.store.events(kind="jev_feedback")["events"]))
        with self.assertRaisesRegex(ValueError, "Command roles"):
            self.api.call("work_report", args)

    def test_feedback_rejects_stale_unregistered_and_mismatched_evaluations(self):
        first = self.api.call("work_assess", {"proposal": "Add logging"})
        args = {"text": "Decision", "evaluation": {"reference": first["evidence"], "stage": "decision"}}
        decision = self.xo.call("work_report", args)
        second = self.api.call("work_assess", {"proposal": "Add caching"})
        with self.assertRaisesRegex(ValueError, "same evaluation"):
            self.xo.call("work_report", {"text": "Outcome", "evaluation": {
                "reference": second["evidence"], "stage": "outcome", "decision": decision["reference"]}})
        self.xo.call("steer", {"action": "guide", "work": "Feature", "objective": "Different requirement"})
        with self.assertRaisesRegex(ValueError, "changed assignment"):
            self.xo.call("work_report", args | {"text": "New decision based on stale evidence"})
        fake = self.api.artifacts.save("fake.json", content=json.dumps(self.retained(first)))
        with self.assertRaisesRegex(ValueError, "recorded Jev"):
            self.xo.call("work_report", {"text": "Fake", "evaluation": {
                "reference": f"artifact:{fake['artifact']['id']}", "stage": "decision"}})

    def test_existing_database_upgrade_preserves_reports_and_retry_identity(self):
        args = {"text": "Existing report"}
        report = self.api.call("work_report", args)
        with self.store.connect() as db:
            db.execute("ALTER TABLE work_updates DROP COLUMN evaluation")
            db.execute("PRAGMA user_version=9")
        upgraded = Store(self.state)
        with upgraded.connect() as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
        retry = API(self.state, "coder").call("work_report", args)
        self.assertEqual(report["reference"], retry["reference"])
        self.assertTrue(retry["reused"])

    def test_effective_configuration_is_inspectable_only_through_operator_surface(self):
        operator = API(self.state, "co", operator=True)
        # Inspecting references is independent of key availability. The normal
        # call boundary separately reads keys to redact its retained telemetry.
        with patch("devtools.credentials.read_credential", side_effect=AssertionError("Inspection must not load a key")):
            inspected = operator.invoke("config_status", {"role": "coder"})
        secret = "fixture-configuration-inspection-secret"
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": secret}):
            result = operator.call("capability_invoke", {"capability": "config_status", "arguments": {"role": "coder"}})
        self.assertEqual(inspected, result)
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(result["features"]["advice"])
        self.assertEqual({"role": "coder", "job": None}, result["selection"])
        self.assertEqual({"env": "TYPESAFE_API_KEY"}, result["connection"]["credential"])
        with self.assertRaisesRegex(ValueError, "not available"):
            self.api.call("config_status", {})

    def test_service_assessments_are_independent_of_agent_advice_tools(self):
        from devtools.advice import Advice
        from devtools.role_packages import catalog

        self.configure({"features": {"advice": False, "filtering": True}})
        packages = copy.deepcopy(catalog())
        packages["roles"]["coder"]["mcp"]["advice"] = []
        with patch("devtools.role_packages._catalog", return_value=packages):
            self.assertNotIn("work_assess", {row["name"] for row in self.api.tools()})
            with self.assertRaisesRegex(ValueError, "not available"):
                self.assess()
            result = Advice(self.store, "coder").evaluate("context_relevance", {"facts": ["Background"]},
                {"relevant": {"type": "noul", "instructions": "Is the fact relevant?"}}, [])
            self.assertIn("relevant", result["answers"])

    def test_invalid_operator_policy_fails_before_provider(self):
        policy = load_policy()
        policy["transport"]["timeout"] = 100
        self.configure({"transport": policy["transport"]})
        with self.assertRaisesRegex(ValueError, "transport"):
            self.api.call("work_assess", {"proposal": "Add logging"})
        self.assertFalse(self.requests)


class AdviceReviewTests(unittest.TestCase):
    setUp_base = WorkspaceFixture.setUp
    prepared = WorkspaceFixture.prepared
    setUp = DeliveryFixture.setUp
    auditor = DeliveryFixture.auditor
    configure = AdviceFixture.configure

    def test_auditor_assesses_submitted_snapshot_not_producers_unsubmitted_source(self):
        self.configure({"features": {"advice": False}})
        audit = self.auditor()
        self.configure({})
        (self.workspace / "src/Example.php").write_text("Unsubmitted changes\n")
        with patch("devtools.advice.Jev.evaluate", side_effect=answer) as provider:
            result = audit.call("work_assess", {
                "sources": [{"path": "src/Example.php"}],
                "questions": [{"name": "review", "instructions": "Is the delivered implementation relevant?"}],
            })
        source = provider.call_args.args[0]["sources"][0]
        self.assertEqual("Delivered implementation\n", source["content"])
        self.assertEqual(self.head, result["results"][0]["source"]["revision"])
        self.assertNotIn("work_save", {row["name"] for row in audit.tools()})
