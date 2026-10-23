import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.control import ControlFixture
from fixtures.runtime import RuntimeFixture
from support import adopt_plan, fixture_environment

from devtools import role_packages
from devtools.api import API, TOOLS
from devtools.launch import configuration
from devtools.onboarding import document
from devtools.profiles import _hook, permitted, tool_catalogue


class PackageCatalogTests(unittest.TestCase):
    def exported(self):
        return json.loads(role_packages.GENERATED.read_text())

    def validate(self, value):
        return role_packages.validate(value, self.exported()["source_sha256"])

    def test_packages_reference_installed_skills_and_registered_mcp_tools(self):
        for name, package in role_packages.catalog()["roles"].items():
            for skill in [package["skills"]["shared"], package["skills"]["role"], *package["skills"]["framework"]]:
                self.assertTrue(document(skill)["content"])
            self.assertEqual(name, role_packages.descriptor(name)["role"])
        role_packages.validate_tools(row["name"] for row in TOOLS)
        with self.assertRaisesRegex(ValueError, "unknown MCP tools"):
            role_packages.validate_tools(["work_context"])

    def test_malformed_or_stale_exports_fail_instead_of_inferring_missing_fields(self):
        for mutate in (
            lambda value: value["catalog"]["roles"]["co"]["hooks"].update(script="/tmp/override"),
            lambda value: value["catalog"]["roles"]["co"]["hooks"].update(native_delegation=True),
            lambda value: value["catalog"]["roles"]["co"]["mcp"]["command"].append("conclude"),
            lambda value: value["catalog"]["roles"]["audit"]["workspace"].update(editor=True, paths=["src/"]),
            lambda value: value["catalog"]["roles"]["arbitrator"]["workspace"].update(editor=True, paths=["src/"]),
            lambda value: value["catalog"]["roles"]["research"]["workspace"].update(paths=["src/"]),
            lambda value: value["catalog"]["roles"]["coder"]["workspace"].update(paths=["../outside"]),
            lambda value: value["catalog"]["roles"]["coder"]["skills"].update(role="missing-installed-skill"),
            lambda value: value["catalog"]["roles"]["coder"]["runtime"].update(harnesses=["unknown"]),
            lambda value: value["catalog"]["roles"]["coder"]["runtime"]["model"].update(allowed=True),
            lambda value: value["catalog"]["roles"]["coder"]["mcp"].pop("bound"),
            lambda value: value.update(source_sha256="stale"),
        ):
            value = self.exported()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(value)
        self.validate(self.exported())

    def test_runtime_options_preserve_operator_choices_and_reject_unchecked_inheritance(self):
        package = role_packages.role("coder")
        original = role_packages.role
        def selected(name):
            return copy.deepcopy(package) if name == "coder" else original(name)
        with patch("devtools.role_packages.role", side_effect=selected):
            chosen = role_packages.runtime_profile("coder", {"harness": "codex", "model": "operator-model", "reasoning": "operator-effort"})
            self.assertEqual("operator-model", chosen["model"])
            self.assertEqual("operator-effort", chosen["reasoning"])
            package["runtime"]["model"] = {"default": None, "allowed": ["allowed-model"]}
            for profile in ({"harness": "codex"}, {"harness": "codex", "model": "other"}):
                with self.assertRaisesRegex(ValueError, "explicit permitted model"):
                    role_packages.runtime_profile("coder", profile)
            package["runtime"]["model"]["default"] = "allowed-model"
            self.assertEqual("allowed-model", role_packages.runtime_profile("coder", {"harness": "codex"})["model"])
            package["runtime"]["harnesses"] = ["claude"]
            with self.assertRaisesRegex(ValueError, "harness"):
                role_packages.runtime_profile("coder", {"harness": "codex"})

    def test_catalog_returns_do_not_mutate_policy(self):
        package = role_packages.role("co")
        package["mcp"]["command"].append("conclude")
        self.assertNotIn("conclude", role_packages.role("co")["mcp"]["command"])

    def test_role_names_and_count_are_configuration_with_explicit_behavior_defaults(self):
        value = self.exported()
        original = value["catalog"]["roles"]
        value["catalog"]["roles"] = {"interface": original["co"], "lead": original["xo"],
                                     "implementer-one": original["coder"], "implementer-two": copy.deepcopy(original["coder"])}
        value["catalog"]["defaults"] = {"co": "interface", "xo": "lead", "coder": "implementer-two"}
        configured = self.validate(value)
        with patch("devtools.role_packages._catalog", return_value=configured):
            self.assertEqual("co", role_packages.adapter("interface"))
            self.assertEqual(("implementer-one", "implementer-two"), role_packages.names_for_adapter("coder"))
            self.assertEqual("implementer-two", role_packages.default_role("coder"))
            self.assertEqual("lead", role_packages.default_role("xo"))
            with self.assertRaisesRegex(ValueError, "Configure a role for the planner behavior"):
                role_packages.default_role("planner")
            del configured["defaults"]["coder"]
            with self.assertRaisesRegex(ValueError, "explicit default"):
                role_packages.default_role("coder")
            del configured["defaults"]["xo"]
            self.assertEqual("lead", role_packages.default_role("xo"))

    def test_registered_behavior_plugin_preserves_semantics_and_permission_ceiling(self):
        value = self.exported()
        registry = role_packages.adapters.registry()
        registry["role"]["review-plugin"] = "devtools.role_behaviors:audit_behavior"
        value["catalog"]["roles"] = {"inspector": value["catalog"]["roles"]["audit"]}
        value["catalog"]["roles"]["inspector"]["behavior"] = "review-plugin"
        value["catalog"]["defaults"] = {"audit": "inspector"}
        with patch("devtools.adapters.registry", return_value=registry):
            configured = self.validate(value)
            with patch("devtools.role_packages._catalog", return_value=configured):
                self.assertEqual("audit", role_packages.adapter("inspector"))
                self.assertEqual(("inspector",), role_packages.names_for_adapter("audit"))
            value["catalog"]["roles"]["inspector"]["workspace"].update(editor=True, paths=["src/"])
            with self.assertRaisesRegex(ValueError, "cannot become job editors"):
                self.validate(value)

    def test_unknown_behavior_and_invalid_default_do_not_infer_legacy_roles(self):
        cases = (
            lambda value: value["catalog"]["roles"]["coder"].update(behavior="unregistered"),
            lambda value: value["catalog"]["defaults"].update(coder="missing"),
            lambda value: value["catalog"]["defaults"].update(coder="co"),
            lambda value: value["catalog"]["roles"]["coder"].pop("behavior"),
        )
        for mutate in cases:
            value = self.exported()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(value)

    def test_changed_catalog_contents_revalidate_instead_of_retaining_a_stale_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "role-packages.json"
            value = self.exported()
            path.write_text(json.dumps(value))
            with patch("devtools.role_packages.GENERATED", path):
                self.assertIn("co", role_packages.catalog()["roles"])
                package = value["catalog"]["roles"]["co"]
                value["catalog"]["roles"] = {"interface": package}
                value["catalog"]["defaults"] = {"co": "interface"}
                path.write_text(json.dumps(value))
                self.assertEqual({"interface"}, set(role_packages.catalog()["roles"]))
                value["source_sha256"] = "stale"
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "stale"):
                    role_packages.catalog()

    def test_cue_source_and_export_agree_and_invalid_definitions_are_rejected(self):
        cue = os.environ.get("AGENTSCIENT_CUE") or shutil.which("cue")
        if not cue:
            self.skipTest("CUE is a maintainer/CI dependency; runtime uses the checked-in export")
        self.assertEqual(self.exported(), role_packages.generate(cue))
        cases = {
            "hook script": 'packages: roles: co: hooks: script: "/tmp/override"',
            "CO acceptance": 'packages: roles: co: mcp: command: ["direct", "monitor", "conclude"]',
            "auditor writes": 'packages: roles: audit: workspace: {editor: true, paths: ["src/"]}',
            "native children": 'packages: roles: co: hooks: native_delegation: true',
            "duplicate harness": 'bad: #Runtime & {harnesses: ["codex", "codex"]}',
            "unknown harness": 'packages: roles: coder: runtime: harnesses: ["unknown"]',
            "outside default": 'bad: #Option & {default: "outside", allowed: ["inside"]}',
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.cue"
            for name, source in cases.items():
                path.write_text(role_packages.SOURCE.read_text() + "\n" + source + "\n")
                result = subprocess.run([cue, "vet", "-c", str(path)], capture_output=True, text=True, timeout=30)
                with self.subTest(name=name):
                    if result.returncode:
                        self.assertTrue(result.stderr)
                    else:
                        # CUE owns structural rules. Installed adapter metadata
                        # supplies behavior/harness ceilings during generation.
                        exported = subprocess.run([cue, "export", str(path), "-e", "packages"],
                                                  capture_output=True, text=True, check=True, timeout=30)
                        invalid = {"source_sha256": self.exported()["source_sha256"], "catalog": json.loads(exported.stdout)}
                        with self.assertRaises(ValueError):
                            self.validate(invalid)


class PackageBoundaryTests(unittest.TestCase):
    setUp = ControlFixture.setUp
    client = ControlFixture.client

    def test_arbitrator_loads_without_project_and_has_only_bounded_decision_authority(self):
        api = API(self.state, "coder")
        api.control.bind("coder", "arbitrator", self.order, reports_to="xo")
        with patch("devtools.api.advice_configured", return_value=True):
            onboarding = api.call("onboard", {})
        self.assertEqual("arbitrator", onboarding["package"]["behavior"])
        self.assertEqual("arbitrator", role_packages.default_role("arbitrator"))
        self.assertIn("agentscient-arbitrator", {row["name"] for row in onboarding["skills"] if row["loaded"]})
        self.assertIn("decision_resolve", onboarding["tools"])
        for forbidden in ("work_assess", "work_save", "work_check", "framework_generate", "review_record",
                          "orchestrate", "steer", "conclude"):
            with self.subTest(tool=forbidden):
                self.assertNotIn(forbidden, onboarding["tools"])
                with self.assertRaisesRegex(ValueError, "not available"):
                    api.call(forbidden, {})
        for harness in ("codex", "claude", "grok"):
            with self.subTest(harness=harness):
                profile = role_packages.runtime_profile("arbitrator", {"harness": harness})
                self.assertEqual("arbitrator", profile["role_package"]["behavior"])
        api.control.bind("coder", "research", self.order, self.job, "xo")
        self.assertNotIn("decision_resolve", {row["name"] for row in api.tools()})
        with self.assertRaisesRegex(ValueError, "not available"):
            api.call("decision_resolve", {})

    def test_role_bound_catalog_keeps_co_interface_and_bootstrap_surface(self):
        api = API(self.state, "owner")
        with patch("devtools.api.advice_configured", return_value=True), patch("devtools.api.project_config", return_value={"root": str(self.state)}), \
                patch("devtools.api.development_configured", return_value=True):
            names = {row["name"] for row in api.tools()}
            self.assertEqual({"work_context", "work_report", "guidance_request", "onboard", "skill_read", "readiness_report", "direct", "monitor", "roll_call"}, names)
        team = json.loads((self.state / "team.json").read_text())
        team["co"] = "owner"
        (self.state / "team.json").write_text(json.dumps(team))
        api.control.unbind("owner")
        self.assertEqual(names, {row["name"] for row in api.tools()})
        with self.assertRaisesRegex(ValueError, "not available"):
            api.call("work_report", {"text": "Unbound report"})

    def test_bootstrap_catalogue_uses_configured_co_alias_and_capabilities(self):
        api = API(self.state, "owner")
        team = json.loads((self.state / "team.json").read_text())
        team["co"] = "owner"
        (self.state / "team.json").write_text(json.dumps(team))
        api.control.unbind("owner")
        packages = role_packages.catalog()
        packages["roles"]["liaison"] = packages["roles"].pop("co")
        packages["defaults"]["co"] = "liaison"
        packages["roles"]["liaison"]["mcp"]["bound"].remove("guidance_request")
        with patch("devtools.role_packages._catalog", return_value=packages):
            self.assertEqual({"work_context", "work_report", "onboard", "skill_read", "readiness_report", "direct", "monitor", "roll_call"},
                             {row["name"] for row in api.tools()})
            with self.assertRaisesRegex(ValueError, "not available"):
                api.call("work_report", {"text": "Unbound aliased CO"})

    def test_role_change_restricts_hooks_and_mcp_calls_before_service_effects(self):
        api = API(self.state, "coder")
        payload = {"hook_event_name": "PreToolUse", "tool_name": "work_save", "tool_input": {"note": "Checkpoint"}}
        with patch("devtools.api.project_config", return_value={"root": str(self.state)}):
            first = _hook(self.state, "coder", "codex", payload)
            self.assertEqual("allow", first["hookSpecificOutput"]["permissionDecision"])
            api.control.bind("coder", "research", self.order, self.job, "xo")
            before = api.control.snapshot()
            second = _hook(self.state, "coder", "codex", payload)
            self.assertEqual("deny", second["hookSpecificOutput"]["permissionDecision"])
            with patch.object(api.workspaces, "save") as effect:
                with self.assertRaisesRegex(ValueError, "not available"):
                    api.call("work_save", {"note": "Bypass denied hook"})
                effect.assert_not_called()
            self.assertEqual(before, api.control.snapshot())
        self.assertEqual("research", api.call("onboard", {})["package"]["role"])

    def test_serena_and_native_helpers_keep_exact_eligibility(self):
        for member in ("owner", "coder"):
            catalogue = tool_catalogue(self.state, member)
            self.assertNotIn("serena", catalogue)
            self.assertFalse(permitted("codex", "serena_repl", {"code": "s.info()"}, catalogue=catalogue))
            self.assertTrue(permitted("grok", "search_tool", {}, catalogue=catalogue))
            self.assertFalse(permitted("grok", "Task", {}, catalogue=catalogue))
        with patch("devtools.profiles.serena_enabled", return_value=True):
            catalogue = tool_catalogue(self.state, "coder")
            self.assertTrue(permitted("claude", "mcp__serena__serena_repl", {"code": "s.info()"}, catalogue=catalogue))
            self.assertFalse(permitted("claude", "mcp__serena__execute_shell_command", {"command": "bad"}, catalogue=catalogue))

    def test_runtime_api_needs_neither_cue_nor_php(self):
        result = subprocess.run([sys.executable, "-c", "import json,sys; from devtools.api import API; print(json.dumps([r['name'] for r in API(sys.argv[1],'owner').tools()]))", str(self.state)],
                                cwd=role_packages.SOURCE.parent.parent, env=fixture_environment(self.state, {"PATH": ""}),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("direct", json.loads(result.stdout))
        self.assertNotIn("conclude", json.loads(result.stdout))

    def test_project_paths_can_narrow_but_never_widen_the_role_package(self):
        spaces = API(self.state, "coder").workspaces
        config = {"paths": {"docs": ["docs/guide.md"], "coder": ["src/Feature/"]}}
        with patch("devtools.workspaces.project_config", return_value=config):
            self.assertEqual(["docs/guide.md"], spaces.paths("docs", assignment={}))
            self.assertEqual(["src/Feature/"], spaces.paths("coder", assignment={}))
            self.assertTrue(spaces.permitted_path("src/Feature/Example.php", "coder", assignment={}))
            self.assertFalse(spaces.permitted_path("src/Other.php", "coder", assignment={}))
            config["paths"]["docs"] = ["src/"]
            with self.assertRaisesRegex(ValueError, "role package ceiling"):
                spaces.paths("docs", assignment={})
            with self.assertRaisesRegex(ValueError, "role package ceiling"):
                spaces.permitted_path("src/Example.php", "docs", assignment={})
            config["paths"]["coder"] = ["devtools/"]
            with self.assertRaisesRegex(ValueError, "role package ceiling"):
                spaces.paths("coder", assignment={})

    def test_workspace_backing_calls_honor_removed_package_capabilities(self):
        api = API(self.state, "coder")
        package, original = role_packages.role("coder"), role_packages.role
        calls = {
            "work_save": lambda: api.workspaces.save("Checkpoint"),
            "work_refresh": api.workspaces.refresh,
            "work_recover": lambda: api.workspaces.recover("checkpoint:unused", "Restore"),
            "work_submit": lambda: api.workspaces.submit("Deliver"),
        }
        with patch("devtools.role_packages.role", side_effect=lambda name: copy.deepcopy(package) if name == "coder" else original(name)), \
                patch.object(api.workspaces, "ensure") as effect:
            for capability, invoke in calls.items():
                package["mcp"]["job"].remove(capability)
                with self.subTest(capability=capability), self.assertRaisesRegex(ValueError, "role package does not permit"):
                    invoke()
                effect.assert_not_called()

    def test_invalid_fixed_profile_is_rejected_before_workspace_preparation(self):
        team = json.loads((self.state / "team.json").read_text())
        coder = next(row for row in team["members"] if row["name"] == "coder")
        coder.update(harness="codex", model="outside")
        with self.store.connect() as db:
            db.execute("UPDATE members SET harness='codex' WHERE name='coder'")
        (self.state / "team.json").write_text(json.dumps(team))
        before = API(self.state, "coder").control.snapshot()
        package, original = role_packages.role("coder"), role_packages.role
        package["runtime"]["model"] = {"default": None, "allowed": ["inside"]}
        with patch("devtools.role_packages.role", side_effect=lambda name: copy.deepcopy(package) if name == "coder" else original(name)), \
                patch("devtools.workspaces.project_config", return_value={"root": str(self.state)}), \
                patch("devtools.workspaces.Workspaces.ensure") as effect:
            with self.assertRaisesRegex(ValueError, "explicit permitted model"):
                configuration(self.state, "coder")
            effect.assert_not_called()
        self.assertEqual(before, API(self.state, "coder").control.snapshot())
        self.assertFalse((self.state / "profiles").exists())


class RuntimePackageTests(unittest.TestCase):
    setUp = RuntimeFixture.setUp
    write = RuntimeFixture.write
    vendor = RuntimeFixture.vendor
    xo = RuntimeFixture.xo

    def test_denied_profile_cannot_allocate_seat_or_change_existing_assignment(self):
        xo = self.xo()
        adopt_plan(xo, [{"name": "Feature", "objective": "Bound work", "done_when": "Verified", "constraints": "Stay scoped"}])
        with self.store.connect() as db:
            before = {table: [tuple(row) for row in db.execute("SELECT * FROM " + table)]
                      for table in ("members", "member_bindings", "role_assignments", "jobs")}
        package, original = role_packages.role("coder"), role_packages.role
        package["runtime"]["harnesses"] = ["claude"]
        with patch("devtools.role_packages.role", side_effect=lambda name: copy.deepcopy(package) if name == "coder" else original(name)):
            with self.assertRaisesRegex(ValueError, "harness"):
                xo.call("orchestrate", {"work": [{"name": "Implementation", "job": "Feature", "role": "coder"}]})
        with self.store.connect() as db:
            after = {table: [tuple(row) for row in db.execute("SELECT * FROM " + table)] for table in before}
        self.assertEqual(before, after)
