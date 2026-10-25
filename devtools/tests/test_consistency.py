"""Registry and policy closure regressions across the actual framework validators."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools import adapters, configuration, consistency, role_packages


def malformed_role():
    return object()


def malformed_harness():
    return object()


class MalformedEvaluator:
    def __init__(self, settings, connection):
        pass


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.export = json.loads(role_packages.GENERATED.read_text())
        self.registry = json.loads(adapters.MANIFEST.read_text())

    def manifest(self, value):
        path = self.root / "adapters.json"
        path.write_text(json.dumps(value))
        return patch.object(adapters, "MANIFEST", path)

    def catalog(self, value):
        path = self.root / "role-packages.json"
        path.write_text(json.dumps(value))
        return patch.object(role_packages, "GENERATED", path)

    def assert_failed(self, result, name, message=None):
        self.assertEqual("failed", result["status"])
        failure = next(row for row in result["checks"] if row["name"] == name)
        self.assertEqual("failed", failure["status"])
        if message:
            self.assertIn(message, failure["detail"])

    def test_current_framework_closes_without_provider_calls_or_credential_reads(self):
        with patch("devtools.credentials.read_credential", side_effect=AssertionError("Credential read")), \
                patch("devtools.jev.read_credential", side_effect=AssertionError("Credential read")), \
                patch("devtools.jev.urllib.request.build_opener", side_effect=AssertionError("Network call")):
            result = consistency.check()
        self.assertEqual("passed", result["status"], result)
        self.assertEqual({"adapter_manifest", "adapter_interfaces", "role_catalog", "skills", "runtime_defaults", "configuration_defaults", "mcp_tools"},
                         {row["name"] for row in result["checks"]})

    def test_unselected_plugin_with_missing_implementation_is_not_silently_ignored(self):
        self.registry["adapters"]["evaluation"]["unused"] = "devtools.not_installed_plugin:Adapter"
        with self.manifest(self.registry):
            result = consistency.check()
        self.assert_failed(result, "adapter_interfaces", "not installed")

    def test_every_plugin_kind_must_implement_its_runtime_interface(self):
        for kind, factory in (("role", "malformed_role"), ("harness", "malformed_harness"), ("evaluation", "MalformedEvaluator")):
            registry = copy.deepcopy(self.registry)
            registry["adapters"][kind]["malformed"] = "devtools.tests.test_consistency:" + factory
            with self.subTest(kind=kind), self.manifest(registry):
                self.assert_failed(consistency.check(), "adapter_interfaces")

    def test_role_tool_declarations_must_be_a_subset_of_actual_mcp_tools(self):
        self.export["catalog"]["roles"]["research"]["mcp"]["advice"].append("unknown_plugin_tool")
        with self.catalog(self.export):
            result = consistency.check()
        self.assert_failed(result, "mcp_tools", "unknown MCP tools")

    def test_duplicate_mcp_tool_ids_cannot_be_hidden_by_dispatch_maps(self):
        from devtools import api

        with patch.object(api, "TOOLS", [*api.TOOLS, api.TOOLS[0]]):
            result = consistency.check()
        self.assert_failed(result, "mcp_tools", "Duplicate MCP tool names")

    def test_missing_configured_skill_and_stale_generated_data_fail(self):
        self.export["catalog"]["roles"]["research"]["skills"]["role"] = "removed-skill"
        with self.catalog(self.export):
            self.assert_failed(consistency.check(), "role_catalog", "missing installed skill")
        generated = self.root / "configuration.json"
        value = json.loads(configuration.GENERATED.read_text())
        value["source_sha256"] = "stale"
        generated.write_text(json.dumps(value))
        with patch.object(configuration, "GENERATED", generated):
            self.assert_failed(consistency.check(), "configuration_defaults", "stale")

    def test_framework_skill_list_also_resolves_to_actual_skill_documents(self):
        self.export["catalog"]["framework_skills"].append("removed-framework-skill")
        with self.catalog(self.export):
            self.assert_failed(consistency.check(), "skills")

    def test_adding_configured_roles_does_not_depend_on_a_fixed_role_count(self):
        self.export["catalog"]["roles"]["second-implementer"] = copy.deepcopy(self.export["catalog"]["roles"]["coder"])
        self.export["catalog"]["roles"]["additional-investigator"] = copy.deepcopy(self.export["catalog"]["roles"]["research"])
        with self.catalog(self.export):
            result = consistency.check()
        self.assertEqual("passed", result["status"], result)
        entry = next(row for row in result["checks"] if row["name"] == "role_catalog")
        self.assertEqual(len(self.export["catalog"]["roles"]), entry["detail"]["roles"])

    def test_optional_team_validation_is_read_only_and_does_not_need_a_key(self):
        team = {"members": [{"name": "owner", "harness": "external", "workspace": str(self.root)}],
                "co": "owner", "project": {"configuration_file": "operator.json"}}
        (self.root / "team.json").write_text(json.dumps(team))
        overlay = {"version": 1, "project": {"features": {"advice": True},
                   "connection": {"credential": {"file": "missing.key"}}},
                   "roles": {"research": {"features": {"filtering": True}}}}
        (self.root / "operator.json").write_text(json.dumps(overlay))
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        with patch("devtools.credentials.read_credential", side_effect=AssertionError("Credential read")):
            result = consistency.check(self.root)
        self.assertEqual("passed", result["status"], result)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})
        entry = next(row for row in result["checks"] if row["name"] == "team_configuration")
        self.assertEqual(2, entry["detail"]["configuration_variants"])
        overlay["roles"]["unknown-role"] = {}
        (self.root / "operator.json").write_text(json.dumps(overlay))
        self.assert_failed(consistency.check(self.root), "team_configuration", "unknown role packages")

    def test_cli_reports_json_and_failure_exit_status(self):
        root = Path(__file__).resolve().parents[2]
        checked = subprocess.run([sys.executable, "-m", "devtools.consistency"], cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertEqual("passed", json.loads(checked.stdout)["status"])
        checked = subprocess.run([sys.executable, "-m", "devtools.consistency", "--state", str(self.root / "missing-team")],
                                 cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(1, checked.returncode, checked.stderr)
        self.assertEqual("failed", json.loads(checked.stdout)["status"])
        self.assertFalse((self.root / "missing-team").exists())
