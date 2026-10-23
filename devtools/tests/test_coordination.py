import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.commands import responsibility
from fixtures.coordination import AssignmentFixture

from devtools.assignments import covers, policy, valid_path
from devtools.development import Development
from devtools.role_packages import catalog
from devtools.runtime import defaults, load, resolve
from devtools.serena_provider import Serena
from devtools.store import SCHEMA_VERSION, Store
from devtools.workspaces import git


class AssignmentBoundaryTests(AssignmentFixture, unittest.TestCase):


    def test_curated_serena_mounts_and_changed_assignment_restart_view(self):
        _, result = self.plan()
        coder, item = self.implementation(result)
        coder.call("work_inspect", {})
        root = Path(coder.workspaces.ensure()["root"])
        provider = Serena(self.state, coder.member)
        scope, signature = provider.scope()
        args = provider.command(scope, coder.workspaces.ensure())
        mounts = [args[i + 1] for i, token in enumerate(args[:-1]) if token == "--mount"]
        self.assertNotIn(f"type=bind,src={root},dst=/workspace,readonly", mounts)
        self.assertIn(f"type=bind,src={root}/README.md,dst=/workspace/README.md,readonly", mounts)
        self.assertIn(f"type=bind,src={root}/src/Example.php,dst=/workspace/src/Example.php", mounts)
        targets = [next(part for part in value.split(",") if part.startswith("dst=")) for value in mounts]
        self.assertEqual(len(targets), len(set(targets)))
        (root / "src/Other.php").write_text("Out of scope")
        with self.assertRaisesRegex(ValueError, "outside this role"):
            coder.call("work_save", {"note": "Extra change"})
        provider.signature = signature
        self.coordinator.call("orchestrate", {"work": [item | {"editable_paths": ["src/Other.php"]}]})
        with self.assertRaisesRegex(ValueError, "Reconnect"):
            provider.scope()

    def test_replan_narrows_existing_editor_and_invalidates_previous_mounts(self):
        _, report = self.plan()
        coder, _ = self.implementation(report)
        root = Path(coder.workspaces.ensure()["root"])
        provider = Serena(self.state, coder.member)
        _, signature = provider.scope()
        provider.signature = signature
        self.coordinator.call("plan_adopt", {"plan": report["reference"], "jobs": [{
            "name": "Feature", "objective": "Feature", "done_when": "Works", "constraints": "Product only",
            "editable_paths": ["src/Narrow.php"]}]})
        with self.assertRaisesRegex(ValueError, "paused|Reconnect"):
            provider.scope()
        self.coordinator.call("steer", {"action": "resume", "work": "Feature"})
        self.assertEqual([], coder.call("work_context", {})["development"]["editable_paths"])
        (root / "src/Example.php").write_text("Old wider permission must not survive")
        with self.assertRaisesRegex(ValueError, "outside this role"):
            coder.call("work_save", {"note": "Try the old assignment"})

    def test_default_planned_scope_does_not_materialize_policy_paths(self):
        self.plan()
        self.coordinator.call("orchestrate", {"work": [responsibility("Feature")]})
        self.coordinator.call("steer", {"action": "resume", "work": "Feature"})
        coder = self.by_role("coder")
        workspace = coder.workspaces.ensure()
        provider = Serena(self.state, coder.member)
        provider.command(coder.control.snapshot(), workspace)
        self.assertEqual(b"", git(workspace["root"], "status", "--porcelain").stdout)

    def test_maker_protection_and_operator_policy_are_reused(self):
        self.api.call("work_inspect", {})
        root = Path(self.api.workspaces.ensure()["root"])
        provider = Serena(self.state, self.api.member)
        rules = policy(self.store, {})
        rules["protected_paths"] += ["src/Maker/PlannedMaker.php", "src/Reserved.php"]
        (self.state / "assignment-policy.json").write_text(json.dumps(rules))
        team = json.loads((self.state / "team.json").read_text())
        team["project"]["assignment_policy"] = "assignment-policy.json"
        (self.state / "team.json").write_text(json.dumps(team))
        args = provider.command(self.api.control.snapshot(), self.api.workspaces.ensure())
        self.assertFalse((root / "src/Maker").exists())
        self.assertFalse((root / "src/Reserved.php").exists())
        self.assertEqual(b"", git(root, "status", "--porcelain").stdout)
        self.assertIn(f"type=bind,src={root},dst=/workspace,readonly", args)
        self.assertIn(f"type=bind,src={root}/src/Example.php,dst=/workspace/src/Example.php", args)
        self.assertNotIn(f"type=bind,src={root}/src,dst=/workspace/src", args)
        (root / "src/Example.php").write_text("Allowed edit")
        self.api.call("work_save", {"note": "Save without phantom protected files"})
        (root / "src/Maker").mkdir()
        (root / "src/Maker/Forbidden.php").write_text("Maker code")
        with self.assertRaisesRegex(ValueError, "outside this role"):
            self.api.call("work_save", {"note": "Change a Maker"})
        with patch.object(Development, "prepare") as prepare, self.assertRaisesRegex(ValueError, "maintained Makers"):
            Development(self.store, self.api.member).generate("make:maker")
        prepare.assert_not_called()
        with self.assertRaisesRegex(ValueError, "exclude Makers"):
            self.coordinator.call("orchestrate", {"work": [responsibility("Feature") | {"editable_paths": ["src/Maker"]}]})

    def test_assignment_changes_wait_for_active_workspace_operations(self):
        _, result = self.plan()
        coder, item = self.implementation(result)
        before = coder.control.snapshot()
        with coder.workspaces.lock("job-" + str(before["job"])), self.assertRaisesRegex(ValueError, "Another operation"):
            self.coordinator.call("orchestrate", {"work": [item | {"editable_paths": ["src/Other.php"]}]})
        self.assertEqual(before, coder.control.snapshot())
        self.assertEqual(["src/Example.php"], coder.call("work_context", {})["assignment"]["editable_paths"])

    def test_replaced_file_and_new_generated_file_invalidate_curated_mounts(self):
        _, result = self.plan()
        coder, _ = self.implementation(result)
        workspace = coder.workspaces.ensure()
        root = Path(workspace["root"])
        provider = Serena(self.state, coder.member)
        scope, signature = provider.scope()
        provider.command(scope, workspace)
        replacement = root / "src/replacement.php"
        replacement.write_text("<?php // Maker or Git replaced the file")
        replacement.replace(root / "src/Example.php")
        provider.signature, provider.process = signature, object()
        def close():
            provider.process = None
        with patch.object(provider, "close", side_effect=close) as closed, patch.object(provider, "request") as request:
            with self.assertRaisesRegex(ValueError, "paths changed"):
                provider.call({"code": "s.info()"})
            closed.assert_called_once()
            request.assert_not_called()
        missing = root / "src/Example.php"
        missing.unlink()
        provider.command(scope, workspace)
        previous = provider.mount_layout
        self.assertTrue(missing.is_file())
        replacement.write_text("<?php // Generated")
        replacement.replace(missing)
        self.assertNotEqual(previous, provider.layout(scope, workspace))

    def test_new_assigned_files_and_explicit_directories_are_writable(self):
        _, result = self.plan()
        coder, item = self.implementation(result)
        paths = ["src/NewService.php", "src/NOTICE", "src/NewDirectory/"]
        self.coordinator.call("orchestrate", {"work": [item | {"editable_paths": paths}]})
        workspace = coder.workspaces.ensure()
        root = Path(workspace["root"])
        provider = Serena(self.state, coder.member)
        args = provider.command(coder.control.snapshot(), workspace)
        for path in paths:
            normalized = path.rstrip("/")
            self.assertIn(f"type=bind,src={root}/{normalized},dst=/workspace/{normalized}", args)
            self.assertEqual(path.endswith("/"), (root / path).is_dir())
        self.assertNotIn(f"type=bind,src={root}/src,dst=/workspace/src", args)
        (root / paths[0]).write_text("<?php // Assigned service")
        (root / paths[1]).write_text("Extensionless file")
        (root / paths[2] / "Child.php").write_text("<?php // Assigned directory")
        coder.call("work_save", {"note": "Create assigned files"})
        self.assertTrue(covers("src/", "src/NOTICE"))
        for path in ("src//", "src/../", "src/./", "/src/", "src/.env/", "src/.git/"):
            self.assertFalse(valid_path(path), path)
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.coordinator.call("orchestrate", {"work": [item | {"editable_paths": ["src/NewDirectory", "src/NewDirectory/"]}]})

    def test_generation_adds_a_writable_child_without_unprotecting_its_parent(self):
        provider = Serena(self.state, self.api.member)
        workspace = self.api.workspaces.ensure()
        scope = self.api.control.snapshot()
        provider.command(scope, workspace)
        before = provider.mount_layout
        root = Path(workspace["root"])
        (root / "src/Generated.php").write_text("<?php // Generated through MCP")
        self.assertNotEqual(before, provider.layout(scope, workspace))
        args = provider.command(scope, workspace)
        self.assertIn(f"type=bind,src={root}/src/Generated.php,dst=/workspace/src/Generated.php", args)
        self.assertNotIn(f"type=bind,src={root}/src,dst=/workspace/src", args)

    def test_existing_role_policy_remains_usable_without_implicitly_enabling_new_roles(self):
        defaults(self.state)
        path = self.state / "roles.json"
        value = json.loads(path.read_text())
        for role in ("nco", "planner"):
            del value["roles"][role]
        path.write_text(json.dumps(value))
        self.assertEqual(set(catalog()["roles"]) - {"nco", "planner"}, set(load(self.state)[0]["roles"]))
        with self.assertRaisesRegex(ValueError, "roles.json"):
            resolve(self.state, "planner")

    def test_upgrade_from_jev_state_preserves_reports_and_bindings(self):
        before = self.coordinator.call("work_report", {"text": "Coordination state"})
        scope = self.coordinator.control.snapshot()
        with self.store.connect() as db:
            db.execute("ALTER TABLE jobs DROP COLUMN assignment")
            db.execute("ALTER TABLE work_updates DROP COLUMN binding")
            db.execute("ALTER TABLE work_updates DROP COLUMN mission")
            db.execute("ALTER TABLE deliveries DROP COLUMN result_update")
            db.execute("PRAGMA user_version=10")
        store = Store(self.state)
        with store.connect() as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(scope, self.coordinator.control.snapshot())
        retry = self.coordinator.call("work_report", {"text": "Coordination state"})
        self.assertEqual(before["reference"], retry["reference"])
        self.assertTrue(retry["reused"])

    def test_native_introspection_uses_installed_console_metadata_and_operator_policy(self):
        self.coordinator.call("orchestrate", {"work": [responsibility("Plan", "planner")]})
        planner = self.by_role("planner")
        # Symfony Console 8.1 JsonDescriptor: command list plus named argument/option maps.
        command = {"name": "debug:container", "description": "Display current services", "usage": [], "help": "Inspect services", "hidden": False,
                   "definition": {"arguments": {"name": {"name": "name", "is_required": False, "is_array": False, "description": "Service", "default": None}},
                                  "options": {"show-hidden": {"name": "--show-hidden", "shortcut": "", "accept_value": False,
                                                              "is_value_required": False, "is_multiple": False, "description": "Include hidden services", "default": False}}}}
        provider = Development(self.store, planner.member)
        with patch.object(provider, "prepare"), patch.object(provider, "_json", side_effect=[{"commands": [command]}, command]), \
                patch.object(provider, "run", return_value={"exit_code": 0, "output": "services"}) as run:
            provider.inspect("console", "debug:container", {"arguments": {"name": "App\\Service\\Probe"}, "options": {"show-hidden": True}})
        self.assertEqual(["php", "bin/console", "debug:container", "--no-interaction", "--no-ansi", "--show-hidden", "--", "App\\Service\\Probe"], run.call_args.args[1])
        with patch.object(provider, "prepare"), patch.object(provider, "_json", return_value={"commands": [command]}), \
                self.assertRaisesRegex(ValueError, "inspection_commands"):
            provider.inspect("console", "cache:clear", {})
