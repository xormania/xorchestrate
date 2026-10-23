import copy
import json
import os
import sqlite3
import struct
import subprocess
import sys
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from fixtures.workspaces import WorkspaceFixture
from support import runtime_policy

from devtools import readiness, role_packages
from devtools.api import API
from devtools.store import Store
from devtools.workspaces import git


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        WorkspaceFixture.setUp(self)
        home = patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex-home")})
        home.start()
        self.addCleanup(home.stop)

    def test_operator_bootstrap_exports_exact_normal_co_connection(self):
        state = self.root / "fresh-state"
        result = subprocess.run(
            [
                sys.executable,
                str(readiness.ENTRY),
                "init",
                "--state",
                str(state),
                "--workspace",
                str(self.checkout),
                "--co",
                "owner",
                "--member",
                "owner:external",
                "--member",
                "chief:codex",
                "--member",
                "worker:codex",
                "--repository",
                "example/project",
                "--serena-image",
                "agentscient-serena:v2-beta",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        config = json.loads((state / "team.json").read_text())
        self.assertEqual({}, config["project"]["development"])
        self.assertEqual("dev", config["project"]["target"])
        connected = subprocess.run(
            [sys.executable, str(readiness.ENTRY), "connection", "--state", str(state)],
            capture_output=True,
            text=True,
            check=True,
        )
        server = json.loads(connected.stdout)["mcpServers"]["agentscient_dev"]
        self.assertEqual([str(readiness.ENTRY), "mcp", "--state", str(state), "--member", "owner"], server["args"])
        self.assertNotIn("--operator", server["args"])
        self.assertEqual([], Store(state).events(kind="launch_requested")["events"])
        self.assertEqual({"selection": True, "advice": False, "filtering": True,
                          "completion": True, "guidance": True}, readiness.config(state)["features"])

    def configured(self):
        config = json.loads((self.state / "team.json").read_text())
        config["project"].update(development={}, serena={"image": "agentscient-serena:v2-beta"})
        config["members"][1].update(harness="codex", binary="codex")
        (self.state / "team.json").write_text(json.dumps(config))
        self.assessment_configuration({})
        git(self.checkout, "remote", "set-url", "origin", "https://github.com/example/project.git")

    def preflight(self, *, supported_host=True, codex_version="codex-cli 0.122.0"):
        def probe(argv, **kwargs):
            if argv[1:3] == ["image", "inspect"]:
                return json.dumps({"org.opencontainers.image.revision": "b79e2a55f9d4072084977dd35bd07d31146d88b9"})
            if argv == ["/fixture/codex", "--version"]:
                return codex_version
            return "Fixture 1.2.3"

        with (
            patch("devtools.readiness.probe", side_effect=probe),
            patch("devtools.readiness.platform", return_value="Locked platform satisfied"),
            patch("devtools.readiness.shutil.which", side_effect=lambda binary: None if binary == "missing-codex" else "/fixture/" + binary),
            patch("devtools.compat.codex_capability", return_value={"status": "supported"}),
            patch("devtools.readiness.managed_platform", return_value="Supported host") if supported_host else nullcontext(),
        ):
            return readiness.doctor(self.state)

    def dynamic_team(self):
        self.state = self.root / "runtime-state"
        result = subprocess.run(
            [sys.executable, str(readiness.ENTRY), "init", "--state", str(self.state),
             "--workspace", str(self.checkout), "--repository", "example/project", "--target", "dev",
             "--serena-image", "agentscient-serena:v2-beta", "--co", "owner", "--member", "owner:external", "--runtime"],
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        git(self.checkout, "remote", "set-url", "origin", "https://github.com/example/project.git")
        availability = json.loads((self.state / "vendors.json").read_text())
        availability["vendors"]["openai"]["available"] = True
        availability["vendors"]["anthropic"]["available"] = True
        (self.state / "vendors.json").write_text(json.dumps(availability))
        self.assessment_configuration({})
        return runtime_policy()

    def test_documented_dynamic_setup_checks_real_capacity_and_selected_fallback_readonly(self):
        policy = self.dynamic_team()
        policy["max_sessions"] = 2
        policy["profiles"]["codex"]["binary"] = "missing-codex"
        (self.state / "roles.json").write_text(json.dumps(policy))
        before = {name: (self.state / name).read_bytes() for name in ("team.json", "roles.json", "vendors.json", "live.sqlite3")}
        with patch("devtools.store.Store.upgrade", side_effect=AssertionError("Doctor must not upgrade state")):
            result = self.preflight()
        self.assertEqual("preflight_passed", result["status"], result)
        self.assertEqual(1, len(json.loads(before["team.json"])["members"]))
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual("passed", checks["collaborator capacity"]["status"])
        self.assertEqual("claude", checks["Runtime role xo"]["detail"]["harness"])
        self.assertEqual("passed", checks["claude binary"]["status"])
        self.assertNotIn("codex binary", checks)
        self.assertEqual(before, {name: (self.state / name).read_bytes() for name in before})

    def test_dynamic_capacity_and_unavailable_or_omitted_workflow_role_are_actionable(self):
        policy = self.dynamic_team()
        policy["max_sessions"] = 1
        (self.state / "roles.json").write_text(json.dumps(policy))
        capacity = next(item for item in self.preflight()["checks"] if item["name"] == "collaborator capacity")
        self.assertEqual("failed", capacity["status"])
        self.assertIn("roles.json max_sessions", capacity["detail"])
        policy["max_sessions"] = 2
        del policy["roles"]["planner"]
        (self.state / "roles.json").write_text(json.dumps(policy))
        missing = next(item for item in self.preflight()["checks"] if item["name"] == "Runtime role planner")
        self.assertEqual("failed", missing["status"])
        self.assertIn("Configure planner preferences", missing["detail"])
        policy["roles"]["planner"] = ["grok"]
        (self.state / "roles.json").write_text(json.dumps(policy))
        unavailable = next(item for item in self.preflight()["checks"] if item["name"] == "Runtime role planner")
        self.assertEqual("failed", unavailable["status"])
        self.assertIn("No runtime available for planner", unavailable["detail"])

    def test_runtime_preflight_uses_bound_co_identity_instead_of_an_unconfigured_default(self):
        policy = self.dynamic_team()
        del policy["roles"]["co"]
        policy["roles"]["interface-v2"] = ["claude"]
        (self.state / "roles.json").write_text(json.dumps(policy))
        catalog = role_packages.catalog()
        catalog["roles"]["interface-v2"] = copy.deepcopy(catalog["roles"]["co"])
        catalog["roles"]["interface-v2"]["runtime"]["harnesses"] = ["claude"]
        with patch("devtools.role_packages._catalog", return_value=catalog):
            operator = API(self.state, "owner", operator=True)
            order = operator.work.work_order_create(title="Bound CO", objective="Resume current role", done_when="Profile matches")
            operator.control.bind("owner", "interface-v2", order["work_order"]["id"])
            database = (self.state / "live.sqlite3").read_bytes()
            result = self.preflight()
        self.assertEqual("preflight_passed", result["status"], result)
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual("claude", checks["Runtime role interface-v2"]["detail"]["harness"])
        self.assertEqual("passed", checks["claude binary (CO operator)"]["status"])
        self.assertNotIn("Runtime role co", checks)
        self.assertEqual(database, (self.state / "live.sqlite3").read_bytes())

    def test_each_selected_provider_gets_its_own_native_login_readiness_check(self):
        policy = self.dynamic_team()
        policy["profiles"]["independent"] = {**policy["profiles"]["codex"], "provider": "independent"}
        policy["roles"]["architect"] = ["independent"]
        (self.state / "roles.json").write_text(json.dumps(policy))
        home = self.root / "codex-home"
        home.mkdir()
        (home / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\n'
            '[model_providers.independent]\nname = "Independent"\nenv_key = "MODEL_KEY"\n'
        )
        result = self.preflight()
        self.assertEqual("blocked", result["status"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual("passed", checks["codex binary (independent)"]["status"])
        self.assertEqual("ephemeral", checks["codex binary (independent)"]["detail"]["login_storage"])
        self.assertEqual("failed", checks["codex binary"]["status"])
        self.assertIn("dedicated CODEX_HOME", checks["codex binary"]["detail"])

    def test_co_and_workers_sharing_codex_get_separate_login_readiness_checks(self):
        policy = self.dynamic_team()
        policy["roles"] = {role: ["codex"] for role in policy["roles"]}
        (self.state / "roles.json").write_text(json.dumps(policy))
        home = self.root / "codex-home"
        home.mkdir()
        (home / "config.toml").write_text('cli_auth_credentials_store="keyring"\n')
        result = self.preflight()
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual("blocked", result["status"])
        operator = checks["codex binary (CO operator)"]
        self.assertEqual("passed", operator["status"])
        self.assertEqual("keyring", operator["detail"]["login_storage"])
        self.assertIn("Native TUI", operator["detail"]["protocol"])
        self.assertEqual("failed", checks["codex binary"]["status"])
        self.assertIn("dedicated CODEX_HOME", checks["codex binary"]["detail"])

    def test_codex_co_requires_mcp_approval_support_independently_of_managed_protocol(self):
        policy = self.dynamic_team()
        policy["roles"] = {role: ["codex"] for role in policy["roles"]}
        (self.state / "roles.json").write_text(json.dumps(policy))
        before = {name: (self.state / name).read_bytes()
                  for name in ("team.json", "roles.json", "live.sqlite3")}
        for version, expected in (("codex-cli 0.121.0", "failed"),
                                  ("codex-cli 0.122.0", "passed"),
                                  ("codex-cli 0.123.0", "passed"),
                                  ("unreported", "failed")):
            with self.subTest(version=version):
                result = self.preflight(codex_version=version)
                checks = {item["name"]: item for item in result["checks"]}
                operator = checks["codex binary (CO operator)"]
                self.assertEqual(expected, operator["status"], operator)
                self.assertEqual("passed", checks["codex binary"]["status"])
                self.assertEqual("blocked" if expected == "failed" else "preflight_passed", result["status"])
                if expected == "failed":
                    self.assertIn("0.122.0", operator["detail"])
                    self.assertIn("default_tools_approval_mode", operator["detail"])
        self.assertEqual(before, {name: (self.state / name).read_bytes() for name in before})
        self.assertFalse(Store(self.state).events(kind="launch_requested")["events"])

    def test_fixed_team_still_requires_collaborator_slots(self):
        self.configured()
        team = json.loads((self.state / "team.json").read_text())
        team["members"] = team["members"][:1]
        (self.state / "team.json").write_text(json.dumps(team))
        capacity = next(item for item in self.preflight()["checks"] if item["name"] == "collaborator capacity")
        self.assertEqual("failed", capacity["status"])
        self.assertIn("collaborator slots", capacity["detail"])

    def test_managed_platform_failure_blocks_preflight_before_any_launch(self):
        self.configured()
        with patch("devtools.readiness.sys.platform", "darwin"):
            result = self.preflight(supported_host=False)
        self.assertEqual("blocked", result["status"])
        platform = next(item for item in result["checks"] if item["name"] == "managed process platform")
        self.assertEqual("failed", platform["status"])
        self.assertIn("Linux or WSL2", platform["detail"])
        self.assertFalse(self.store.events(kind="launch_requested")["events"])

    def test_readonly_preflight_checks_protocol_without_claiming_authenticated_acceptance(self):
        self.configured()
        old_config = (self.state / "team.json").read_bytes()
        old_db = (self.state / "live.sqlite3").read_bytes()

        def probe(argv, **kwargs):
            if argv[1:3] == ["image", "inspect"]:
                return json.dumps({"org.opencontainers.image.revision": "b79e2a55f9d4072084977dd35bd07d31146d88b9"})
            return "Fixture 1.2.3"

        with (
            patch("devtools.readiness.probe", side_effect=probe),
            patch("devtools.readiness.platform", return_value="Locked platform satisfied"),
            patch("devtools.readiness.shutil.which", return_value="/fixture/codex"),
            patch("devtools.compat.codex_capability", return_value={"status": "supported"}),
            patch("devtools.readiness.managed_platform", return_value="Supported host"),
        ):
            result = readiness.doctor(self.state)
            self.assertEqual("preflight_passed", result["status"], result)
            self.assertEqual("not established by preflight", result["authenticated_acceptance"])
            with patch("devtools.compat.codex_capability", return_value={"status": "unsupported"}):
                blocked = readiness.doctor(self.state)
            self.assertEqual("blocked", blocked["status"])
            self.assertEqual("failed", next(c for c in blocked["checks"] if c["name"] == "codex binary")["status"])
        self.assertEqual(old_config, (self.state / "team.json").read_bytes())
        self.assertEqual(old_db, (self.state / "live.sqlite3").read_bytes())

    def test_missing_project_and_mismatched_remote_fail_without_echoing_credentials(self):
        self.configured()
        git(self.checkout, "remote", "set-url", "origin", "https://user:secret-value@github.com/example/other.git")
        with (
            patch("devtools.readiness.platform", return_value="okay"),
            patch("devtools.readiness.probe", side_effect=FileNotFoundError("Dependency unavailable")),
            patch("devtools.readiness.shutil.which", return_value=None),
        ):
            result = readiness.doctor(self.state)
        self.assertEqual("blocked", result["status"])
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual("failed", next(c for c in result["checks"] if c["name"] == "Git remote")["status"])
        config = json.loads((self.state / "team.json").read_text())
        del config["project"]
        (self.state / "team.json").write_text(json.dumps(config))
        with patch("devtools.readiness.shutil.which", return_value=None):
            self.assertEqual("blocked", readiness.doctor(self.state)["status"])

    def assessment_configuration(self, overlay):
        # Native readiness fixtures do not need a live evaluation credential.
        # Individual decision tests opt into their actual feature explicitly.
        overlay = copy.deepcopy(overlay)
        project = overlay.setdefault("project", {})
        project["features"] = {"selection": False, "advice": False, "filtering": False,
                               "completion": False, **project.get("features", {})}
        team = json.loads((self.state / "team.json").read_text())
        team["project"]["configuration_file"] = "configuration.json"
        (self.state / "team.json").write_text(json.dumps(team))
        (self.state / "configuration.json").write_text(json.dumps({"version": 1, **overlay}))

    def quiet_doctor(self):
        with (
            patch("devtools.readiness.platform", return_value="Locked platform satisfied"),
            patch("devtools.readiness.probe", side_effect=FileNotFoundError("Dependency unavailable")),
            patch("devtools.readiness.shutil.which", return_value=None),
        ):
            return readiness.doctor(self.state)

    def test_config_inspection_is_readonly_scoped_and_never_reads_the_key(self):
        job = self.api.control.snapshot()["job"]
        self.assessment_configuration({
            "project": {"connection": {"credential": {"file": "unread-key"}},
                        "decisions": {"rules": {"scope": {"choice": {"minimum_probability": 0.6}}}}},
            "roles": {"coder": {"decisions": {"rules": {"scope": {"choice": {"minimum_probability": 0.7}}}}}},
            "jobs": {str(job): {"decisions": {"rules": {"scope": {"choice": {"minimum_probability": 0.8}}}}}},
        })
        database = (self.state / "live.sqlite3").read_bytes()
        with (
            patch("devtools.readiness.read_credential", side_effect=AssertionError("Must not read key")),
            patch("devtools.store.Store.upgrade", side_effect=AssertionError("Must not upgrade state")),
        ):
            settings = readiness.config(self.state, role="coder", job=job)
        self.assertEqual(0.8, settings["decisions"]["rules"]["scope"]["choice"]["minimum_probability"])
        self.assertEqual({"role": "coder", "job": job}, settings["selection"])
        self.assertEqual({"file": str(self.state / "unread-key")}, settings["connection"]["credential"])
        self.assertEqual(64, len(settings["revision"]))
        self.assertEqual(database, (self.state / "live.sqlite3").read_bytes())

    def test_config_cli_reports_effective_settings_and_invalid_selectors(self):
        self.assessment_configuration({"project": {"connection": {"credential": {"file": "absent-key"}}}})
        command = [sys.executable, str(readiness.ENTRY), "config", "--state", str(self.state)]
        result = subprocess.run(command + ["--role", "coder"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("coder", json.loads(result.stdout)["selection"]["role"])
        invalid = subprocess.run(command + ["--role", "missing_role"], capture_output=True, text=True)
        self.assertEqual(2, invalid.returncode)
        self.assertIn("Unknown configuration role", invalid.stderr)
        self.assertFalse((self.state / "absent-key").exists())

    def test_config_inspection_redacts_secret_text_but_preserves_source_reference(self):
        self.assessment_configuration({"project": {
            "filtering": {"failure_message": "Never expose fixture-secret-123456"},
            "connection": {"credential": {"env": "LOCAL_JEV_KEY"}},
        }})
        with patch.dict(os.environ, {"TEST_PASSWORD": "fixture-secret-123456"}):
            result = readiness.config(self.state)
        self.assertNotIn("fixture-secret-123456", json.dumps(result))
        self.assertEqual({"env": "LOCAL_JEV_KEY"}, result["connection"]["credential"])

    def test_doctor_checks_enabled_role_credentials_locally_without_provider_call(self):
        key = self.state / "local.key"
        key.write_text("fixture-local-key-123456\n")
        key.chmod(0o600)
        self.assessment_configuration({"roles": {"research": {
            "features": {"filtering": True}, "connection": {"credential": {"file": "local.key"}},
        }}})
        with patch("devtools.advice.Jev.evaluate") as provider:
            ready = self.quiet_doctor()
        provider.assert_not_called()
        credential = next(item for item in ready["checks"] if item["name"] == "Jev credential")
        self.assertEqual("passed", credential["status"], ready)
        self.assertEqual(1, credential["detail"]["sources_checked"])
        self.assertNotIn("fixture-local-key-123456", json.dumps(ready))
        key.unlink()
        missing = self.quiet_doctor()
        self.assertEqual("failed", next(item for item in missing["checks"] if item["name"] == "Jev credential")["status"])
        self.assertEqual("blocked", missing["status"])

    def test_selection_alone_requires_a_credential_without_contacting_provider(self):
        self.assessment_configuration({"project": {
            "features": {"selection": True}, "connection": {"credential": {"file": "absent-key"}},
        }})
        with patch("devtools.advice.Jev.evaluate") as provider:
            result = self.quiet_doctor()
        provider.assert_not_called()
        self.assertEqual("failed", next(item for item in result["checks"] if item["name"] == "Jev credential")["status"])
        self.assertEqual("blocked", result["status"])

    def test_selection_preflight_requires_arbitrator_and_inspects_alternative_provider(self):
        policy = self.dynamic_team()
        policy["max_sessions"] = 2
        policy["profiles"]["independent"] = {**policy["profiles"]["codex"], "provider": "independent"}
        policy["roles"]["architect"].append("independent")
        del policy["roles"]["arbitrator"]
        (self.state / "roles.json").write_text(json.dumps(policy))
        home = self.root / "codex-home"
        home.mkdir()
        (home / "config.toml").write_text(
            '[model_providers.independent]\nname = "Independent"\nenv_key = "MODEL_KEY"\n'
        )
        self.assessment_configuration({"project": {"features": {"selection": True}}})
        with patch("devtools.readiness.read_credential", return_value="fixture-key"):
            result = self.preflight()
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual("failed", checks["collaborator capacity"]["status"])
        self.assertIn("reserved Arbitrator", checks["collaborator capacity"]["detail"])
        self.assertEqual("failed", checks["Runtime role arbitrator"]["status"])
        self.assertIn("Configure arbitrator preferences", checks["Runtime role arbitrator"]["detail"])
        self.assertEqual("passed", checks["codex binary (independent)"]["status"])
        self.assertEqual("blocked", result["status"])

    def test_doctor_disabled_features_do_not_require_or_read_a_key(self):
        self.assessment_configuration({"project": {"connection": {"credential": {"file": "absent-key"}}}})
        with patch("devtools.readiness.read_credential") as read:
            result = self.quiet_doctor()
        read.assert_not_called()
        self.assertEqual("not_checked", next(item for item in result["checks"] if item["name"] == "Jev credential")["status"])

    def test_doctor_reports_legacy_configuration_as_migration_error(self):
        team = json.loads((self.state / "team.json").read_text())
        team["project"]["jev"] = {}
        (self.state / "team.json").write_text(json.dumps(team))
        result = self.quiet_doctor()
        check = next(item for item in result["checks"] if item["name"] == "assessment configuration")
        self.assertEqual("failed", check["status"])
        self.assertIn("project.configuration_file", check["detail"])
        self.assertEqual("blocked", result["status"])

    def test_platform_probe_uses_locked_real_platform_and_reports_missing_extensions(self):
        result = subprocess.CompletedProcess([], 2, json.dumps([{"name": "ext-mbstring", "status": "missing"}]), "")
        with (
            patch.dict(os.environ, {"COMPOSER_IGNORE_PLATFORM_REQS": "1", "COMPOSER_NO_DEV": "1"}),
            patch("devtools.readiness.subprocess.run", return_value=result) as run,
        ):
            with self.assertRaisesRegex(ValueError, "ext-mbstring"):
                readiness.platform(self.checkout, {"php": "/configured/php", "composer": "composer"})
        self.assertIn("--lock", run.call_args.args[0])
        self.assertIn("--no-plugins", run.call_args.args[0])
        self.assertNotIn("COMPOSER_IGNORE_PLATFORM_REQS", run.call_args.kwargs["env"])
        self.assertNotIn("COMPOSER_NO_DEV", run.call_args.kwargs["env"])
        self.assertTrue(run.call_args.kwargs["env"]["PATH"].startswith("/configured" + os.pathsep))

    def test_evidence_keeps_actual_observations_without_manufacturing_acceptance(self):
        scope = self.api.control.snapshot()
        order, job = scope["work_order"], scope["job"]
        self.store.event("coder", "native_session", native_id="fixture-thread", work_order=order, job=job)
        self.store.event("coder", "serena_call", success=True, work_order=order, job=job)
        self.api.call(
            "work_report",
            {
                "kind": "result",
                "text": "Result evidence",
                "evidence": {"name": "result.txt", "content": "Observed behavior"},
            },
        )
        connections = []
        connect = sqlite3.connect

        def track_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            self.addCleanup(connection.close)
            return connection

        with patch("devtools.store.sqlite3.connect", side_effect=track_connection):
            report = readiness.evidence(self.state, order)
            with self.assertRaisesRegex(ValueError, "No recorded work order"):
                readiness.evidence(self.state, order + 99)
        self.assertEqual(2, len(connections))
        for connection in connections:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                connection.execute("SELECT 1")
        self.assertEqual({"coder": ["fixture-thread"]}, report["native_sessions"])
        self.assertEqual(1, report["successful_serena_calls"])
        self.assertEqual(1, report["artifact_count"])
        self.assertEqual(1, report["completed_tool_calls"]["work_report"])
        self.assertNotIn("passed", report)
        self.assertIn("does not certify", report["interpretation"])
        self.assertTrue(all(row["acknowledged"] == 0 for row in report["messages"]))

    def test_suppressed_capture_remains_readable_after_credential_repair(self):
        order = self.api.control.snapshot()["work_order"]
        before = readiness.evidence(self.state, order)
        key = self.state / "provider.key"
        key.write_text("fixture-evidence-key")
        key.chmod(0o644)
        policy = self.state / "capture-policy.json"
        policy.write_text(json.dumps({"version": 1, "project": {
            "connection": {"credential": {"file": str(key)}}}}))
        team_path = self.state / "team.json"
        team = json.loads(team_path.read_text())
        team["project"]["configuration_file"] = str(policy)
        team_path.write_text(json.dumps(team))
        for kind, payload in (
            ("operation_finished", {"capability": "work_context"}),
            ("serena_call", {"success": True}),
            ("native_session", {"native_id": "unavailable-conversation"}),
            ("runtime_observed", {"settings": {"model": "unavailable-model"}}),
        ):
            self.store.event("coder", kind, work_order=order, **payload)
        key.chmod(0o600)
        report = readiness.evidence(self.state, order)
        for field in ("completed_tool_calls", "successful_serena_calls", "native_sessions", "runtime_records"):
            self.assertEqual(before[field], report[field], field)
        self.assertEqual({"total": 4, "reasons": {"credential_filter_unavailable": 4}}, report["suppressed_events"])
        self.store.event("coder", "operation_finished", capability="work_context", work_order=order)
        recovered = readiness.evidence(self.state, order)
        self.assertEqual(before["completed_tool_calls"].get("work_context", 0) + 1,
                         recovered["completed_tool_calls"]["work_context"])
        self.assertEqual(report["suppressed_events"], recovered["suppressed_events"])


class ManagedPlatformTests(unittest.TestCase):
    def sockets(self):
        peer, listener = MagicMock(), MagicMock()
        peer.__enter__.return_value = peer
        peer.getsockopt.return_value = struct.pack("3i", os.getpid(), os.getuid(), 0)
        listener.__enter__.return_value = listener
        return peer, listener

    def test_missing_namespace_identity_and_kernel_pidfd_support_are_reported(self):
        with patch("devtools.readiness.sys.platform", "linux"), patch("devtools.processes.identity", return_value=None):
            with self.assertRaisesRegex(ValueError, "readable /proc"):
                readiness.managed_platform()
        with (patch("devtools.processes.identity", return_value={"pid": os.getpid(), "boot": "fixture"}),
              patch("devtools.readiness.os.pidfd_open", side_effect=PermissionError("blocked"))):
            with self.assertRaisesRegex(ValueError, "enable pidfd access"):
                readiness.managed_platform()

    def test_missing_python_process_api_is_actionable(self):
        with patch("devtools.readiness.os.pidfd_open", None):
            with self.assertRaisesRegex(ValueError, "Linux Python build"):
                readiness.managed_platform()

    def test_abstract_unix_socket_restriction_is_reported(self):
        peer, listener = self.sockets()
        listener.bind.side_effect = PermissionError("blocked")
        with (patch("devtools.processes.identity", return_value={"pid": os.getpid(), "boot": "fixture"}),
              patch("devtools.readiness.socket.socketpair", return_value=(peer, MagicMock())),
              patch("devtools.readiness.socket.socket", return_value=listener)):
            with self.assertRaisesRegex(ValueError, "allow AF_UNIX abstract bind"):
                readiness.managed_platform()
        listener.bind.assert_called_once()

    def test_supported_platform_probe_never_signals_a_process(self):
        peer, listener = self.sockets()
        with (patch("devtools.processes.identity", return_value={"pid": os.getpid(), "boot": "fixture"}),
              patch("devtools.readiness.socket.socketpair", return_value=(peer, MagicMock())),
              patch("devtools.readiness.socket.socket", return_value=listener),
              patch("devtools.readiness.signal.pidfd_send_signal", side_effect=AssertionError("No process signals"))):
            self.assertIn("are available", readiness.managed_platform())
        self.assertTrue(listener.bind.call_args.args[0].startswith("\0"))


if __name__ == "__main__":
    unittest.main()
