"""Maintained role settings reach native launch boundaries without vendor calls."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from devtools import console, native, runtime
from devtools.api import API
from devtools.launch import configuration, initialize
from devtools.role_packages import catalog
from devtools.store import Store

# This is the owner-approved acceptance contract, independent of the defaults file.
EXPECTED = {
    "co": ("codex", "gpt-6-astra", "xhigh"),
    "xo": ("codex", "gpt-6-astra", "xhigh"),
    "nco": ("codex", "gpt-6-astra", "high"),
    "planner": ("codex", "gpt-6.1-sol", "xhigh"),
    "architect": ("claude", "claude-opus-5-5", "xhigh"),
    "coder": ("claude", "claude-opus-5-5", "xhigh"),
    "research": ("codex", "gpt-6-astra", "max"),
    "arbitrator": ("codex", "gpt-6-astra", "xhigh"),
    "test_writer": ("codex", "gpt-6.1-sol", "xhigh"),
    "audit": ("grok", "grok-4.7", "high"),
    "security_auditor": ("codex", "gpt-daybreak-blue-latest", "xhigh"),
    "adversarial_review": ("codex", "gpt-6-sol", "high"),
    "docs": ("claude", "claude-sonnet-5-5", "high"),
}


class RuntimeDefaultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace, self.state = self.root / "workspace", self.root / "state"
        self.workspace.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        isolated = patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.home / "codex"),
                                           "GROK_HOME": str(self.home / "grok")})
        isolated.start()
        self.addCleanup(isolated.stop)
        initialize(self.state, self.workspace, ["owner:external"], co="owner", runtime=True)
        self.store = Store(self.state)

    def enable_fixture_executables(self):
        policy, vendors, _ = runtime.load(self.state)
        grok = Path(__file__).with_name("grok_mcp_fixture.py")
        grok.chmod(0o755)
        codex = Path(__file__).with_name("co_fixture.py")
        codex.chmod(0o755)
        for profile in policy["profiles"].values():
            # Grok CO performs native MCP registration before the relayed TUI.
            # Codex CO probes the installed version before configuring MCP.
            profile["binary"] = {"grok": str(grok), "codex": str(codex)}.get(profile["harness"], sys.executable)
        for vendor in vendors["vendors"].values():
            vendor["available"] = True
        (self.state / "roles.json").write_text(json.dumps(policy))
        (self.state / "vendors.json").write_text(json.dumps(vendors))
        return policy

    def bind_roles(self):
        """Trusted bindings; the production resolver still supplies every setting."""
        operator = API(self.state, "owner", operator=True)
        order = operator.work.work_order_create(title="Runtime settings", objective="Load configured roles",
                                                done_when="Native requests retain model and effort")["work_order"]["id"]
        operator.control.bind("owner", "co", order)
        job = operator.work.job_create(title="Configuration proof", objective="Prove native inputs",
                                       done_when="All transports checked", work_order=order)["job"]["id"]
        for role in ["xo", *[name for name in EXPECTED if name not in {"co", "xo"}]]:
            harness = runtime.resolve(self.state, role)["harness"]
            name = "fixture-" + role
            self.store.initialize([{"name": name, "harness": harness, "workspace": str(self.workspace)}], runtime=True)
            operator.control.bind(name, role, order, job=job if role == "nco" else None,
                                  reports_to="owner" if role == "xo" else "fixture-xo")

    def test_fresh_initialization_loads_every_approved_role_and_disables_vendors(self):
        policy, vendors, revision = runtime.load(self.state)
        self.assertEqual(set(catalog()["roles"]), set(policy["roles"]))
        self.assertEqual(set(EXPECTED), set(policy["roles"]))
        self.assertEqual(12, policy["max_sessions"])
        self.assertTrue(all(not vendor["available"] for vendor in vendors["vendors"].values()))
        with self.assertRaisesRegex(ValueError, "No runtime available"):
            runtime.resolve(self.state, "co", installed=False)
        self.enable_fixture_executables()
        for role, expected in EXPECTED.items():
            with self.subTest(role=role):
                selected = runtime.resolve(self.state, role)
                self.assertEqual(expected, tuple(selected[key] for key in ("harness", "model", "reasoning")))
                self.assertEqual(role, selected["role_package"]["role"])
                self.assertNotEqual(revision, selected["selection"]["revision"])

    def test_specialists_do_not_silently_change_model_when_their_vendor_is_disabled(self):
        self.enable_fixture_executables()
        vendors = json.loads((self.state / "vendors.json").read_text())
        for vendor, roles, co in (
            ("anthropic", ("architect", "coder", "docs"), ("codex", "gpt-6-astra", "xhigh")),
            ("openai", ("xo", "nco"), ("grok", "grok-4.7", "high")),
        ):
            with self.subTest(vendor=vendor):
                vendors["vendors"][vendor]["available"] = False
                (self.state / "vendors.json").write_text(json.dumps(vendors))
                for role in roles:
                    with self.subTest(role=role), self.assertRaisesRegex(ValueError, "No runtime available"):
                        runtime.resolve(self.state, role)
                self.assertEqual(co, tuple(runtime.resolve(self.state, "co")[key]
                                           for key in ("harness", "model", "reasoning")))
                vendors["vendors"][vendor]["available"] = True

    def test_co_all_three_profiles_reach_native_argv_and_requested_telemetry(self):
        policy = self.enable_fixture_executables()
        expected = {"codex_astra_xhigh": ("codex", "gpt-6-astra", "xhigh"),
                    "codex_sol_xhigh": ("codex", "gpt-6.1-sol", "xhigh"),
                    "grok_high": ("grok", "grok-4.7", "high")}
        self.assertEqual(list(expected), policy["roles"]["co"])
        for profile, (harness, model, effort) in expected.items():
            with self.subTest(profile=profile), patch.object(console, "relay", return_value=0) as relay:
                self.assertEqual(0, console.run(self.state, profile=profile))
                argv = relay.call_args.args[0]
                if harness == "codex":
                    self.assertIn('model="' + model + '"', argv)
                    self.assertIn('model_reasoning_effort="' + effort + '"', argv)
                    self.assertIn('model_provider="openai"', argv)
                else:
                    self.assertEqual(model, argv[argv.index("--model") + 1])
                    self.assertEqual(effort, argv[argv.index("--effort") + 1])
                event = self.store.events(actor="owner", kind="runtime_requested")["events"][-1]["data"]
                self.assertEqual((harness, model, effort), tuple(event["settings"][key] for key in ("harness", "model", "reasoning")))
                self.assertEqual("explicit", event["sources"]["model"])
                self.assertEqual("explicit", event["sources"]["reasoning"])
                self.assertEqual(profile, event["selection"]["profile"])

    def test_worker_role_settings_reach_each_native_transport(self):
        self.enable_fixture_executables()
        self.bind_roles()
        for role, (harness, model, effort) in EXPECTED.items():
            if role == "co":
                continue
            with self.subTest(role=role):
                member = "fixture-" + role
                config = configuration(self.state, member)
                generation = self.store.begin_session(member, os.getpid(), settings=config)
                if harness == "claude":
                    process = Mock(pid=12345, returncode=0)
                    process.poll.return_value = 0
                    with patch.object(native.subprocess, "Popen", return_value=process) as spawn, \
                            patch("devtools.processes.register"), patch.object(native.os, "killpg"):
                        native.claude(self.state, member, config, generation)
                    argv = spawn.call_args.args[0]
                    self.assertEqual(model, argv[argv.index("--model") + 1])
                    self.assertEqual(effort, argv[argv.index("--effort") + 1])
                else:
                    transport = native.Native(self.state, member, config, generation)
                    try:
                        if harness == "codex":
                            mcp = Mock(request=AsyncMock(side_effect=[{}, {"tools": API(self.state, member).tools()}]),
                                       notify=AsyncMock())
                            rpc = Mock(request=AsyncMock(side_effect=[{}, {"thread": {"id": "fixture-thread"}}]),
                                       notify=AsyncMock())
                            transport.spawn = AsyncMock(side_effect=[mcp, rpc])
                            with patch.object(native, "codex_capability", return_value={"status": "supported"}):
                                asyncio.run(transport.start())
                            method, request = rpc.request.await_args.args
                            self.assertEqual("thread/start", method)
                            self.assertEqual(model, request["model"])
                            self.assertEqual("openai", request["modelProvider"])
                            self.assertEqual(effort, request["config"]["model_reasoning_effort"])
                        else:
                            rpc = Mock(request=AsyncMock(side_effect=[{"authMethods": []}, {"sessionId": "fixture-session"}]))
                            transport.spawn = AsyncMock(return_value=rpc)
                            asyncio.run(transport.start())
                            argv = transport.spawn.await_args.args[0]
                            self.assertEqual(model, argv[argv.index("--model") + 1])
                            self.assertEqual(effort, argv[argv.index("--effort") + 1])
                    finally:
                        transport.trace.close()
                requested = self.store.events(actor=member, kind="runtime_requested")["events"][-1]["data"]
                self.assertEqual((harness, model, effort),
                                 tuple(requested["settings"][key] for key in ("harness", "model", "reasoning")))
                # Fixture transport success is not evidence of authenticated model availability.
                self.assertFalse(self.store.events(actor=member, kind="runtime_observed")["events"])
                self.store.session(member, generation=generation, state="stopped", pid=None)

    def test_invalid_maintained_profile_reference_fails_before_team_creation(self):
        broken = runtime.default_policy()
        broken["roles"]["coder"] = ["missing-profile"]
        source = self.root / "invalid-defaults.json"
        source.write_text(json.dumps(broken))
        target = self.root / "invalid-state"
        with patch.object(runtime, "DEFAULTS", source), self.assertRaisesRegex(ValueError, "known profiles"):
            initialize(target, self.workspace, ["owner:external"], co="owner", runtime=True)
        self.assertFalse((target / "team.json").exists())
        self.assertFalse((target / "live.sqlite3").exists())
        self.assertFalse((target / "roles.json").exists())

    def test_partial_operator_policy_is_never_overwritten_by_initialization(self):
        for filename in ("roles.json", "vendors.json"):
            with self.subTest(filename=filename):
                target = self.root / filename.removesuffix(".json")
                target.mkdir()
                original = b'{"operator": "existing partial configuration"}\n'
                (target / filename).write_bytes(original)
                with self.assertRaisesRegex(ValueError, "Runtime policy already exists"):
                    initialize(target, self.workspace, ["owner:external"], co="owner", runtime=True)
                self.assertEqual(original, (target / filename).read_bytes())
                self.assertEqual({filename}, {path.name for path in target.iterdir()})

    def test_reinitialization_preserves_existing_operator_policy(self):
        policy = self.enable_fixture_executables()
        name = policy["roles"]["coder"][0]
        policy["profiles"][name]["model"] = "operator-selected-model"
        (self.state / "roles.json").write_text(json.dumps(policy))
        before = {name: (self.state / name).read_bytes() for name in ("roles.json", "vendors.json", "team.json")}
        with self.assertRaisesRegex(ValueError, "Team already exists"):
            initialize(self.state, self.workspace, ["owner:external"], co="owner", runtime=True)
        self.assertEqual(before, {name: (self.state / name).read_bytes() for name in before})
        self.assertEqual("operator-selected-model", runtime.resolve(self.state, "coder")["model"])


if __name__ == "__main__":
    unittest.main()
