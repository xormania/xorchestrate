import copy
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools import configuration
from devtools.store import State


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = State(self.root, read_only=False)
        with self.store.connect() as db:
            db.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
            db.executemany("INSERT INTO jobs VALUES (?)", [(1,), (2,)])

    def configure(self, value, name="operator.json"):
        (self.root / "team.json").write_text(json.dumps({"project": {"configuration_file": name}}))
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_independent_features_and_project_role_job_precedence(self):
        self.configure({"version": 1,
                        "project": {"features": {"advice": True}, "filtering": {"max_facts": 3}},
                        "roles": {"research": {"features": {"filtering": False}, "filtering": {"max_facts": 4}}},
                        "jobs": {"1": {"filtering": {"max_facts": 7}}}})
        general = configuration.resolve(self.store)
        researcher = configuration.resolve(self.store, "research")
        job = configuration.resolve(self.store, "research", 1)
        self.assertEqual(3, general["filtering"]["max_facts"])
        self.assertEqual(4, researcher["filtering"]["max_facts"])
        self.assertEqual(7, job["filtering"]["max_facts"])
        self.assertEqual({"selection": True, "advice": True, "filtering": False, "guidance": True, "completion": True}, job["features"])
        self.assertEqual({"role": "research", "job": 1}, job["selection"])
        self.assertNotEqual(researcher["revision"], job["revision"])
        self.assertEqual(job, configuration.resolve(self.store, "research", 1))

    def test_absent_config_enables_automatic_decisions_and_guidance(self):
        value = configuration.resolve(self.store)
        self.assertEqual({"selection": True, "advice": False, "filtering": True, "guidance": True, "completion": True}, value["features"])
        self.assertNotIn("revision", configuration.defaults())
        value["scope"]["questions"].clear()
        self.assertTrue(configuration.resolve(self.store)["scope"]["questions"])

    def test_readiness_deadline_uses_existing_project_role_job_overlays(self):
        self.assertEqual(30, configuration.defaults()["readiness"]["timeout_seconds"])
        self.configure({"version": 1,
                        "project": {"readiness": {"timeout_seconds": 45}},
                        "roles": {"research": {"readiness": {"timeout_seconds": 60}}},
                        "jobs": {"1": {"readiness": {"timeout_seconds": 90}}}})
        self.assertEqual(45, configuration.resolve(self.store)["readiness"]["timeout_seconds"])
        self.assertEqual(60, configuration.resolve(self.store, "research")["readiness"]["timeout_seconds"])
        self.assertEqual(90, configuration.resolve(self.store, "research", 1)["readiness"]["timeout_seconds"])
        for invalid in (0, 301, True, 1.5, "30"):
            with self.subTest(timeout=invalid):
                value = configuration.defaults()
                value["readiness"]["timeout_seconds"] = invalid
                with self.assertRaisesRegex(ValueError, "readiness.timeout_seconds"):
                    configuration.validate(value)

    def test_fixture_configuration_state_resolves_without_writing_or_migrating(self):
        # The separately versioned fixture imports this public entry point in
        # both preparation and its frozen final verifier.
        from devtools.configuration import ConfigurationState

        self.configure({"version": 1,
                        "project": {"connection": {"credential": {"file": "private/jev.key"}}},
                        "roles": {"coder": {"filtering": {"max_facts": 4}}},
                        "jobs": {"1": {"filtering": {"max_facts": 7}}}})
        before = self.store.path.read_bytes()
        state = ConfigurationState(self.root)
        value = configuration.resolve(state, role="coder", job=1)
        self.assertEqual(7, value["filtering"]["max_facts"])
        self.assertEqual({"file": str(self.root / "private/jev.key")}, value["connection"]["credential"])
        self.assertFalse((self.root / "private/jev.key").exists())
        with state.connect() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("CREATE TABLE forbidden (id INTEGER)")
        self.assertEqual(before, self.store.path.read_bytes())
        missing = ConfigurationState(self.root / "missing")
        with self.assertRaises(sqlite3.OperationalError), missing.connect():
            self.fail("Missing state must not be created")
        self.assertFalse(missing.directory.exists())

    def test_decision_rules_resolve_partial_overrides_without_changing_defaults(self):
        self.configure({"version": 1, "project": {"decisions": {
            "choice": {"minimum_probability": 0.7},
            "rules": {"runtime_fit": {"choice": {"minimum_margin": 0.2}}},
        }}, "roles": {"research": {"decisions": {
            "rules": {"runtime_fit": {"choice": {"minimum_probability": 0.8}}},
        }}}, "jobs": {"1": {"decisions": {
            "rules": {"runtime_fit": {"choice": {"minimum_probability": 0.9}}},
        }}}})
        settings = configuration.resolve(self.store, "research", 1)
        self.assertEqual({"minimum_probability": 0.9, "minimum_margin": 0.2},
                         configuration.decision_rule(settings, "runtime_fit")["choice"])
        self.assertEqual({"minimum_probability": 0.7, "minimum_margin": 0.15},
                         configuration.decision_rule(settings, "scope")["choice"])
        self.assertEqual({"reject_below": 0.65, "accept_at": 0.85},
                         configuration.decision_rule(settings, "completion")["noul"])
        self.assertEqual({"reject_below": 0.15, "accept_at": 0.35},
                         configuration.decision_rule(settings, "context_relevance")["noul"])
        changed = configuration.decision_rule(settings, "scope")
        changed["choice"]["minimum_probability"] = 0
        self.assertEqual(0.7, configuration.decision_rule(settings, "scope")["choice"]["minimum_probability"])
        self.assertEqual(0.65, configuration.defaults()["decisions"]["choice"]["minimum_probability"])

    def test_decision_invalid_band_in_inactive_combination_is_rejected(self):
        self.configure({"version": 1, "roles": {"research": {"decisions": {
            "rules": {"scope": {"noul": {"reject_below": 0.5}}},
        }}}, "jobs": {"2": {"decisions": {
            "rules": {"scope": {"noul": {"accept_at": 0.4}}},
        }}}})
        with self.assertRaisesRegex(ValueError, "reject_below must be less"):
            configuration.resolve(self.store, "coder", 1)

    def test_decision_schema_rejects_invalid_thresholds_and_unqualified_arbitrators(self):
        for changes in (
            {"arbitrator_role": "research"}, {"arbitrator_role": "unregistered"},
            {"reserved_sessions": 0}, {"reserved_sessions": 11}, {"reserved_sessions": True},
            {"noul": {"reject_below": 0.65}}, {"noul": {"accept_at": 0.3}},
            {"choice": {"minimum_probability": True}}, {"choice": {"minimum_margin": -0.1}},
            {"score": {"minimum_confidence": 1.1}}, {"score": {"minimum_margin": float("inf")}},
            {"unknown_choices": ["unknown", "unknown"]}, {"failure_mode": "continue"},
            {"rules": {"unknown-field": {}}}, {"rules": {"scope": {"invented": {}}}},
            {"rules": {"scope": {"score": {"probability": 0.2}}}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                configuration.validate(configuration._merge(configuration.defaults(), {"decisions": changes}))
        for purpose in (None, True, "", "../scope"):
            with self.subTest(purpose=purpose), self.assertRaises(ValueError):
                configuration.decision_rule(configuration.defaults(), purpose)

    def test_legacy_gate_thresholds_cannot_silently_override_decision_rules(self):
        for old_setting in ({"completion": {"minimum_probability": 0.9}},
                            {"completion": {"failure_mode": "mechanical_only"}},
                            {"filtering": {"threshold": 0.2}}):
            self.configure({"version": 1, "project": old_setting})
            with self.subTest(old_setting=old_setting), self.assertRaisesRegex(ValueError, "Unknown configuration field"):
                configuration.resolve(self.store)

    def test_lists_replace_and_credential_switch_does_not_retain_environment(self):
        self.configure({"version": 1, "project": {
            "guidance": {"routes": [], "actions": []},
            "connection": {"credential": {"file": "private/jev.key"}},
        }}, "nested/settings.json")
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret-never-load"}):
            value = configuration.resolve(self.store)
        self.assertEqual([], value["guidance"]["routes"])
        self.assertEqual({"file": str(self.root / "nested/private/jev.key")}, value["connection"]["credential"])
        self.assertNotIn("secret-never-load", json.dumps(value))
        self.assertFalse((self.root / "nested/private/jev.key").exists())

    def test_key_symlink_is_preserved_for_credential_loader_rejection(self):
        from devtools.credentials import read_credential

        key = self.root / "real.key"
        key.write_text("private-key")
        key.chmod(0o600)
        (self.root / "linked.key").symlink_to(key)
        self.configure({"version": 1, "project": {"connection": {"credential": {"file": "linked.key"}}}})
        reference = configuration.resolve(self.store)["connection"]["credential"]
        self.assertEqual(str(self.root / "linked.key"), reference["file"])
        with self.assertRaises(ValueError):
            read_credential(reference)

    def test_evaluation_identifiers_cannot_bypass_exact_credential_redaction(self):
        from devtools.jev_config import clean_questions

        for questions in ({"secret_key": {"type": "noul", "instructions": "Inspect fact"}},
                          {"question": {"type": "choice", "instructions": "Inspect fact", "criteria": {"secret_key": "value"}}}):
            with self.subTest(questions=questions), self.assertRaisesRegex(ValueError, "evaluation identifiers"):
                clean_questions(questions, secrets=("secret_key",))

    def test_all_overrides_validate_even_when_unselected(self):
        for changes in (
            {"roles": {"research": {"decisions": {"noul": {"accept_at": 2}}}}},
            {"roles": {"unregistered": {}}},
            {"jobs": {"9": {}}},
            {"jobs": {"01": {}}},
            {"jobs": {"2": {"features": {"advice": "yes"}}}},
            {"project": {"guidance": {"fallback": {"skill": "missing-skill"}}}},
            {"project": {"filtering": {"protected_pattern": "["}}},
            {"project": {"transport": {"unknown": 1}}},
            {"project": {"assignment": {"roles": ["co"]}}},
            {"project": {"assignment": {"roles": ["xo"]}}},
            {"project": {"connection": {"credential": {"env": "KEY", "file": "key"}}}},
            {"extra": {}},
        ):
            with self.subTest(changes=changes):
                self.configure({"version": 1, **changes})
                with self.assertRaises(ValueError):
                    configuration.resolve(self.store)

    def test_cross_layer_invalid_combination_is_rejected_before_selection(self):
        self.configure({"version": 1,
                        "roles": {"research": {"assessment": {"source_bytes": 7000}}},
                        "jobs": {"2": {"assessment": {"max_source_bytes": 3000}}}})
        with self.assertRaisesRegex(ValueError, "source_bytes"):
            configuration.resolve(self.store)

    def test_legacy_entry_and_duplicate_fields_are_explicit_errors(self):
        path = self.configure({"version": 1})
        (self.root / "team.json").write_text(json.dumps({"project": {"jev": {}, "configuration_file": path.name}}))
        with self.assertRaisesRegex(ValueError, "Legacy project.jev"):
            configuration.resolve(self.store)
        self.configure({"version": 1})
        path.write_text('{"version":1,"project":{"model":"jev-1.13.0","model":"jev-1.14.0"}}')
        with self.assertRaisesRegex(ValueError, "Duplicate configuration field"):
            configuration.resolve(self.store)

    def test_untrusted_job_selector_types_and_unknown_binding_selectors_fail(self):
        for selector in (True, "1", 0, -1, 9):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                configuration.resolve(self.store, "research", selector)
        with self.assertRaisesRegex(ValueError, "Unknown configuration role"):
            configuration.resolve(self.store, "unregistered")

    def test_revision_changes_on_effective_policy_not_unrelated_overrides(self):
        path = self.configure({"version": 1, "roles": {"research": {"filtering": {"max_facts": 4}}}})
        before = configuration.resolve(self.store, "coder")
        value = json.loads(path.read_text())
        value["roles"]["research"]["filtering"]["max_facts"] = 5
        path.write_text(json.dumps(value))
        self.assertEqual(before["revision"], configuration.resolve(self.store, "coder")["revision"])
        value["project"] = {"features": {"completion": False}}
        path.write_text(json.dumps(value))
        self.assertNotEqual(before["revision"], configuration.resolve(self.store, "coder")["revision"])

    def test_endpoint_and_secret_references_are_validated_without_echoing_values(self):
        default = configuration.defaults()["connection"]
        for endpoint in ("http://api.typesafe.ai/v1/systemone", "https://secret@example.test", "https://host/path?token=secret", "https://host/path#secret", "https://host:invalid"):
            value = copy.deepcopy(default)
            value["endpoint"] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError) as caught:
                configuration.validate_connection(value)
            self.assertNotIn("secret", str(caught.exception))
        for credential in ({"key": "secret-value"}, {"env": "BAD NAME"}, {"file": ""}, {}):
            value = copy.deepcopy(default)
            value["credential"] = credential
            with self.subTest(credential=credential), self.assertRaises(ValueError):
                configuration.validate_connection(value)

    def test_changed_source_invalidates_export_even_after_cache_warmup(self):
        configuration.defaults()
        source = self.root / "configuration.cue"
        generated = source.with_suffix(".json")
        source.write_text(configuration.SOURCE.read_text() + "\n// changed\n")
        generated.write_text(configuration.GENERATED.read_text())
        with patch.object(configuration, "SOURCE", source), patch.object(configuration, "GENERATED", generated):
            with self.assertRaisesRegex(ValueError, "stale"):
                configuration.defaults()

    def test_wrong_value_types_raise_configuration_errors(self):
        for path, malformed in ((["guidance", "failure_mode"], []), (["decisions", "noul", "accept_at"], True),
                                (["model"], None), (["features", "advice"], 1), (["transport", "retry_statuses"], [True])):
            value = configuration.defaults()
            target = value
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = malformed
            with self.subTest(path=path), self.assertRaises(ValueError):
                configuration.validate(value)

    def test_profile_evidence_closes_over_fixed_members_even_in_inactive_override(self):
        path = self.configure({"version": 1, "roles": {"research": {"assignment": {"profile_evidence": {"helper": "Measured fit"}}}}})
        team = json.loads((self.root / "team.json").read_text())
        team["members"] = [{"name": "helper"}]
        (self.root / "team.json").write_text(json.dumps(team))
        configuration.resolve(self.store)
        value = json.loads(path.read_text())
        value["roles"]["research"]["assignment"]["profile_evidence"] = {"typo": "Measured fit"}
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "unknown configured runtime profiles"):
            configuration.resolve(self.store)

    def test_profile_evidence_closes_over_declared_unavailable_runtime_profiles(self):
        self.configure({"version": 1, "jobs": {"1": {"assignment": {"profile_evidence": {"offline": "Measured fit"}}}}})
        team = json.loads((self.root / "team.json").read_text())
        team["runtime"] = True
        (self.root / "team.json").write_text(json.dumps(team))
        (self.root / "roles.json").write_text(json.dumps({"version": 1, "max_sessions": 2,
            "profiles": {"offline": {"harness": "codex", "vendor": "vendor"}}, "roles": {"research": ["offline"]}}))
        (self.root / "vendors.json").write_text(json.dumps({"version": 1, "vendors": {"vendor": {"available": False}}}))
        self.assertEqual({"offline": "Measured fit"}, configuration.resolve(self.store, job=1)["assignment"]["profile_evidence"])
        (self.root / "roles.json").write_text(json.dumps({"version": 1, "max_sessions": 2,
            "profiles": {"replacement": {"harness": "codex", "vendor": "vendor"}}, "roles": {"research": ["replacement"]}}))
        with self.assertRaisesRegex(ValueError, "unknown configured runtime profiles"):
            configuration.resolve(self.store)

    def test_role_cardinality_and_names_follow_registry_and_semantic_defaults(self):
        registered = configuration.catalog()
        coder = registered["roles"]["coder"]
        arbitrator = registered["roles"]["arbitrator"]
        registered["roles"] = {"role" + str(index): copy.deepcopy(coder) for index in range(40)}
        registered["roles"]["decision_specialist"] = arbitrator
        self.configure({"version": 1, "project": {"decisions": {"arbitrator_role": "decision_specialist"}},
                        "roles": {"role39": {"features": {"advice": True}}}})
        with patch.object(configuration, "catalog", return_value=registered):
            value = configuration.resolve(self.store, "role39")
        self.assertTrue(value["features"]["advice"])
        self.assertEqual([], value["guidance"]["excluded_roles"])
        self.assertEqual(["co"], value["guidance"]["excluded_adapters"])
        self.assertEqual("decision_specialist", value["decisions"]["arbitrator_role"])

    def test_explicit_overlay_can_repair_removed_default_skill_references(self):
        registered = configuration.catalog()
        for package in registered["roles"].values():
            package["skills"]["framework"] = []
        with patch.object(configuration, "catalog", return_value=registered):
            configuration.defaults()  # Defaults are data; closure applies after the operator overlay.
            with self.assertRaisesRegex(ValueError, "unknown installed role skill"):
                configuration.resolve(self.store)
            self.configure({"version": 1, "project": {"guidance": {"routes": []}}})
            self.assertEqual([], configuration.resolve(self.store)["guidance"]["routes"])

    def test_recovery_templates_close_over_managed_tools_schemas_and_placeholders(self):
        action = {"categories": ["not_ready"], "reasons": ["check_failed"], "tool": "work_check",
                  "arguments": {"check": "$check"}, "when": "After saving the corrected source."}
        for changed in (
            {"tool": "invented_tool"}, {"tool": "capability_invoke"},
            {"arguments": {"check": "$invented"}}, {"arguments": {"check": 42}},
            {"arguments": {"command": "unsafe"}}, {"categories": ["invented_category"]},
        ):
            with self.subTest(changed=changed):
                self.configure({"version": 1, "roles": {"research": {
                    "guidance": {"recovery_actions": [{**action, **changed}]}}}})
                with self.assertRaises(ValueError):
                    configuration.resolve(self.store, "coder")
        self.configure({"version": 1, "project": {"guidance": {"recovery_actions": [action]}}})
        self.assertEqual([action], configuration.resolve(self.store)["guidance"]["recovery_actions"])

    def test_recovery_templates_use_effective_advice_schema(self):
        action = {"categories": ["invalid_request"], "reasons": [], "tool": "work_assess",
                  "arguments": {"sources": [{"path": "src/Example.php"}] * 16,
                                "questions": [{"name": "scope", "instructions": "Inspect these sources."}]},
                  "when": "After selecting sources within the current assignment."}
        self.configure({"version": 1, "project": {
            "assessment": {"max_sources": 16, "max_questions": 2},
            "guidance": {"recovery_actions": [action]}}})
        self.assertEqual([action], configuration.resolve(self.store)["guidance"]["recovery_actions"])

    def test_adapter_registration_changes_revision_and_can_be_selected_without_loading(self):
        from devtools import adapters

        before = configuration.resolve(self.store)
        document = json.loads(adapters.MANIFEST.read_text())
        document["adapters"]["evaluation"]["alternate"] = "devtools.jev:TypeSafeAdapter"
        manifest = self.root / "adapters.json"
        manifest.write_text(json.dumps(document))
        with patch.object(adapters, "MANIFEST", manifest):
            after = configuration.resolve(self.store)
            self.assertNotEqual(before["revision"], after["revision"])
            self.configure({"version": 1, "project": {"connection": {"adapter": "alternate"}}})
            self.assertEqual("alternate", configuration.resolve(self.store)["connection"]["adapter"])

    def test_inspection_is_read_only_and_enumerates_all_override_combinations(self):
        self.configure({"version": 1, "roles": {"research": {}, "coder": {}}, "jobs": {"1": {}, "2": {}}})
        state = State(self.root)
        self.assertEqual(9, len(configuration.selections(state)))
        before = (self.root / "live.sqlite3").read_bytes()
        self.assertEqual(1, configuration.resolve(state, "research", 1)["selection"]["job"])
        self.assertEqual(before, (self.root / "live.sqlite3").read_bytes())

    def test_cue_export_matches_and_rejects_stale_unknown_fields(self):
        cue = os.environ.get("AGENTSCIENT_CUE") or shutil.which("cue")
        if not cue:
            self.skipTest("CUE is a maintainer dependency")
        self.assertEqual(json.loads(configuration.GENERATED.read_text()), configuration.generate(cue))
        source = self.root / "invalid.cue"
        for path, changed in ((None, None), (["features", "legacy"], True),
                              (["connection", "credential"], {"env": "KEY", "file": "key"}),
                              (["transport", "max_attempts"], 0),
                              (["decisions", "noul"], {"reject_below": 0.9, "accept_at": 0.2}),
                              (["decisions", "choice", "minimum_probability"], 2),
                              (["decisions", "rules", "scope"], {"score": {"invented": 1}})):
            value = configuration.defaults()
            if path is not None:
                target = value
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = changed
            source.write_text(configuration.SOURCE.read_text() + "\nbad: #Settings & " + json.dumps(value) + "\n")
            checked = subprocess.run([cue, "vet", "-c", str(source)], capture_output=True, timeout=30)
            with self.subTest(path=path):
                self.assertEqual(path is None, checked.returncode == 0, checked.stderr.decode())
