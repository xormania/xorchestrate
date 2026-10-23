"""Planner diagnostics may retain evidence but cannot change integrated source."""

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.commands import responsibility
from fixtures.workspaces import WorkspaceFixture

from devtools.api import API
from devtools.development import Development
from devtools.operations import Operations, execute
from devtools.workspaces import git


class PlannerDiagnosticTests(unittest.TestCase):
    def setUp(self):
        WorkspaceFixture.setUp(self)
        (self.checkout / "composer.json").write_text("{}")
        (self.checkout / "composer.lock").write_text("{}")
        (self.checkout / "src/Remove.php").write_text("retained source\n")
        (self.checkout / ".gitignore").write_text("/vendor/\n/var/\n/diagnostic-*.txt\n/absolute-link\n")
        git(self.checkout, "add", ".")
        git(self.checkout, "commit", "-m", "Planner diagnostic fixture")
        git(self.checkout, "push", "origin", "dev")
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["development"] = {"checks": {"fixture": [sys.executable, "-c", "print('measured')"]}}
        (self.state / "team.json").write_text(json.dumps(config))
        xo = API(self.state, "xo")
        xo.call("orchestrate", {"work": [responsibility("Investigate", "planner")]})
        with self.store.connect() as db:
            member = db.execute("SELECT member FROM member_bindings WHERE role='planner'").fetchone()[0]
        self.planner = API(self.state, member)
        self.workspace = Path(self.planner.workspaces.ensure()["root"])
        self.revision = git(self.workspace, "rev-parse", "HEAD").stdout.decode().strip()
        (self.workspace / "diagnostic-existing.txt").write_text("retained untracked work\n")
        (self.workspace / "vendor").mkdir()
        (self.workspace / "vendor/autoload.php").write_text("<?php")
        (self.workspace / "vendor/.agentscient-dependencies").write_text(hashlib.sha256(b"{}{}").hexdigest())

    def configure(self, **values):
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["development"].update(values)
        (self.state / "team.json").write_text(json.dumps(config))

    def executable(self, name, body):
        path = self.root / name
        path.write_text("#!" + sys.executable + "\nimport json, subprocess, sys\nfrom pathlib import Path\n" + body + "\n")
        path.chmod(0o755)
        return str(path)

    @staticmethod
    def mutations():
        return (
            "Path('src/Example.php').write_text('diagnostic edit')\n"
            "Path('src/Remove.php').unlink()\n"
            "Path('src/Added.php').write_text('diagnostic addition')\n"
            "Path('diagnostic-existing.txt').unlink()\n"
            "Path('diagnostic-created.txt').write_text('diagnostic untracked addition')\n"
            "subprocess.run(['git', 'add', 'src/Example.php'], capture_output=True)\n"
            "subprocess.run(['git', '-c', 'user.name=Diagnostic fixture', '-c', 'user.email=fixture@example.invalid', "
            "'commit', '-m', 'Disposable diagnostic mutation'], capture_output=True, check=True)\n"
            "subprocess.run(['git', 'update-ref', 'refs/heads/diagnostic-leak', 'HEAD'], capture_output=True)\n"
        )

    def source_state(self):
        return (
            Development._files(self.workspace),
            Development._files(self.checkout),
            git(self.workspace, "rev-parse", "HEAD").stdout,
            git(self.workspace, "ls-files", "--stage", "-z").stdout,
            git(self.workspace, "status", "--porcelain=v1", "-z", "--ignored").stdout,
            git(self.checkout, "show-ref").stdout,
        )

    def operation(self, action, arguments):
        with patch.object(Operations, "launch"):
            receipt = self.planner.call(action, arguments)
        execute(self.state, self.planner.member, int(receipt["reference"].split(":")[1]))
        details = json.loads(self.planner.call("work_context", {"reference": receipt["reference"]})["content"])
        return details

    def check_writes(self, exit_code):
        program = "import subprocess\nfrom pathlib import Path\n" + self.mutations()
        program += f"print('measured planner diagnostic')\nraise SystemExit({exit_code})\n"
        self.configure(checks={"fixture": [sys.executable, "-c", program]})
        before = self.source_state()
        details = self.operation("work_check", {})
        self.assertEqual(before, self.source_state())
        self.assertEqual("completed", details["state"], details)
        result = details["result"]
        self.assertEqual(exit_code == 0, result["passed"])
        self.assertEqual(self.revision, result["revision"])
        check = result["checks"][0]
        self.assertEqual(exit_code, check["exit_code"])
        evidence = json.loads(self.planner.call("work_context", {"reference": check["evidence"]})["content"])
        self.assertIn("measured planner diagnostic", evidence["output"])
        self.assertEqual(exit_code, evidence["exit_code"])

    def test_successful_planner_check_discards_source_and_git_mutations(self):
        self.check_writes(0)

    def test_failed_planner_check_discards_source_and_git_mutations_and_retains_evidence(self):
        self.check_writes(7)

    def test_planner_inspection_and_composer_install_discard_source_mutations(self):
        (self.workspace / "vendor/.agentscient-dependencies").unlink()
        composer = self.executable("composer-fixture", (
            "if 'install' in sys.argv:\n"
            "    Path('src/ComposerTouched.php').write_text('composer script edit')\n"
            "    Path('README.md').unlink()\n"
            "    Path('vendor').mkdir(exist_ok=True)\n"
            "    Path('vendor/autoload.php').write_text('<?php')\n"
            "else:\n"
            + "\n".join("    " + line for line in self.mutations().splitlines()) + "\n"
            "    print(json.dumps({'name': 'symfony/console', 'diagnostic': 'measured package facts'}))"
        ))
        self.configure(composer=composer)
        before = self.source_state()
        details = self.operation("framework_inspect", {"kind": "packages", "package": "symfony/console"})
        self.assertEqual(before, self.source_state())
        self.assertEqual("completed", details["state"], details)
        result = details["result"]
        self.assertEqual("symfony/console", result["result"]["name"])
        evidence = json.loads(self.planner.call("work_context", {"reference": result["evidence"]})["content"])
        self.assertEqual(result["result"], evidence)

    def test_failed_planner_inspection_preserves_source_and_its_full_error(self):
        console = self.executable("console-fixture", self.mutations() + "print('measured inspection failure')\nraise SystemExit(9)")
        self.configure(php=console)
        before = self.source_state()
        details = self.operation("framework_inspect", {"kind": "makers", "name": "make:command"})
        self.assertEqual(before, self.source_state())
        self.assertEqual("failed", details["state"], details)
        self.assertIn("measured inspection failure", details["error"])

    def test_failed_planner_composer_install_discards_source_mutations(self):
        (self.workspace / "vendor/.agentscient-dependencies").unlink()
        composer = self.executable("failed-composer-fixture", self.mutations() + "print('measured install failure')\nraise SystemExit(6)")
        self.configure(composer=composer)
        before = self.source_state()
        details = self.operation("framework_inspect", {"kind": "packages", "package": "symfony/console"})
        self.assertEqual(before, self.source_state())
        self.assertEqual("failed", details["state"], details)
        self.assertIn("measured install failure", details["error"])

    def test_planner_check_isolates_absolute_internal_links_and_compiled_cache(self):
        cache = self.workspace / "var/cache/container.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({"kernel.project_dir": str(self.workspace)}))
        (self.workspace / "absolute-link").symlink_to(self.workspace / "src/Example.php")
        program = (
            "import json\nfrom pathlib import Path\n"
            "cache = Path('var/cache/container.json')\n"
            "if cache.exists():\n"
            "    producer = Path(json.loads(cache.read_text())['kernel.project_dir'])\n"
            "    (producer / 'src/Example.php').write_text('escaped through compiled cache')\n"
            "Path('absolute-link').write_text('diagnostic edit through internal link')\n"
            "print('measured linked diagnostic')\n"
        )
        self.configure(checks={"fixture": [sys.executable, "-c", program]})
        before = self.source_state()
        details = self.operation("work_check", {})
        self.assertEqual(before, self.source_state())
        self.assertEqual("completed", details["state"], details)
        self.assertTrue(details["result"]["passed"])
        self.assertEqual(self.revision, details["result"]["revision"])
        self.assertIn("measured linked diagnostic", details["result"]["checks"][0]["output"])

    def test_planner_inspection_retains_profiler_data_while_discarding_compiled_cache(self):
        cache = self.workspace / "var/cache/container.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({"kernel.project_dir": str(self.workspace)}))
        profile = self.workspace / "var/cache/dev/profiler/data"
        profile.parent.mkdir(parents=True)
        profile.write_text("measured original profiler data")
        console = self.executable("profiler-console-fixture", (
            "assert not Path('var/cache/container.json').exists(), 'Compiled producer cache was reused'\n"
            "profile = Path('var/cache/dev/profiler/data').read_text()\n"
            "assert profile == 'measured original profiler data'\n"
            "print(json.dumps({'name': 'make:command', 'profile': profile, 'definition': {'arguments': {}, 'options': {}}}))"
        ))
        self.configure(php=console)
        before = self.source_state()
        details = self.operation("framework_inspect", {"kind": "makers", "name": "make:command"})
        self.assertEqual(before, self.source_state())
        self.assertEqual("completed", details["state"], details)
        self.assertEqual("measured original profiler data", details["result"]["result"]["profile"])


if __name__ == "__main__":
    unittest.main()
