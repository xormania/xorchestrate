import hashlib
import json
import sqlite3
import sys
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fixtures.coordination import AssignmentFixture

from devtools.development import Development
from devtools.launch import team_config
from devtools.operations import execute
from devtools.workspaces import git


class WriteBoundaryTests(AssignmentFixture, unittest.TestCase):

    def assigned(self, paths=None):
        _, plan = self.plan()
        self.coder, self.assignment = self.implementation(plan)
        if paths is not None:
            self.assignment = self.assignment | {"editable_paths": paths}
            self.coordinator.call("orchestrate", {"work": [self.assignment]})
        self.workspace = Path(self.coder.workspaces.ensure()["root"])
        return self.workspace

    def maker(self, body):
        root = self.workspace
        (root / "composer.json").write_text("{}")
        (root / "composer.lock").write_text("{}")
        (root / ".gitignore").write_text("/vendor/\n/var/\n")
        (root / "vendor").mkdir(exist_ok=True)
        (root / "vendor/autoload.php").write_text("<?php")
        (root / "vendor/.agentscient-dependencies").write_text(hashlib.sha256(b"{}{}").hexdigest())
        git(root, "add", "composer.json", "composer.lock", ".gitignore")
        git(root, "commit", "-m", "Fixture installed dependencies")
        executable = self.root / "maintained-maker"
        executable.write_text("#!" + sys.executable + "\nimport json, sys\nfrom pathlib import Path\n"
                              "if 'help' in sys.argv:\n"
                              "    print(json.dumps({'name': 'make:controller', 'definition': {"
                              "'arguments': {}, 'options': {}}}))\nelse:\n"
                              + "\n".join("    " + line for line in body.splitlines()) + "\n")
        executable.chmod(0o755)
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["development"]["php"] = str(executable)
        (self.state / "team.json").write_text(json.dumps(config))

    def generate(self):
        with patch.object(self.coder.operations, "launch"):
            receipt = self.coder.call("framework_generate", {"maker": "make:controller"})
        ident = int(receipt["reference"].split(":")[1])
        execute(self.state, self.coder.member, ident)
        return self.coder.operations.get(ident)

    def workspace_state(self):
        with self.store.connect() as db:
            checkpoints = [tuple(row) for row in db.execute("SELECT * FROM checkpoints ORDER BY id")]
            artifacts = [tuple(row) for row in db.execute("SELECT * FROM artifacts ORDER BY id")]
        return (Development._files(self.workspace), git(self.workspace, "rev-parse", "HEAD").stdout,
                git(self.workspace, "ls-files", "--stage", "-z").stdout, checkpoints, artifacts)

    def revoke_during(self, effect):
        attempted, committed, errors = threading.Event(), threading.Event(), []
        blocked = []
        connect = self.coordinator.store.connect

        class Connection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, statement, *arguments):
                if statement == "BEGIN IMMEDIATE" and not attempted.is_set():
                    timeout = self.connection.execute("PRAGMA busy_timeout").fetchone()[0]
                    self.connection.execute("PRAGMA busy_timeout=0")
                    try:
                        result = self.connection.execute(statement, *arguments)
                    except sqlite3.OperationalError as error:
                        if "locked" not in str(error):
                            raise
                        blocked.append(True)
                    else:
                        blocked.append(False)
                        return result
                    finally:
                        self.connection.execute(f"PRAGMA busy_timeout={timeout}")
                        attempted.set()
                return self.connection.execute(statement, *arguments)

        @contextmanager
        def observe():
            with connect() as connection:
                yield Connection(connection) if threading.current_thread() is thread else connection

        def pause():
            try:
                self.coordinator.commands.steer(action="pause", work="Feature")
                committed.set()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=pause)
        self.revocation = thread, committed, errors
        self.addCleanup(thread.join, 3)
        # Registration already happened during assignment. Hold that immutable
        # configuration so the competing write is the actual revocation, not
        # _config's incidental member-registration UPSERT.
        with patch.object(self.coordinator.commands, "_config", return_value=team_config(self.state)), \
                patch.object(self.coordinator.store, "connect", observe):
            thread.start()
            self.assertTrue(attempted.wait(3), "Revocation did not attempt its write transaction")
            self.assertEqual([True], blocked, "Binding revocation must wait for the authorized source write")
            result = effect()
            self.assertFalse(committed.is_set())
            self.assertFalse(self.coder.control.snapshot()["paused"])
        return result

    def assert_revocation_completed(self):
        thread, committed, errors = self.revocation
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(committed.is_set())
        self.assertTrue(self.coder.control.snapshot()["paused"])

    def test_maker_denial_preserves_dirty_work_and_all_workspace_state(self):
        root = self.assigned()
        self.maker("Path('src/Example.php').write_text('generated')\n"
                   "path = Path('src/Controller/Outside.php')\n"
                   "path.parent.mkdir(parents=True)\npath.write_text('outside')")
        (root / "src/Example.php").write_text("existing unsaved work")
        before = self.workspace_state()
        result = self.generate()
        self.assertEqual("failed", result["state"])
        self.assertIn("src/Controller/Outside.php", result["error"])
        self.assertEqual(before, self.workspace_state())
        self.assertFalse((root / "src/Controller").exists())

    def test_maker_checks_protected_paths_even_for_a_broad_coder_assignment(self):
        self.assigned(["src/"])
        self.maker("path = Path('src/Maker/Unexpected.php')\n"
                   "path.parent.mkdir(parents=True)\npath.write_text('protected')")
        before = self.workspace_state()
        result = self.generate()
        self.assertEqual("failed", result["state"])
        self.assertIn("src/Maker/Unexpected.php", result["error"])
        self.assertEqual(before, self.workspace_state())

    def test_maker_promotes_allowed_edits_additions_deletions_and_renames_only(self):
        root = self.assigned(["src/"])
        self.maker("assert not Path('.git').exists()\n"
                   "Path('src/Example.php').write_text('generated')\n"
                   "Path('src/New.php').write_text('new')\n"
                   "Path('src/Deleted.php').unlink()\n"
                   "Path('src/Moved.php').rename('src/Renamed.php')\n"
                   "Path('var/cache').mkdir(parents=True)\nPath('var/cache/runtime').write_text('cache')")
        (root / "src/Deleted.php").write_text("delete")
        (root / "src/Moved.php").write_text("move")
        (root / "internal-link").symlink_to(root / "src/Example.php")
        git(root, "add", "src/Deleted.php", "src/Moved.php", "internal-link")
        git(root, "commit", "-m", "Fixture existing project files")
        head = git(root, "rev-parse", "HEAD").stdout
        result = self.generate()
        self.assertEqual("completed", result["state"], result)
        self.assertEqual([], result["result"]["result"]["outside_role_paths"])
        self.assertEqual("generated", (root / "src/Example.php").read_text())
        self.assertEqual("new", (root / "src/New.php").read_text())
        self.assertFalse((root / "src/Deleted.php").exists())
        self.assertFalse((root / "src/Moved.php").exists())
        self.assertEqual("move", (root / "src/Renamed.php").read_text())
        self.assertEqual(root / "src/Example.php", (root / "internal-link").readlink())
        self.assertFalse((root / "var").exists())
        self.assertEqual(head, git(root, "rev-parse", "HEAD").stdout)

    def test_maker_rejects_external_project_symlinks_before_execution(self):
        root = self.assigned()
        self.maker("Path('src/Example.php').write_text('generated')")
        (root / "external-link").symlink_to(self.checkout / "README.md")
        before = self.workspace_state()
        result = self.generate()
        self.assertEqual("failed", result["state"])
        self.assertIn("symlinks outside", result["error"])
        self.assertEqual(before, self.workspace_state())
        self.assertEqual("Example\n", (self.checkout / "README.md").read_text())

    def test_maker_discards_compiled_cache_which_retains_the_producer_project_path(self):
        root = self.assigned()
        self.maker("cache = Path('var/cache/container.json')\n"
                   "if cache.exists():\n"
                   "    destination = Path(json.loads(cache.read_text())['kernel.project_dir']) / 'src/Outside.php'\n"
                   "else:\n"
                   "    destination = Path('src/Example.php')\n"
                   "destination.write_text('generated')")
        (root / "var/cache").mkdir(parents=True)
        compiled = root / "var/cache/container.json"
        compiled.write_text(json.dumps({"kernel.project_dir": str(root)}))
        cached = compiled.read_bytes()
        result = self.generate()
        self.assertEqual("completed", result["state"], result)
        self.assertEqual("generated", (root / "src/Example.php").read_text())
        self.assertFalse((root / "src/Outside.php").exists())
        self.assertEqual(cached, compiled.read_bytes())

    def test_maker_rechecks_the_operation_binding_before_promotion(self):
        self.assigned()
        self.maker("Path('src/Example.php').write_text('generated')")
        before = self.workspace_state()
        run = Development.run

        def pause_after_generation(service, root, argv, **kwargs):
            result = run(service, root, argv, **kwargs)
            if "make:controller" in argv and "help" not in argv:
                self.coordinator.call("steer", {"action": "pause", "work": "Feature"})
            return result

        with patch.object(Development, "run", pause_after_generation):
            result = self.generate()
        self.assertEqual("failed", result["state"])
        self.assertIn("paused", result["error"])
        self.assertEqual(before, self.workspace_state())

    def test_maker_source_effects_finish_before_concurrent_binding_revocation(self):
        root = self.assigned()
        self.maker("Path('src/Example.php').write_text('generated')")
        promote = Development._promote

        def revoke_at_promotion(*args):
            return self.revoke_during(lambda: promote(*args))

        with patch.object(Development, "_promote", staticmethod(revoke_at_promotion)):
            result = self.generate()
        self.assert_revocation_completed()
        self.assertEqual("completed", result["state"], result)
        self.assertEqual("generated", (root / "src/Example.php").read_text())

    def test_failed_maker_promotion_rolls_back_files_and_new_directories(self):
        root = self.assigned(["src/"])
        self.maker("Path('src/Example.php').write_text('generated')\n"
                   "Path('src/NewDirectory').mkdir()\nPath('src/NewDirectory/Added.php').write_text('new')\n"
                   "Path('src/Other.php').write_text('replacement')")
        (root / "src/Other.php").write_text("existing")
        git(root, "add", "src/Other.php")
        git(root, "commit", "-m", "Fixture second generated target")
        before = self.workspace_state()
        import os

        replace = os.replace
        def fail_one_promotion(source, target):
            if Path(target) == root / "src/Other.php":
                raise PermissionError("Fixture promotion failure")
            return replace(source, target)

        with patch("devtools.development.os.replace", fail_one_promotion):
            result = self.generate()
        self.assertEqual("failed", result["state"])
        self.assertIn("Fixture promotion failure", result["error"])
        self.assertEqual(before, self.workspace_state())
        self.assertFalse((root / "src/NewDirectory").exists())

    def test_maker_reports_rollback_failure_and_still_restores_other_targets(self):
        root = self.assigned(["src/"])
        self.maker("Path('src/Example.php').write_text('generated')\n"
                   "Path('src/Middle.php').write_text('generated middle')\n"
                   "Path('src/Other.php').write_text('generated other')")
        (root / "src/Middle.php").write_text("original middle")
        (root / "src/Other.php").write_text("original other")
        git(root, "add", "src/Middle.php", "src/Other.php")
        git(root, "commit", "-m", "Fixture rollback targets")
        import os

        replace = os.replace
        rolling_back = False

        def fail_promotion_and_one_restore(source, target):
            nonlocal rolling_back
            if Path(target) == root / "src/Other.php":
                rolling_back = True
                raise PermissionError("Fixture promotion failure")
            if rolling_back and Path(target) == root / "src/Middle.php":
                raise PermissionError("Fixture rollback failure")
            return replace(source, target)

        with patch("devtools.development.os.replace", fail_promotion_and_one_restore):
            result = self.generate()
        self.assertEqual("failed", result["state"])
        self.assertIn("could not be rolled back: src/Middle.php", result["error"])
        self.assertEqual("original\n", (root / "src/Example.php").read_text())
        self.assertEqual("original other", (root / "src/Other.php").read_text())
        self.assertEqual("generated middle", (root / "src/Middle.php").read_text())

    def test_docs_recovery_cannot_revert_completed_coder_source(self):
        root = self.assigned()
        checkpoint = self.coder.call("work_save", {"note": "Original checkpoint"})["checkpoint"]
        (root / "src/Example.php").write_text("Implemented source")
        self.coder.call("work_save", {"note": "Implement assigned source"})
        self.coder.call("work_report", {"kind": "result", "text": "Implementation saved"})
        self.coordinator.call("steer", {"action": "complete", "work": "Feature", "assignment": "Feature",
                                        "direction": "Implementation checked"})
        self.coordinator.call("orchestrate", {"work": [{"name": "Documentation", "job": "Feature", "role": "docs"}]})
        docs = self.by_role("docs")
        before = self.workspace_state()
        with self.assertRaisesRegex(ValueError, "Recovery would change.*src/Example.php"):
            docs.call("work_recover", {"checkpoint": checkpoint, "reason": "Restore an earlier checkpoint"})
        self.assertEqual(before, self.workspace_state())

    def test_recovery_validates_both_ends_of_a_saved_rename_before_preserving(self):
        root = self.assigned(["src/"])
        (root / "src/Previous.php").write_text("existing")
        first = self.coder.call("work_save", {"note": "Before rename"})["checkpoint"]
        (root / "src/Previous.php").rename(root / "src/Next.php")
        self.coder.call("work_save", {"note": "Rename assigned source"})
        self.coordinator.call("orchestrate", {"work": [self.assignment | {"editable_paths": ["src/Next.php"]}]})
        before = self.workspace_state()
        with self.assertRaisesRegex(ValueError, "Recovery would change.*src/Previous.php"):
            self.coder.call("work_recover", {"checkpoint": first, "reason": "Revert rename"})
        self.assertEqual(before, self.workspace_state())

    def test_recovery_restores_only_allowed_files_and_preserves_unsaved_history(self):
        root = self.assigned()
        first = self.coder.call("work_save", {"note": "Original checkpoint"})["checkpoint"]
        (root / "src/Example.php").write_text("Saved implementation")
        saved = self.coder.call("work_save", {"note": "Implement"})["checkpoint"]
        (root / "src/Example.php").write_text("Unsaved implementation")
        result = self.coder.call("work_recover", {"checkpoint": first, "reason": "Restore original"})
        self.assertEqual("original\n", (root / "src/Example.php").read_text())
        self.assertEqual("Example\n", (root / "README.md").read_text())
        self.assertEqual(0, git(root, "merge-base", "--is-ancestor", saved.split(":")[1], "HEAD").returncode)
        self.assertIn("Unsaved implementation", git(root, "show", result["preserved"]["checkpoint"].split(":")[1]
                                                   + ":src/Example.php").stdout.decode())

    def test_recovery_source_effects_finish_before_concurrent_binding_revocation(self):
        root = self.assigned()
        first = self.coder.call("work_save", {"note": "Original checkpoint"})["checkpoint"]
        (root / "src/Example.php").write_text("Implemented source")
        self.coder.call("work_save", {"note": "Implement"})

        def revoke_at_restore(where, *args, **kwargs):
            if args and args[0] == "restore":
                return self.revoke_during(lambda: git(where, *args, **kwargs))
            return git(where, *args, **kwargs)

        with patch("devtools.workspaces.git", revoke_at_restore):
            try:
                self.coder.call("work_recover", {"checkpoint": first, "reason": "Restore original"})
            except ValueError as error:
                # A pause which commits after the authorized restore may stop
                # the subsequent checkpoint recording, but cannot precede it.
                self.assertIn("paused", str(error))
        self.assert_revocation_completed()
        self.assertEqual("original\n", (root / "src/Example.php").read_text())

    def test_co_development_services_reject_before_workspaces_queueing_or_processes(self):
        root = self.assigned()
        service = Development(self.store, "co")
        from devtools.operations import Operations

        operations = Operations(self.store, "co")
        for legacy_job in (None, self.coder.control.snapshot()["job"]):
            # A migrated legacy row must not gain capabilities by bypassing the
            # normal bind_in guard. The normal CO binding is checked first.
            with self.store.connect() as db:
                db.execute("UPDATE member_bindings SET job=? WHERE member='co'", (legacy_job,))
            before = self.workspace_state()
            with patch.object(service.workspaces, "use") as workspace_use, \
                    patch("devtools.development.subprocess.Popen") as process, \
                    patch.object(operations, "launch") as launch:
                for call in (lambda: service.run(root, ["php", "bin/console"]),
                             lambda: service.prepare(root), lambda: service.inspect("makers"),
                             lambda: service.checks(root), lambda: service.check()):
                    with self.assertRaisesRegex(ValueError, "CO hands work to XO"):
                        call()
                for action in ("framework_inspect", "framework_generate", "work_check", "work_submit"):
                    with self.assertRaisesRegex(ValueError, "corresponding role"):
                        operations.start(action, {})
                workspace_use.assert_not_called()
                process.assert_not_called()
                launch.assert_not_called()
            with self.store.connect() as db:
                self.assertEqual(0, db.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
            self.assertEqual(before, self.workspace_state())


if __name__ == "__main__":
    unittest.main()
