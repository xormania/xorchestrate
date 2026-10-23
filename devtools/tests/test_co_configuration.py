"""CO launch choices honor the current configured identity before registration."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import initialize, runtime_policy

from devtools import adapters, console, role_packages
from devtools import run as entry
from devtools.api import API
from devtools.control import Control
from devtools.store import Store


class COConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.state = self.root / "state"
        initialize(self.state, self.workspace, ["owner:external"], co="owner", runtime=True)
        self.store = Store(self.state)
        self.packages = copy.deepcopy(role_packages.catalog())
        configured = patch.object(role_packages, "_catalog", return_value=self.packages)
        configured.start()
        self.addCleanup(configured.stop)
        self.policy = runtime_policy()
        for profile in self.policy["profiles"].values():
            profile.update(binary=sys.executable, model="approved", reasoning="low")
        self.write_policy()
        vendors = json.loads((self.state / "vendors.json").read_text())
        for vendor in vendors["vendors"].values():
            vendor["available"] = True
        (self.state / "vendors.json").write_text(json.dumps(vendors))
        self.private = self.root / "private"
        self.private.mkdir()

    def write_policy(self):
        (self.state / "roles.json").write_text(json.dumps(self.policy))

    def bind(self, role):
        api = API(self.state, "owner", operator=True)
        order = api.work.work_order_create(title="CO configuration", objective="Preserve the current interface",
                                           done_when="Launch uses its configured role")["work_order"]["id"]
        api.control.bind("owner", role, order)
        return Control(self.store, "owner").snapshot()

    def launch(self, **arguments):
        # Exercise resolution, registration and retained runtime snapshots without
        # opening a native process or reading the operator's credentials.
        with patch.object(console, "prepare", return_value=(self.private, {}, {})), \
                patch.object(console, "command", return_value=(["fixture"], "fixture-session")), \
                patch.object(console, "relay", return_value=0), patch.object(console, "retain_login"):
            self.assertEqual(0, console.run(self.state, **arguments))
        return self.store.events(kind="session_starting")["events"][-1]["data"]["settings"]

    def test_initial_unbound_co_uses_configured_default_identity(self):
        self.packages["roles"]["interface"] = self.packages["roles"].pop("co")
        self.packages["defaults"]["co"] = "interface"
        self.policy["roles"]["interface"] = self.policy["roles"].pop("co")
        self.write_policy()
        self.assertIsNone(Control(self.store, "owner").snapshot())
        selected = self.launch()
        self.assertEqual("interface", selected["selection"]["role"])
        self.assertEqual("interface", selected["role_package"]["role"])
        self.assertIsNone(Control(self.store, "owner").snapshot())

    def test_initial_prompt_reaches_command_only_and_is_not_a_runtime_setting(self):
        brief = "Greeting acceptance\nUnicode: café\n"
        with patch.object(console, "prepare", return_value=(self.private, {}, {})) as prepare, \
                patch.object(console, "command", return_value=(["fixture"], "fixture-session")) as command, \
                patch.object(console, "relay", return_value=0), patch.object(console, "retain_login"):
            self.assertEqual(0, console.run(self.state, initial_prompt=brief))
        self.assertEqual(brief, command.call_args.args[2]["initial_prompt"])
        self.assertNotIn("initial_prompt", prepare.call_args.kwargs["config"])
        selected = self.store.events(kind="session_starting")["events"][-1]["data"]["settings"]
        self.assertNotIn("initial_prompt", selected)
        self.assertNotIn(brief, (self.state / "roles.json").read_text())

    def test_invalid_initial_prompt_is_rejected_before_runtime_or_session_allocation(self):
        before = self.store.member("owner")
        for brief in ("", " \n", "nul\0byte", b"bytes", "\ud800", "é" * 32769):
            with self.subTest(brief=repr(brief[:20])), patch.object(console, "resolve") as resolve, \
                    patch.object(console, "prepare") as prepare, self.assertRaisesRegex(ValueError, "brief"):
                console.run(self.state, initial_prompt=brief)
            resolve.assert_not_called()
            prepare.assert_not_called()
            self.assertEqual(before, self.store.member("owner"))
        self.assertEqual([], self.store.events(kind="session_starting")["events"])

    def test_unsupported_harness_brief_is_rejected_before_session_allocation(self):
        from devtools.harnesses import CodexHarness

        before = self.store.member("owner")
        with patch.object(CodexHarness, "supports_initial_prompt", False), \
                patch.object(console, "prepare") as prepare, \
                self.assertRaisesRegex(ValueError, "does not support an initial CO brief"):
            console.run(self.state, initial_prompt="Run the acceptance work order.")
        prepare.assert_not_called()
        self.assertEqual(before, self.store.member("owner"))
        self.assertEqual([], self.store.events(kind="session_starting")["events"])

    def test_brief_preflight_checks_selected_custom_adapter_and_vendor_availability(self):
        manifest = self.root / "adapters.json"
        document = json.loads(adapters.MANIFEST.read_text())
        document["adapters"]["harness"]["fixture"] = "devtools.tests.harness_plugin_fixture:FixtureHarness"
        manifest.write_text(json.dumps(document))
        self.packages["roles"]["co"]["runtime"]["harnesses"].append("fixture")
        self.policy["profiles"]["custom"] = {"harness": "fixture", "vendor": "fixture-vendor", "binary": sys.executable}
        self.policy["roles"]["co"] = ["custom", "codex"]
        self.write_policy()
        vendors_file = self.state / "vendors.json"
        vendors = json.loads(vendors_file.read_text())
        vendors["vendors"]["fixture-vendor"] = {"available": True}
        vendors_file.write_text(json.dumps(vendors))
        before = self.store.member("owner")
        with patch.object(adapters, "MANIFEST", manifest), patch.object(console, "prepare") as prepare, \
                patch.object(console, "relay") as relay:
            for profile in (None, "custom"):
                with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "initial CO brief: fixture"):
                    console.initial_prompt_profile(self.state, profile=profile)
            self.assertEqual("codex", console.initial_prompt_profile(self.state, profile="codex")["harness"])
            vendors["vendors"]["fixture-vendor"]["available"] = False
            vendors_file.write_text(json.dumps(vendors))
            self.assertEqual("codex", console.initial_prompt_profile(self.state)["harness"])
        prepare.assert_not_called()
        relay.assert_not_called()
        self.assertEqual(before, self.store.member("owner"))
        self.assertEqual([], self.store.events(kind="session_starting")["events"])

    def test_cli_brief_file_preserves_utf8_and_rejects_invalid_files_before_launch(self):
        brief = self.root / "brief.txt"
        content = "--Greeting\r\nUnicode: café\n"
        brief.write_bytes(content.encode("utf-8"))
        arguments = ["run.py", "co", "--state", str(self.state), "--brief-file", str(brief)]
        with patch.object(sys, "argv", arguments), patch.object(console, "run", return_value=0) as run:
            self.assertEqual(0, entry.main())
        run.assert_called_once_with(str(self.state), None, None, None, initial_prompt=content)
        for payload in (b"", b"  \n", b"\xff", b"nul\0byte", b"x" * 65537):
            brief.write_bytes(payload)
            with self.subTest(payload=repr(payload[:20])), patch.object(sys, "argv", arguments), \
                    patch.object(console, "run") as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, entry.main())
            run.assert_not_called()
        brief.unlink()
        with patch.object(sys, "argv", arguments), patch.object(console, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, entry.main())
        run.assert_not_called()

    def test_existing_bound_co_uses_its_identity_and_profile_preferences(self):
        self.packages["roles"]["liaison"] = copy.deepcopy(self.packages["roles"]["co"])
        self.packages["roles"]["liaison"]["runtime"]["harnesses"] = ["claude"]
        self.policy["roles"]["liaison"] = ["claude"]
        self.write_policy()
        scope = self.bind("liaison")
        selected = self.launch()
        self.assertEqual("co", self.packages["defaults"]["co"])
        self.assertEqual("liaison", selected["selection"]["role"])
        self.assertEqual("liaison", selected["role_package"]["role"])
        self.assertEqual("claude", selected["harness"])
        self.assertEqual(scope, Control(self.store, "owner").snapshot())
        before = self.store.member("owner")
        with patch.object(console, "prepare") as prepare, self.assertRaisesRegex(ValueError, "configured preferences"):
            console.run(self.state, profile="codex")
        prepare.assert_not_called()
        self.assertEqual(before, self.store.member("owner"))

    def test_model_and_reasoning_overrides_are_checked_before_session_registration(self):
        runtime = self.packages["roles"]["co"]["runtime"]
        runtime["model"] = {"default": None, "allowed": ["approved", "alternate"]}
        runtime["reasoning"] = {"default": None, "allowed": ["low", "medium"]}
        selected = self.launch(model="alternate", reasoning="medium")
        self.assertEqual(("alternate", "medium"), (selected["model"], selected["reasoning"]))
        self.assertEqual("approved", json.loads((self.state / "roles.json").read_text())["profiles"]["codex"]["model"])
        before = self.store.member("owner")
        starts = self.store.events(kind="session_starting")["events"]
        for arguments in ({"model": "forbidden"}, {"reasoning": "high"}):
            with self.subTest(arguments=arguments), patch.object(console, "prepare") as prepare, \
                    self.assertRaisesRegex(ValueError, "explicit permitted"):
                console.run(self.state, **arguments)
            prepare.assert_not_called()
            self.assertEqual(before, self.store.member("owner"))
            self.assertEqual(starts, self.store.events(kind="session_starting")["events"])

    def test_non_co_binding_is_rejected_before_runtime_resolution_or_registration(self):
        # Trusted repair can bind this appointed member differently; launching the
        # CO must not relabel that live responsibility as the default interface.
        self.bind("co")
        with self.store.connect() as db:
            db.execute("UPDATE member_bindings SET role='coder' WHERE member='owner'")
        before = self.store.member("owner")
        with patch.object(console, "resolve") as resolve, patch.object(console, "prepare") as prepare, \
                self.assertRaisesRegex(ValueError, "current binding must use the CO behavior"):
            console.run(self.state)
        resolve.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(before, self.store.member("owner"))
        self.assertEqual([], self.store.events(kind="session_starting")["events"])

    def test_binding_changes_during_resolution_prevent_session_registration(self):
        self.packages["roles"]["liaison"] = copy.deepcopy(self.packages["roles"]["co"])
        self.bind("co")
        resolve = console.resolve
        # Check the whole snapshot: trusted rebinding, a newer direction, and a
        # pause each invalidate the runtime decision, even with the same harness.
        for mutation in ("role='liaison'", "revision=revision+1", "paused=1"):
            with self.subTest(mutation=mutation):
                with self.store.connect() as db:
                    db.execute("UPDATE member_bindings SET role='co',paused=0 WHERE member='owner'")
                before = self.store.member("owner")

                def rebind(*args, **kwargs):
                    config = resolve(*args, **kwargs)
                    with self.store.connect() as db:
                        db.execute("UPDATE member_bindings SET " + mutation + " WHERE member='owner'")
                    return config

                with patch.object(console, "resolve", side_effect=rebind), patch.object(console, "prepare") as prepare, \
                        self.assertRaisesRegex(ValueError, "Work binding changed"):
                    console.run(self.state)
                prepare.assert_not_called()
                self.assertEqual(before, self.store.member("owner"))
                self.assertEqual([], self.store.events(kind="session_starting")["events"])

    def test_initial_binding_created_during_resolution_prevents_session_registration(self):
        self.assertIsNone(Control(self.store, "owner").snapshot())
        resolve = console.resolve
        bound = []

        def bind(*args, **kwargs):
            config = resolve(*args, **kwargs)
            bound.append(self.bind("co"))
            return config

        with patch.object(console, "resolve", side_effect=bind), patch.object(console, "prepare") as prepare, \
                self.assertRaisesRegex(ValueError, "Work binding changed"):
            console.run(self.state)
        prepare.assert_not_called()
        self.assertEqual(bound[0], Control(self.store, "owner").snapshot())
        self.assertIsNone(self.store.member("owner")["generation"])
        self.assertEqual([], self.store.events(kind="session_starting")["events"])


if __name__ == "__main__":
    unittest.main()
