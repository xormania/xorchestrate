"""Independent regression tests: Git observation executes nothing a source configures.

Contract (only): `ops/conductor/monitor-lever.md`, the guarantee that
observation never invokes arbitrary configured commands, and that a remote
needing a configured command, helper or disallowed transport reads as
remote-unavailable. Tests drive the existing CLI through `_GitRefsFixture`;
its git shim passes through to the real Git, so these exercise Git itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import test_monitor as monitor_base
import test_monitor_git_refs as git_refs

from ops.testkit import World, run

REMOTE_NAME = git_refs.REMOTE_NAME


class GitObservationIsolation(git_refs._GitRefsFixture):
    """A source repository's configuration cannot make observation run a program."""

    def setUp(self):
        self.world = World(prefix="monitor-git-isolation-")
        self.addCleanup(self.world.close)
        self._example_watch = self.world.watch(monitor_base.EXAMPLE)
        self._tool_before = monitor_base.TOOL.is_file()
        self.root = self.world.root
        self.home = self.world.home
        self.xdg = self.world.xdg_state
        self.scratch = self.world.root / "scratch"
        self.conf_dir = self.world.root / "conf"
        self.cwd = self.world.cwd
        self.state = self.world.root / "state"
        for path in (self.scratch, self.conf_dir, self.state):
            path.mkdir()
        self.bin = self.world.bin
        self.store = self.conf_dir / "jobs"
        self.cwd_store = self.cwd / "jobs"
        self.rollout = self.conf_dir / "rollout.jsonl"
        self.gh = self.bin / "gh"
        self.argv_log = self.scratch / "gh-argv.jsonl"
        self.counter = self.scratch / "gh-calls"
        self.sleep_marker = self.scratch / "gh-sleep-started"
        self.worker_cwd = self.scratch / "worker-cwd"
        self.worker_cwd.mkdir()
        self.staged = self.world.stage(
            monitor_base.TOOL,
            siblings=monitor_base.SIBLINGS,
        )
        self.config = self.conf_dir / "monitor.json"
        self._workers = []
        self.plant_lane_conf()

        self.real_git = self.world.require("git")
        self.origin = self.scratch / "origin.git"
        self.git_repo = self.scratch / "repo"
        self.pusher = self.scratch / "pusher"
        self.other_wt = self.scratch / "occupied-wt"
        self.world.tools.wrap("git")
        self.assertEqual(self.world.tools.calls("git"), [])

    def tearDown(self):
        try:
            self._example_watch.assert_unchanged(self)
            self.assertEqual(monitor_base.TOOL.is_file(), self._tool_before)
        finally:
            self.world.close()

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def env(self, *, agent=None, extra=None):
        values = {"LANE_CONF": str(self.conf_path)}
        if agent is not None:
            values["WF_AGENT"] = agent
        if extra:
            for name, value in extra.items():
                if value is None:
                    values.pop(name, None)
                else:
                    values[name] = value
        return self.world.env(values)

    def git_env(self):
        return self.world.env()

    def git(self, repo: Path, *args: str, check: bool = True):
        result = run(
            [
                self.real_git,
                "-c",
                "user.name=Git Refs Tester",
                "-c",
                "user.email=git-refs-tester@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-C",
                str(repo),
                *args,
            ],
            cwd=self.world.cwd,
            env=self.git_env(),
            timeout=30,
        )
        if check:
            self.assertEqual(result.exit, 0, result.stderr or result.stdout)
        return result

    def plant_pair(self) -> dict[str, str]:
        self.origin.mkdir()
        init_b = run(
            [self.real_git, "init", "--bare", "-b", git_refs.MASTER, str(self.origin)],
            cwd=self.world.cwd,
            env=self.git_env(),
            timeout=30,
        )
        self.assertEqual(init_b.exit, 0, init_b.stderr)
        self.git_repo.mkdir()
        init_w = run(
            [self.real_git, "init", "-b", git_refs.MASTER, str(self.git_repo)],
            cwd=self.world.cwd,
            env=self.git_env(),
            timeout=30,
        )
        self.assertEqual(init_w.exit, 0, init_w.stderr)
        readme = self.git_repo / "README"
        self.world.plant_bytes(readme, b"git-refs fixture master\n")
        self.git(self.git_repo, "add", "README")
        self.git(self.git_repo, "commit", "-m", "master seed\n")
        master_oid = self.oid(self.git_repo, "HEAD")
        self.git(self.git_repo, "checkout", "-b", git_refs.DEV)
        self.world.plant_bytes(readme, b"git-refs fixture dev\n")
        self.git(self.git_repo, "add", "README")
        self.git(self.git_repo, "commit", "-m", "dev seed\n")
        dev_oid = self.oid(self.git_repo, "HEAD")
        self.assertNotEqual(dev_oid, master_oid)
        self.git(self.git_repo, "remote", "add", REMOTE_NAME, str(self.origin))
        self.git(self.git_repo, "push", REMOTE_NAME, git_refs.DEV, git_refs.MASTER)
        self.git(self.git_repo, "checkout", git_refs.MASTER)
        self.assertEqual(self.oid(self.git_repo, git_refs.DEV_REF), dev_oid)
        self.assertEqual(self.oid(self.git_repo, git_refs.MASTER_REF), master_oid)
        self.assertEqual(self.tracking_oid(self.git_repo, git_refs.DEV), dev_oid)
        self.assertEqual(self.tracking_oid(self.git_repo, git_refs.MASTER), master_oid)
        return {"dev": dev_oid, "master": master_oid}

    def invoke(
        self,
        argv,
        *,
        agent=None,
        extra=None,
        cwd=None,
        timeout=None,
        new_session=False,
    ):
        self.assertFalse(new_session, "this family has no long-lived command")
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=cwd or self.cwd,
            env=self.env(agent=agent, extra=extra),
            timeout=monitor_base.RUN_CAP if timeout is None else timeout,
        )

    def parse_json(self, result, *, expect_exit=0):
        self.assertEqual(result.exit, expect_exit, self.combined(result))
        self.assertTrue(result.stdout.strip(), self.combined(result))
        return json.loads(result.stdout)

    def ls_remote_argvs(self):
        return [
            call["argv"]
            for call in self.world.tools.calls("git")
            if "ls-remote" in call["argv"]
        ]

    def _plant_marker_script(self, name: str) -> Path:
        planted = self.world.tools.plant(name, exit=255)
        self.assertEqual(self.world.tools.calls(name), [])
        return planted

    def _assert_unavailable_without_execution(self, name: str) -> None:
        proc, counts = self.run_counts()
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 2, counts)
        self.assertEqual(
            self.world.tools.calls(name),
            [],
            "a configured command ran during observation",
        )
        self.assert_one_ls_remote(self.git_repo)

    def test_repo_core_sshcommand_is_not_executed(self):
        self.plant_pair()
        configured = self._plant_marker_script("configured-ssh")
        self._plant_marker_script("ssh")
        self.git(self.git_repo, "remote", "set-url", REMOTE_NAME, "ssh://127.0.0.1:1/none.git")
        self.git(self.git_repo, "config", "core.sshCommand", str(configured))
        self.plant_git_refs()
        self._assert_unavailable_without_execution("configured-ssh")
        self.assertTrue(
            self.world.tools.calls("ssh"),
            "the default ssh on PATH was not what Git ran",
        )

    def test_ext_transport_is_refused_before_execution(self):
        self.plant_pair()
        self._plant_marker_script("configured-ext")
        self.git(
            self.git_repo,
            "remote",
            "set-url",
            REMOTE_NAME,
            "ext::configured-ext %S",
        )
        self.plant_git_refs()
        self._assert_unavailable_without_execution("configured-ext")
