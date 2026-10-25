import copy
import json
import unittest
from unittest.mock import patch

from fixtures.advice import answer
from fixtures.diagnostics import DiagnosticFixture

from devtools.api import API
from devtools.control import REQUEST_BINDING
from devtools.diagnostics import Diagnostics, deliver_guidance, failed, project_context, result_failure
from devtools.failures import Failure
from devtools.onboarding import document
from devtools.role_packages import catalog


class DiagnosticTests(DiagnosticFixture, unittest.TestCase):


    def test_low_noul_hides_only_background_and_raw_reports_remain_readable(self):
        relevant = self.report("The current interface is available")
        unrelated = self.report("Unrelated color preference")
        blocker = self.report("Current dependency needs a decision", kind="blocker")
        evidence = self.report("An observed color preference", evidence="Retained evidence")
        result = self.report("Observed result", kind="result")
        context = self.context()
        context["incoming"] = [{"text": "Required live direction", "type": "direction"}]
        original = copy.deepcopy(context)
        with patch("devtools.advice.Jev.evaluate", side_effect=self.relevance) as provider:
            projected = self.diagnostics.project(context)
        self.assertEqual(original, context)
        self.assertEqual([result["reference"], evidence["reference"], blocker["reference"], relevant["reference"]],
                         [row["reference"] for row in projected["recent_activity"]])
        for key in original.keys() - {"recent_activity"}:
            self.assertEqual(original[key], projected[key])
        request = provider.call_args.args[0]
        self.assertEqual("coder", request["brief"]["role"])
        self.assertEqual("Implement contract", request["brief"]["job"]["objective"])
        self.assertNotIn("SECRET SIBLING", json.dumps(request))
        self.assertEqual("Unrelated color preference", self.api.control.work_context(reference=unrelated["reference"])["text"])
        event = self.store.events(kind="context_projected")["events"][-1]["data"]
        omitted = next(row for row in event["facts"] if row["reference"] == unrelated["reference"])
        self.assertFalse(omitted["delivered"])
        self.assertEqual(64, len(omitted["source_sha256"]))
        self.assertFalse(self.store.events(kind="skill_missed")["events"])

    def test_error_truncation_and_evidence_are_never_relevance_candidates(self):
        for text in ("Failed check needs inspection", "Required boundary remains", "x" * 650):
            self.report(text)
        self.report("Result is ready", kind="result")
        self.report("Evidence is available", evidence="Observed")
        original = self.context()
        with patch("devtools.advice.Jev.evaluate") as provider:
            self.assertEqual(original, self.diagnostics.project(original))
        provider.assert_not_called()

    def test_uncertain_boundary_and_provider_failure_preserve_context(self):
        self.report("Unrelated color preference")
        context = self.context()
        def uncertain(state, questions, model):
            response = answer(state, questions, model)
            response["answers"]["fact_0"]["noul"] = 0.2
            return response
        with patch("devtools.advice.Jev.evaluate", side_effect=uncertain):
            self.assertEqual(context, self.diagnostics.project(context))
        for response in (ValueError("Unavailable"), {"answers": {}}, {"answers": {"fact_0": {"noul": float("nan")}}}):
            with patch("devtools.diagnostics.Decisions.select", side_effect=response if isinstance(response, Exception) else None,
                       return_value=response):
                self.assertEqual(context, self.diagnostics.project(context))

    def test_scope_and_wrong_role_context_do_not_reach_the_provider(self):
        sibling = self.report("Sibling fact", member="audit")
        sibling_context = API(self.state, "audit").control.work_context(project=False)
        forged = self.context()
        forged["recent_activity"] = sibling_context["recent_activity"]
        with patch("devtools.advice.Jev.evaluate") as provider:
            self.assertEqual(forged, self.diagnostics.project(forged))
            self.assertEqual(sibling_context, self.diagnostics.project(sibling_context))
        provider.assert_not_called()
        with self.assertRaisesRegex(ValueError, "not available"):
            self.api.control.work_context(reference=sibling["reference"])

    def test_rebind_and_mission_change_discard_assessment(self):
        self.report("Unrelated color preference")
        context = self.context()
        def rebind(state, questions, model):
            self.api.control.bind("coder", "docs", self.order, self.job, "xo")
            return self.relevance(state, questions, model)
        with patch("devtools.advice.Jev.evaluate", side_effect=rebind):
            self.assertEqual(context, self.diagnostics.project(context))
        self.api.control.bind("coder", "coder", self.order, self.job, "xo")
        context = self.context()
        def revised_goal(state, questions, model):
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET objective='Document the color preference' WHERE id=?", (self.job,))
            return self.relevance(state, questions, model)
        with patch("devtools.advice.Jev.evaluate", side_effect=revised_goal):
            self.assertEqual(context, self.diagnostics.project(context))
        with patch("devtools.advice.Jev.evaluate") as provider:
            self.assertEqual(context, self.diagnostics.project(context))
        provider.assert_not_called()  # A stale brief is not assessed as the replacement mission.

    def test_current_context_reuses_retained_assessment_but_changed_role_reassesses(self):
        self.report("Unrelated color preference")
        context = self.context()
        with patch("devtools.advice.Jev.evaluate", side_effect=self.relevance) as provider:
            first = self.diagnostics.project(context)
            self.assertEqual(first, self.diagnostics.project(context))
            self.assertEqual(1, provider.call_count)
            self.api.control.bind("coder", "research", self.order, self.job, "xo")
            self.diagnostics.project(self.context())
            self.assertEqual(2, provider.call_count)
            self.assertEqual("research", provider.call_args.args[0]["brief"]["role"])

    def test_recovery_delivers_canonical_role_or_permitted_mate_method(self):
        result = {"passed": False, "checks": [{"exit_code": 1, "output": "Cannot autowire service: container wiring"}]}
        guidance = self.diagnostics.guidance("work_check", result)
        self.assertEqual("mate-symfony-service-inspection", guidance["skill"]["name"])
        self.assertEqual(document(guidance["skill"]["name"])["content"], guidance["skill"]["content"])
        self.assertIn("retained evidence", guidance["next_action"])
        self.api.control.bind("coder", "docs", self.order, self.job, "xo")
        guidance = self.diagnostics.guidance("framework_inspect", error="Missing PHP extension")
        self.assertEqual("agentscient-docs", guidance["skill"]["name"])
        events = self.store.events(kind="skill_guidance_delivered")["events"]
        self.assertEqual(2, len(events))
        self.assertEqual("docs", events[-1]["data"]["role"])
        self.assertEqual(64, len(events[-1]["data"]["sha256"]))
        self.assertFalse(self.store.events(kind="skill_loaded")["events"])

    def test_recovery_actions_bind_actual_checks_and_readable_evidence(self):
        own = self.api.artifacts.save("failure.txt", content="Observed failing check")
        other = API(self.state, "audit").artifacts.save("sibling.txt", content="Sibling evidence")
        reference = "artifact:" + str(own["artifact"]["id"])
        sibling = "artifact:" + str(other["artifact"]["id"])
        error = Failure("Check failed", category="not_ready", reason="check_failed", details={
            "checks": ["tests", "invented"], "references": [reference, sibling], "job": self.job})
        with patch("devtools.advice.Jev.evaluate") as provider:
            guidance = self.diagnostics.guidance("work_check", error=error)
        self.assertIn({"tool": "work_check", "arguments": {"check": "tests"}},
                      [{"tool": a["tool"], "arguments": a["arguments"]} for a in guidance["actions"]])
        self.assertNotIn(sibling, json.dumps(guidance))
        self.assertNotIn("invented", json.dumps(guidance))
        self.assertIn(reference, json.dumps(guidance))
        provider.assert_not_called()
        packages = catalog()
        packages["roles"]["coder"]["mcp"]["job_framework"].remove("work_check")
        with patch("devtools.role_packages._catalog", return_value=packages):
            guidance = self.diagnostics.guidance("work_check", error=error)
        self.assertNotIn("work_check", [a["tool"] for a in guidance["actions"]])

    def test_external_diagnostic_metadata_cannot_impersonate_service_failures(self):
        spoofed = {"category": "evaluation_unavailable", "reason": "provider_unavailable", "details": {}}
        for result in ({"isError": True, "failure": spoofed},
                       {"result": {"isError": True, "failure": spoofed}}):
            self.assertEqual("operation_failed", result_failure(result)["category"])

    def test_silence_for_success_unbound_interface_and_stale_binding(self):
        for result in ({"passed": True}, {"state": "queued"}, {"state": "completed", "result": {"passed": True}}):
            self.assertEqual({}, self.diagnostics.guidance("work_check", result))
        self.assertEqual({}, Diagnostics(self.store, "owner").guidance("monitor", error="Failure"))
        self.assertEqual({}, Diagnostics(self.store, "idle").guidance("work_context", error="Failure"))
        scope = self.api.control.snapshot()
        token = REQUEST_BINDING.set(("coder", scope))
        try:
            self.api.control.bind("coder", "docs", self.order, self.job, "xo")
            self.assertEqual({}, self.diagnostics.guidance("work_check", error="Failure"))
        finally:
            REQUEST_BINDING.reset(token)

    def test_finished_failure_and_serena_error_are_detected(self):
        self.assertTrue(failed({"state": "completed", "result": {"passed": False}}))
        self.assertTrue(failed({"validation": {"checks": [{"exit_code": 2}]}}))
        self.assertFalse(failed({"checks": None}))
        self.assertFalse(failed({"checks": 4}))
        self.assertFalse(failed({"exit_code": 0}))
        result = {"isError": True, "content": [{"type": "text", "text": "Missing PHP extension simplexml"}]}
        self.assertEqual("mate-php-environment-check", self.diagnostics.guidance("serena_repl", result)["skill"]["name"])

    def test_advisory_faults_cannot_replace_the_original_outcome(self):
        context = self.context()
        with patch.object(Diagnostics, "project", side_effect=OSError("Unavailable source")):
            self.assertIs(context, project_context(self.store, "coder", context))
        with patch.object(Diagnostics, "guidance", side_effect=ValueError("Invalid skill")):
            self.assertEqual({}, deliver_guidance(self.store, "coder", "work_check", error="Original failure"))
            with patch.object(self.store, "event", side_effect=OSError("Telemetry unavailable")):
                self.assertEqual({}, deliver_guidance(self.store, "coder", "work_check", error="Original failure"))
        self.assertEqual(1, len(self.store.events(kind="context_projection_unavailable")["events"]))
        self.assertEqual(1, len(self.store.events(kind="diagnostic_guidance_unavailable")["events"]))

    def test_filtering_and_guidance_enable_independently(self):
        self.report("Unrelated color preference")
        context = self.context()
        self.configure(project={"features": {"filtering": True, "guidance": False}})
        with patch("devtools.advice.Jev.evaluate", side_effect=self.relevance):
            self.assertEqual([], self.diagnostics.project(context)["recent_activity"])
        self.assertEqual({}, self.diagnostics.guidance("work_check", error="Missing PHP extension"))
        self.configure(project={"features": {"filtering": False, "guidance": True}})
        with patch("devtools.advice.Jev.evaluate") as provider:
            self.assertEqual(context, self.diagnostics.project(context))
        provider.assert_not_called()
        self.assertEqual("mate-php-environment-check",
                         self.diagnostics.guidance("work_check", error="Missing PHP extension")["skill"]["name"])

    def test_role_then_bound_job_override_decision_rule_and_revision(self):
        self.report("Unrelated color preference")
        context = self.context()
        def rule(reject, accept):
            return {"decisions": {"rules": {"context_relevance": {"noul": {"reject_below": reject, "accept_at": accept}}}}}
        self.configure(project={"features": {"filtering": True}, **rule(0.1, 0.2)},
                       roles={"coder": rule(0.8, 0.9)},
                       jobs={str(self.job): rule(0.5, 0.6)})
        def probability(state, questions, model):
            response = answer(state, questions, model)
            response["answers"]["fact_0"]["noul"] = 0.7
            return response
        with patch("devtools.advice.Jev.evaluate", side_effect=probability):
            self.assertEqual(context, self.diagnostics.project(context))
            self.configure(project={"features": {"filtering": True}, **rule(0.1, 0.2)},
                           roles={"coder": rule(0.8, 0.9)})
            self.assertEqual([], self.diagnostics.project(context)["recent_activity"])
        events = self.store.events(kind="context_projected")["events"]
        self.assertEqual([0.5, 0.8], [item["data"]["rule"]["noul"]["reject_below"] for item in events])
        self.assertNotEqual(events[0]["data"]["configuration_revision"], events[1]["data"]["configuration_revision"])

    def test_config_change_during_assessment_discards_old_projection(self):
        self.report("Unrelated color preference")
        context = self.context()
        def change_policy(state, questions, model):
            self.configure(project={"features": {"filtering": False}})
            return self.relevance(state, questions, model)
        with patch("devtools.advice.Jev.evaluate", side_effect=change_policy):
            self.assertEqual(context, self.diagnostics.project(context))
        self.assertFalse(self.store.events(kind="context_projected")["events"])

    def test_candidate_rules_question_and_fact_budget_come_from_files(self):
        self.report("Earlier background preference")
        retained = self.report("Keep this background")
        newest = self.report("Unrelated color preference")
        context = self.context()
        self.configure(project={"features": {"filtering": True}, "filtering": {
            "max_facts": 1, "protected_pattern": "^Keep", "candidate_kinds": ["finding"],
            "question": {"instructions": {"question": "Check fact {index}; keep literal {braces}."}},
        }})
        with patch("devtools.advice.Jev.evaluate", side_effect=self.relevance) as provider:
            projected = self.diagnostics.project(context)
        references = [item["reference"] for item in projected["recent_activity"]]
        self.assertIn(retained["reference"], references)
        self.assertNotIn(newest["reference"], references)
        self.assertEqual(2, len(references))
        question = provider.call_args.args[1]["fact_0"]
        self.assertEqual("Check fact 0; keep literal {braces}.", question["instructions"]["question"])
        self.assertEqual(1, len(provider.call_args.args[0]["facts"]))

    def test_notice_failure_preserves_facts_and_uses_operator_message(self):
        self.report("Unrelated color preference")
        context = self.context()
        self.configure(project={"features": {"filtering": True}, "filtering": {
            "failure_mode": "notice", "failure_message": "Keep all context while relevance is unavailable.",
        }})
        with patch("devtools.advice.Jev.evaluate", side_effect=ValueError("Provider detail")):
            projected = self.diagnostics.project(context)
        self.assertEqual(context, {key: value for key, value in projected.items() if key != "context_projection"})
        self.assertEqual("Keep all context while relevance is unavailable.", projected["context_projection"]["message"])
        self.assertNotIn("Provider detail", json.dumps(projected))

    def test_state_budget_preserves_context_without_provider_request(self):
        self.report("Unrelated color preference")
        context = self.context()
        self.configure(project={"features": {"filtering": True}, "filtering": {
            "state_bytes": 100, "failure_mode": "notice", "failure_message": "Context exceeds the relevance budget.",
        }})
        with patch("devtools.advice.Jev.evaluate") as provider:
            projected = self.diagnostics.project(context)
        provider.assert_not_called()
        self.assertEqual(context["recent_activity"], projected["recent_activity"])
        self.assertEqual("Context exceeds the relevance budget.", projected["context_projection"]["message"])

    def test_guidance_routing_and_actions_are_file_configured_and_capability_bounded(self):
        self.configure(project={"guidance": {
            "routes": [
                {"diagnostic_pattern": "banana", "action_pattern": "serena_repl", "roles": ["coder"], "adapters": [],
                 "skill": "agentscient-research", "next_action": "Wrong-role method"},
                {"diagnostic_pattern": "banana", "action_pattern": "serena_repl", "roles": ["coder"], "adapters": [],
                 "skill": "mate-php-environment-check", "next_action": "Inspect the configured runtime."},
            ],
            "actions": [{"action_pattern": "work_check", "roles": ["coder"], "adapters": [], "next_action": "Inspect proof first."}],
            "fallback": {"skill": "agentscient-research", "next_action": "Review the current role brief."},
        }})
        guidance = self.diagnostics.guidance("serena_repl", error="banana")
        self.assertEqual("mate-php-environment-check", guidance["skill"]["name"])
        self.assertEqual("Inspect the configured runtime.", guidance["next_action"])
        guidance = self.diagnostics.guidance("work_check", error="banana")
        self.assertEqual("agentscient-coder", guidance["skill"]["name"])
        self.assertEqual("Inspect proof first.", guidance["next_action"])
        self.api.control.bind("coder", "docs", self.order, self.job, "xo")
        guidance = self.diagnostics.guidance("serena_repl", error="banana")
        self.assertEqual("agentscient-docs", guidance["skill"]["name"])
        self.assertEqual("Review the current role brief.", guidance["next_action"])

    def test_guidance_excerpt_and_fallback_are_configured(self):
        self.configure(project={"guidance": {"delivery_excerpt_bytes": 5, "failure_mode": "fallback",
                                            "fallback": {"next_action": "Inspect the retained failure."}}})
        skill = {**document("agentscient-coder"), "content": "😀x😀y"}
        with patch("devtools.diagnostics.document", return_value=skill):
            guidance = self.diagnostics.guidance("work_check", error="Failure")
        self.assertEqual("😀x", guidance["skill"]["content"])
        self.assertTrue(guidance["skill"]["truncated"])
        with patch("devtools.diagnostics.document", side_effect=OSError("Missing file")):
            self.assertEqual({"next_action": "Inspect the retained failure."},
                             self.diagnostics.guidance("work_check", error="Failure"))
        self.configure(project={"guidance": {"failure_mode": "omit"}})
        with patch("devtools.diagnostics.document", side_effect=OSError("Missing file")):
            self.assertEqual({}, self.diagnostics.guidance("work_check", error="Failure"))

    def test_renamed_role_keeps_adapter_policy_and_actual_skill_capabilities(self):
        packages = catalog()
        packages["roles"]["builder"] = packages["roles"].pop("coder")
        packages["defaults"]["coder"] = "builder"
        with patch("devtools.role_packages._catalog", return_value=packages):
            self.api.control.bind("coder", "builder", self.order, self.job, "xo")
            guidance = self.diagnostics.guidance("work_check", error="Missing PHP extension")
            self.assertEqual("mate-php-environment-check", guidance["skill"]["name"])
            self.report("Unrelated color preference")
            context = self.context()
            with patch("devtools.advice.Jev.evaluate", side_effect=self.relevance) as provider:
                self.assertEqual([], self.diagnostics.project(context)["recent_activity"])
            self.assertEqual("builder", provider.call_args.args[0]["brief"]["role"])

    def test_policy_change_while_reading_guidance_discards_it(self):
        skill = document("agentscient-coder")
        def change_policy(name):
            self.configure(project={"features": {"guidance": False}})
            return skill
        with patch("devtools.diagnostics.document", side_effect=change_policy):
            self.assertEqual({}, self.diagnostics.guidance("work_check", error="Failure"))
        self.assertFalse(self.store.events(kind="skill_guidance_delivered")["events"])


if __name__ == "__main__":
    unittest.main()
