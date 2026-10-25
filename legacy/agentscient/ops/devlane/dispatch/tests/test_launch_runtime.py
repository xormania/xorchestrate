"""LD-119/LD-142: an issued job keeps trusted code through edits and resume.

Owner-authorized repair; one producer wrote implementation and tests.
These cases execute the guard and coordination core without a model service.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import unittest
from pathlib import Path

import launch_support as ls

from ops.devlane.harness import isolation
from ops.devlane.hooks import profiles
from ops.testkit import World, run


class IssuedRuntime(ls.CoordinationCore, unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="issued-runtime-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.repo = self.world.cwd
        self.env = self.world.env({"WF_AGENT": ls.AGENT})
        for args in (("init", "-q", "-b", "work"),
                     ("config", "user.name", "Runtime Fixture"),
                     ("config", "user.email", "runtime@example.invalid"),
                     ("commit", "--allow-empty", "-qm", "fixture")):
            run(["git", *args], cwd=self.repo, env=self.env, expect=0)
        self.revision = run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env, expect=0).stdout.strip()
        self.installation = self.world.root / "installation"
        for name in profiles.source_pins(ls.REPO):
            target = self.installation / Path(name).relative_to(ls.REPO)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(name, target)
        # Local configuration is part of the trusted installation too.
        (self.installation / "ops/lane.local.conf").write_text("SCOPE_CAP='12000'\n")
        for rel in ("ops/devlane/dispatch/launch.py", "ops/devlane/task/envelope.py",
                    "ops/devlane/task/write_path.py"):
            target = self.installation / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ls.REPO / rel, target)
        self.job = self.world.root / "job"
        self.home = self.job / "home"
        self.home.mkdir(parents=True)
        self.kwargs = dict(source=self.installation, cwd=self.repo, home=self.home,
                           session="issued-runtime-worker", agent=ls.AGENT,
                           executable=sys.executable, servers=("coordination",))
        # record is the launcher's existing sibling import; load() registers it.
        ls.require_module(self, "record")
        self.launch = ls.load_path(self, self.installation / "ops/devlane/dispatch/launch.py", "runtime_launch")

    def install(self):
        return isolation.install_enforcement("codex", self.job / "enforcement", **self.kwargs)

    def mutate_launcher(self):
        for rel in ("ops/conductor/tools/probes.py", "ops/devlane/hooks/profiles.py"):
            path = self.installation / rel
            path.write_text(path.read_text() + "\n# An unrelated forward merge.\n")
        (self.installation / "ops/lane.local.conf").write_text("SCOPE_CAP='13000'\n")

    def issue(self, binding):
        leader = self.dispatcher_session()
        assignment = self.assignment(leader)
        digest = hashlib.sha256(binding["coordination_capability"].encode()).hexdigest()
        result = self.core("bind-for", "--session", binding["session"], "--roles", "worker",
                           "--for", assignment, "--harness", "codex", "--capability-digest", digest,
                           session=leader)
        self.assertEqual(result.returncode, 0, result.stderr)
        participant = re.search(r"participant (PT-\d+)", result.stdout).group(1)
        result = run([sys.executable, str(ls.REPO / "ops/conductor/tools/deliverable.py"),
                      "add", "work", "--why", "runtime regression"], cwd=self.repo, env=self.env)
        self.assertEqual(result.exit, 0, result.stderr)
        meta, server = self.launch._coordination_mcp(
            self.job, self.repo, "codex", "fixture-runtime", participant, assignment,
            "work", "2026-09-23T00:00:00Z")
        self.assertIsNotNone(server, meta)
        return assignment, server

    def worker_env(self, binding):
        return {**self.env, "WF_AGENT": ls.AGENT,
                "CLAUDE_SESSION_ID": binding["session"],
                "COORDINATION_CAPABILITY": binding["coordination_capability"],
                "AGENTSCIENT_HOOK_PROFILE": binding["binding"]}

    def guard(self, binding, command=None):
        payload = {"session_id": binding["session"], "cwd": str(self.repo),
                   "tool_name": "Read", "tool_input": {"file_path": str(self.repo / "README.md")}}
        if command is not None:
            payload.update(tool_name="Bash", tool_input={"command": command})
        result = run(["/bin/sh", binding["helper"], "PreToolUse"], cwd=self.repo,
                     env=self.worker_env(binding), stdin=json.dumps(payload), expect=0)
        return json.loads(result.stdout)

    def call(self, binding, server, name, arguments):
        result = run([*server[:-1], "call", name, json.dumps(arguments)],
                     cwd=self.repo, env=self.worker_env(binding))
        self.assertEqual(result.exit, 0, result.stderr)
        return json.loads(result.stdout)

    def test_launcher_edit_keeps_guard_and_assignment_submission(self):
        profile, binding = self.install()
        assignment, server = self.issue(binding)
        claimed = self.call(binding, server, "coord_claim", {"assignment": assignment, "branch": "work", "generation": 2})
        self.assertNotIn("deny", json.dumps(self.guard(binding)))
        self.mutate_launcher()
        answer = self.guard(binding)
        self.assertNotIn("deny", json.dumps(answer), answer)
        self.assertNotIn("deny", json.dumps(self.guard(binding, "git status --porcelain")))
        warning = self.guard(binding, "git push --force origin work")["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", warning)
        self.assertIn("force-push", warning["additionalContext"])
        profile.validate(binding)
        own = self.core("who", session=binding["session"], profile=binding["binding"])
        self.assertEqual(own.returncode, 0, own.stderr)
        self.assertIn(binding["session"], own.stdout)
        result = self.call(binding, server, "coord_submit", {
            "assignment": assignment, "evidence": self.revision,
            "revision": self.revision, "generation": claimed["generation"]})
        self.assertEqual(result["place"], "submitted")
        self.assertEqual(Path(server[2]), Path(binding["source"]) / "ops/conductor/tools/coordination_mcp.py")
        self.assertEqual((Path(binding["source"]) / "ops/lane.local.conf").read_text(), "SCOPE_CAP='12000'\n")

    def test_resume_uses_issued_bytes_after_launcher_changes(self):
        _, first = self.install()
        self.mutate_launcher()
        profile, resumed = self.install()
        self.assertEqual(resumed, first)
        profile.validate(resumed)

    def test_next_job_adopts_new_bytes_without_repinning_existing_job(self):
        first_profile, first = self.install()
        relative = "ops/conductor/tools/probes.py"
        old_bytes = (Path(first["source"]) / relative).read_bytes()
        self.mutate_launcher()
        self.job = self.world.root / "next-job"
        self.home = self.job / "home"
        self.home.mkdir(parents=True)
        self.kwargs.update(home=self.home, session="next-worker")
        next_profile, second = self.install()
        self.assertNotEqual((Path(second["source"]) / relative).read_bytes(), old_bytes)
        self.assertEqual((Path(first["source"]) / relative).read_bytes(), old_bytes)
        self.assertNotEqual(first["coordination_capability"], second["coordination_capability"])
        first_profile.validate(first)
        next_profile.validate(second)

    def test_relocation_preserves_runtime_and_capability(self):
        _, first = self.install()
        assignment, server = self.issue(first)
        claimed = self.call(first, server, "coord_claim", {
            "assignment": assignment, "branch": "work", "generation": 2})
        record = {"brief": {"coordination": {"config": server[-2], "server": server}}}
        self.mutate_launcher()
        old = self.job
        self.job = self.world.root / "moved-job"
        shutil.move(old, self.job)
        self.kwargs.update(home=self.job / "home", relocated_from=old)
        profile, moved = self.install()
        self.assertEqual(moved["coordination_capability"], first["coordination_capability"])
        self.assertEqual(Path(moved["source"]), self.job / "runtime")
        self.assertNotIn(str(old), profiles.canonical(moved))
        profile.validate(moved)
        self.launch._relocate_coordination(record, self.job, str(old))
        submitted = self.call(moved, record["brief"]["coordination"]["server"], "coord_submit", {
            "assignment": assignment, "evidence": self.revision,
            "revision": self.revision, "generation": claimed["generation"]})
        self.assertEqual(submitted["place"], "submitted")

    def test_worker_owned_policy_and_mcp_are_never_executed(self):
        marker = self.world.root / "candidate-executed"
        for rel in ("ops/devlane/hooks/profiles.py", "ops/conductor/tools/coordination_mcp.py"):
            target = self.repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('candidate executed')\n")
        profile, binding = self.install()
        _, server = self.issue(binding)
        self.assertNotIn("deny", json.dumps(self.guard(binding)))
        self.call(binding, server, "coord_brief", {})
        self.assertFalse(marker.exists())
        self.assertFalse(Path(binding["source"]).is_relative_to(self.repo))
        profile.validate(binding)

    def test_changed_runtime_warns_without_loading_its_python(self):
        _, binding = self.install()
        marker = self.world.root / "tampered-executed"
        policy = Path(binding["source"]) / "ops/devlane/hooks/profiles.py"
        policy.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        with self.assertRaises(isolation.NotIsolated):
            self.install()
        self.assertFalse(marker.exists())
        warning = self.guard(binding)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", warning)
        self.assertIn("hook-unavailable", warning["additionalContext"])
        self.assertFalse(marker.exists())

    def test_runtime_symlink_substitution_refuses(self):
        _, binding = self.install()
        runtime = Path(binding["source"])
        # The installation itself is not moved: the worker's runtime is separate.
        self.assertNotEqual(runtime, self.installation)
        replacement = runtime.with_name("replacement")
        shutil.move(runtime, replacement)
        runtime.symlink_to(replacement, target_is_directory=True)
        with self.assertRaises(isolation.NotIsolated):
            self.install()

    def test_worker_cannot_write_runtime_through_native_transport(self):
        profile, binding = self.install()
        command, env = profile.native_launch(binding, [sys.executable], self.worker_env(binding))
        bubble = command[0]
        probe = run([bubble, "--unshare-user", "--ro-bind", "/", "/", "--", "/usr/bin/true"],
                    cwd=self.repo, env=self.env)
        if probe.exit:
            self.skipTest("native bubblewrap unavailable: " + probe.stderr.strip())
        code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('tampered')"
        target = str(Path(binding["source"]) / "ops/conductor/tools/coordination_mcp.py")
        attempted = run([*command[:command.index("--") + 1], sys.executable, "-c", code, target],
                        cwd=self.repo, env=env)
        self.assertNotEqual(attempted.exit, 0)
        self.assertIn("Read-only file system", attempted.stderr)
        profile.validate(binding)


if __name__ == "__main__":
    unittest.main()
