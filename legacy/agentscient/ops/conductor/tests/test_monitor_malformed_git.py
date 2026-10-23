"""Independent regression tests for malformed Git metadata observation.

Contracts (only): `ops/conductor/monitor-lever.md` (disabled entries are
validated never evaluated; an observation failure is unavailable data,
not a config/state refusal; source failures do not halt other due
observations; missing/unreadable local Git is unavailable;
`local-unavailable` when the local read failed; monitor state stays
disjoint from the configured repo and Git common dir) and inherited
`ops/conductor/instrument-conventions.md`.

Admitted defect only: a valid registry whose `repo/.git` is a regular
file of bytes `not a gitdir pointer\\n` currently makes `run --once`
exit 2 even when that source is disabled, and blocks a healthy sibling.
Healthy-consumer control: malformed source absent and disabled; queued
events keep evidence `{repo,remote,ref}` and summarize freshness, not
commit OIDs. `ops/conductor/tests/test_monitor_git_refs.py` (19 tests)
stays frozen.

The subject `ops/conductor/tools/monitor.py` is not read here. Tests
drive the existing CLI through `_GitRefsFixture` with hermetic scratch
repos, config and state. No new public helper is demanded.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import unittest
from pathlib import Path

from test_monitor import CONSUMER, NOW, RUN_CAP, _tree_files
from test_monitor_git_refs import (
    DEV,
    DEV_REF,
    MASTER,
    MASTER_REF,
    REMOTE_NAME,
    REPO_EMAIL,
    REPO_USER,
    _GitRefsFixture,
)

from ops.testkit import Result, Timeout, World, run

MALFORMED_GIT_BYTES = b"not a gitdir pointer\n"
BAD_MONITOR = "malformed_refs"
OK_MONITOR = "healthy_refs"


class _MalformedGitFixture(_GitRefsFixture):
    """Healthy git-refs sibling plus a planted malformed `.git` file."""

    def setUp(self):
        self.world = World(prefix="monitor-malformed-git-")
        try:
            super().setUp()
        except Exception:
            self.world.close()
            raise
        self._restore_modes: list[tuple[Path, int]] = []
        self.malformed = self.world.cwd / "malformed-src"
        self.unread_store = self.world.cwd / "unread-git-store"
        self.pointer_repo = self.world.cwd / "unknown-pointer-src"
        self.assertFalse(self.malformed.exists(), "plant: malformed starts absent")
        self.assertFalse(
            self.git_argv_log.exists(), "plant: git argv log absent before sources"
        )

    def tearDown(self):
        try:
            for path, mode in reversed(self._restore_modes):
                with contextlib.suppress(OSError):
                    os.chmod(path, mode)
            super().tearDown()
        finally:
            self.world.close()

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def _plant_git_shim(self) -> None:
        """The target family needs only the passthrough side of the shared shim."""
        self.bin = self.world.bin
        self.gh = self.bin / "gh"
        self.git_argv_log = self.world.tools.log_path("git")
        # a wrapped real git is the passthrough side; no control file since
        # _GitRefsFixture moved onto world.tools
        self.world.tools.wrap("git")

    def env(self, *, agent=None, extra=None):
        values = {
            "PATH": self.path_without_gh(),
            "LANE_CONF": str(self.conf_path),
        }
        if agent is not None:
            values["WF_AGENT"] = agent
        if extra:
            for name, value in extra.items():
                if value is None:
                    values.pop(name, None)
                else:
                    values[name] = value
        return self.world.env(values)

    def git(self, repo: Path, *args: str, check: bool = True) -> Result:
        proc = run(
            [
                self.real_git,
                "-c",
                f"user.name={REPO_USER}",
                "-c",
                f"user.email={REPO_EMAIL}",
                "-c",
                "commit.gpgsign=false",
                "-C",
                str(repo),
                *args,
            ],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=RUN_CAP,
        )
        if check:
            self.assertEqual(proc.exit, 0, f"git {args} in {repo}: {proc.stderr or proc.stdout}")
        return proc

    def plant_pair(self) -> dict[str, str]:
        self.origin.mkdir()
        init_b = self.git(self.origin, "init", "--bare", "-b", MASTER)
        self.assertEqual(init_b.exit, 0, init_b.stderr)
        self.git_repo.mkdir()
        init_w = self.git(self.git_repo, "init", "-b", MASTER)
        self.assertEqual(init_w.exit, 0, init_w.stderr)
        self.git(self.git_repo, "config", "user.name", REPO_USER)
        self.git(self.git_repo, "config", "user.email", REPO_EMAIL)
        readme = self.git_repo / "README"
        self.world.plant_bytes(readme, b"git-refs fixture master\n")
        self.git(self.git_repo, "add", "README")
        self.git(self.git_repo, "commit", "-m", "master seed\n")
        master_oid = self.oid(self.git_repo, "HEAD")
        self.git(self.git_repo, "checkout", "-b", DEV)
        self.world.plant_bytes(readme, b"git-refs fixture dev\n")
        self.git(self.git_repo, "add", "README")
        self.git(self.git_repo, "commit", "-m", "dev seed\n")
        dev_oid = self.oid(self.git_repo, "HEAD")
        self.assertNotEqual(dev_oid, master_oid)
        self.git(self.git_repo, "remote", "add", REMOTE_NAME, str(self.origin))
        self.git(self.git_repo, "push", REMOTE_NAME, DEV, MASTER)
        self.git(self.git_repo, "checkout", MASTER)
        self.assertEqual(self.oid(self.git_repo, DEV_REF), dev_oid)
        self.assertEqual(self.oid(self.git_repo, MASTER_REF), master_oid)
        self.assertEqual(self.tracking_oid(self.git_repo, DEV), dev_oid)
        self.assertEqual(self.tracking_oid(self.git_repo, MASTER), master_oid)
        return {"dev": dev_oid, "master": master_oid}

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = None,
        extra: dict[str, str | None] | None = None,
        cwd: Path | None = None,
        timeout: int | float | None = None,
        new_session: bool = False,
    ):
        del new_session  # run owns the one process-session policy.
        try:
            result = run(
                [sys.executable, str(self.staged), *argv],
                cwd=cwd or self.world.cwd,
                env=self.env(agent=agent, extra=extra),
                timeout=RUN_CAP if timeout is None else timeout,
            )
        except Timeout as exc:
            self.fail(f"monitor exceeded timeout: {exc}")
        return result

    def _chmod(self, path: Path, mode: int) -> None:
        previous = stat.S_IMODE(path.stat().st_mode)
        self._restore_modes.append((path, previous))
        os.chmod(path, mode)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, path)

    def plant_malformed_git(self) -> Path:
        self.malformed.mkdir()
        git_file = self.malformed / ".git"
        self._plant_bytes(git_file, MALFORMED_GIT_BYTES)
        self.assert_malformed_git_bytes()
        return self.malformed

    def assert_malformed_git_bytes(self) -> None:
        git_file = self.malformed / ".git"
        self.assertTrue(git_file.is_file(), git_file)
        self.assertFalse(git_file.is_dir(), git_file)
        self.assertFalse(git_file.is_symlink(), git_file)
        landed = git_file.read_bytes()
        self.assertEqual(landed, MALFORMED_GIT_BYTES)
        self.assertFalse(landed.startswith(b"gitdir: "), landed)

    def relative_to_config(self, repo: Path) -> str:
        rel = os.path.relpath(repo, start=self.config.parent)
        self.assertFalse(os.path.isabs(rel), rel)
        self.assertTrue(
            (self.config.parent / rel).resolve() == repo.resolve(),
            rel,
        )
        return rel

    def pair_registry(
        self,
        *,
        malformed_enabled: bool,
        healthy_enabled: bool = True,
        malformed_repo: Path | None = None,
        healthy_repo: Path | None = None,
    ) -> dict:
        bad = malformed_repo if malformed_repo is not None else self.malformed
        ok = healthy_repo if healthy_repo is not None else self.git_repo
        return self.registry(
            monitors={
                BAD_MONITOR: {
                    "enabled": malformed_enabled,
                    "adapter": "git-refs",
                    "schedule": "fast",
                    "params": {
                        "repo": self.relative_to_config(bad),
                        "remote": REMOTE_NAME,
                        "branches": [DEV, MASTER],
                        "timeout_s": 5,
                    },
                },
                OK_MONITOR: {
                    "enabled": healthy_enabled,
                    "adapter": "git-refs",
                    "schedule": "fast",
                    "params": {
                        "repo": self.relative_to_config(ok),
                        "remote": REMOTE_NAME,
                        "branches": [DEV, MASTER],
                        "timeout_s": 5,
                    },
                },
            },
            conditions={
                "malformed_unavail": {
                    "enabled": True,
                    "monitor": BAD_MONITOR,
                    "op": "unavailable",
                    "field": None,
                    "threshold": None,
                    "cooldown_s": 0,
                },
                "malformed_changed": {
                    "enabled": True,
                    "monitor": BAD_MONITOR,
                    "op": "changed",
                    "field": "freshness",
                    "threshold": None,
                    "cooldown_s": 0,
                },
                "healthy_unavail": {
                    "enabled": True,
                    "monitor": OK_MONITOR,
                    "op": "unavailable",
                    "field": None,
                    "threshold": None,
                    "cooldown_s": 0,
                },
                "healthy_changed": {
                    "enabled": True,
                    "monitor": OK_MONITOR,
                    "op": "changed",
                    "field": "freshness",
                    "threshold": None,
                    "cooldown_s": 0,
                },
            },
            routes={
                "malformed_unavail": {
                    "enabled": True,
                    "condition": "malformed_unavail",
                    "adapter": "queue",
                    "consumer": CONSUMER,
                },
                "malformed_changed": {
                    "enabled": True,
                    "condition": "malformed_changed",
                    "adapter": "queue",
                    "consumer": CONSUMER,
                },
                "healthy_unavail": {
                    "enabled": True,
                    "condition": "healthy_unavail",
                    "adapter": "queue",
                    "consumer": CONSUMER,
                },
                "healthy_changed": {
                    "enabled": True,
                    "condition": "healthy_changed",
                    "adapter": "queue",
                    "consumer": CONSUMER,
                },
            },
        )

    def plant_pair_registry(self, **kw) -> dict:
        body = self.pair_registry(**kw)
        self.plant_config(body)
        landed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(landed["monitors"][BAD_MONITOR]["adapter"], "git-refs")
        self.assertEqual(landed["monitors"][OK_MONITOR]["adapter"], "git-refs")
        return body

    def plant_healthy_and_malformed(self, *, malformed_enabled: bool) -> dict[str, str]:
        oids = self.plant_pair()
        self.plant_malformed_git()
        self.assert_malformed_git_bytes()
        self.assertEqual(self.oid(self.git_repo, DEV_REF), oids["dev"])
        self.assertEqual(self.oid(self.git_repo, MASTER_REF), oids["master"])
        self.plant_pair_registry(malformed_enabled=malformed_enabled)
        self.assertFalse(
            self.git_argv_log.exists(),
            "plant: planting sources must not query through the PATH git shim",
        )
        data = self.check_ok()
        self.assertGreaterEqual(data["monitors"], 2, data)
        self.assert_state_empty()
        self.assertEqual(self.ls_remote_argvs(), [])
        self.assert_malformed_git_bytes()
        return oids

    def git_argvs(self) -> list[list[str]]:
        return [row["argv"] for row in self.world.tools.calls("git")]

    def ls_remote_argvs(self) -> list[list[str]]:
        return [argv for argv in self.git_argvs() if "ls-remote" in argv]

    def argv_targets_repo(self, argv: list[str], repo: Path) -> bool:
        resolved = repo.resolve()
        for item in argv:
            try:
                if Path(item).resolve() == resolved:
                    return True
            except (OSError, ValueError):
                continue
        return False

    def git_argvs_for(self, repo: Path) -> list[list[str]]:
        return [argv for argv in self.git_argvs() if self.argv_targets_repo(argv, repo)]

    def ls_remote_for(self, repo: Path) -> list[list[str]]:
        return [
            argv
            for argv in self.ls_remote_argvs()
            if self.argv_targets_repo(argv, repo)
        ]

    def plant_unreadable_gitdir_pointer(self) -> Path:
        self.unread_store.mkdir()
        marker = self.unread_store / "HEAD"
        self._plant_bytes(marker, b"ref: refs/heads/master\n")
        self.pointer_repo.mkdir()
        pointer = f"gitdir: {self.unread_store.resolve()}\n".encode("ascii")
        self._plant_bytes(self.pointer_repo / ".git", pointer)
        landed = (self.pointer_repo / ".git").read_bytes()
        self.assertTrue(landed.startswith(b"gitdir: "), landed)
        self.assertIn(str(self.unread_store.resolve()).encode("ascii"), landed)
        self._chmod(self.unread_store, 0o000)
        try:
            next(self.unread_store.iterdir())
        except PermissionError:
            pass
        else:
            self.skipTest("mode 000 stays readable under this uid; unreadability cannot be planted")
        return self.pointer_repo

    def assert_no_files_under(self, root: Path) -> None:
        try:
            mode = stat.S_IMODE(root.stat().st_mode)
            os.chmod(root, mode | 0o700)
        except OSError:
            return
        try:
            names = [p for p in root.rglob("*") if p.is_file()]
            extra = [p for p in names if p.name != "HEAD"]
            self.assertEqual(extra, [], extra)
        finally:
            with contextlib.suppress(OSError):
                os.chmod(root, 0o000)

    def assert_overlap_refusal(self, proc, state_dir: Path) -> None:
        self.assert_refusal(proc, "state")
        err = (proc.stderr or "").lower()
        self.assertIn("disjoint", err, proc.stderr)
        self.assertIn("source", err, proc.stderr)
        self.assertNotIn("restore the source metadata", err, proc.stderr)
        self.assertNotIn("unavailable git storage path", err, proc.stderr)
        self.assertIn(str(state_dir.resolve()).lower(), err, proc.stderr)
        self.assertEqual(_tree_files(state_dir), (), _tree_files(state_dir))

    def assert_healthy_sibling_observed(self, events: list) -> None:
        changed = [
            event
            for event in events
            if event.get("monitor") == OK_MONITOR
            and event.get("condition") == "healthy_changed"
        ]
        self.assertEqual(len(changed), 2, changed)
        self.assertEqual(
            [event["subject"] for event in changed],
            [DEV_REF, MASTER_REF],
        )
        expected_repo = self.git_repo.resolve()
        for event in changed:
            self.assertEqual(event["state"], "ok", event)
            self.assertEqual(
                event["summary"],
                self._metric_summary("freshness", "equal"),
                event,
            )
            evidence = event["evidence"]
            self.assertIsInstance(evidence, dict, event)
            self.assertEqual(set(evidence), {"repo", "remote", "ref"}, evidence)
            self.assertEqual(evidence["ref"], event["subject"], event)
            self.assertEqual(evidence["remote"], REMOTE_NAME, event)
            self.assertEqual(
                Path(evidence["repo"]).resolve(),
                expected_repo,
                event,
            )


class HealthyConsumerBaseline(_MalformedGitFixture):
    """Absent disabled malformed source leaves the healthy consumer intact."""

    def test_disabled_absent_malformed_source_observes_healthy_consumer(self):
        oids = self.plant_pair()
        self.assertFalse(
            self.malformed.exists(),
            "plant: malformed source starts absent",
        )
        self.assertEqual(self.oid(self.git_repo, DEV_REF), oids["dev"])
        self.assertEqual(self.oid(self.git_repo, MASTER_REF), oids["master"])
        self.plant_pair_registry(malformed_enabled=False)
        self.assertFalse(
            self.malformed.exists(),
            "registry plant must not create the malformed source",
        )
        self.assertFalse(
            self.git_argv_log.exists(),
            "plant: planting sources must not query through the PATH git shim",
        )
        data = self.check_ok()
        self.assertGreaterEqual(data["monitors"], 2, data)
        self.assert_state_empty()
        self.assertEqual(self.ls_remote_argvs(), [])
        before_healthy = self.fingerprint(self.git_repo)
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["events"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        self.assertEqual(self.ls_remote_for(self.malformed), [])
        self.assertEqual(self.git_argvs_for(self.malformed), [])
        self.assertEqual(len(self.ls_remote_for(self.git_repo)), 1, self.ls_remote_argvs())
        self.assert_one_ls_remote(self.git_repo)
        events = self.digest_events()
        self.assertEqual(
            [event for event in events if event.get("monitor") == BAD_MONITOR],
            [],
            events,
        )
        self.assertEqual(len(events), 2, events)
        self.assert_healthy_sibling_observed(events)
        self.assertFalse(self.malformed.exists())
        self.assertEqual(self.fingerprint(self.git_repo), before_healthy)
        self.assertNotIn("restore the source metadata", (proc.stderr or "").lower())


class MalformedGitDisabled(_MalformedGitFixture):
    """Disabled malformed Git metadata is not evaluated and does not block."""

    def test_disabled_malformed_pointer_does_not_query_or_block_healthy_sibling(self):
        self.plant_healthy_and_malformed(malformed_enabled=False)
        before_malformed = (self.malformed / ".git").read_bytes()
        before_healthy = self.fingerprint(self.git_repo)
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        self.assertGreaterEqual(counts["events"], 2, counts)
        self.assertEqual(self.ls_remote_for(self.malformed), [])
        self.assertEqual(self.git_argvs_for(self.malformed), [])
        self.assertEqual(len(self.ls_remote_for(self.git_repo)), 1, self.ls_remote_argvs())
        self.assert_one_ls_remote(self.git_repo)
        events = self.digest_events()
        self.assertEqual(
            [event for event in events if event.get("monitor") == BAD_MONITOR],
            [],
            events,
        )
        self.assert_healthy_sibling_observed(events)
        self.assertEqual((self.malformed / ".git").read_bytes(), before_malformed)
        self.assertEqual(self.fingerprint(self.git_repo), before_healthy)
        self.assertNotIn("restore the source metadata", (proc.stderr or "").lower())


class MalformedGitEnabled(_MalformedGitFixture):
    """Enabled malformed Git metadata is local-unavailable, not a run refusal."""

    def test_enabled_malformed_pointer_is_local_unavailable_and_permits_sibling(self):
        oids = self.plant_healthy_and_malformed(malformed_enabled=True)
        before_malformed = (self.malformed / ".git").read_bytes()
        before_healthy = self.fingerprint(self.git_repo)
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertGreaterEqual(counts["observations"], 4, counts)
        self.assertGreaterEqual(counts["unavailable"], 2, counts)
        self.assertGreaterEqual(counts["events"], 1, counts)
        self.assertEqual(len(self.ls_remote_for(self.git_repo)), 1, self.ls_remote_argvs())
        events = self.digest_events()
        self.assert_healthy_sibling_observed(events)
        unavail = [
            event
            for event in events
            if event.get("monitor") == BAD_MONITOR
            and event.get("condition") == "malformed_unavail"
        ]
        self.assertEqual(len(unavail), 2, unavail)
        self.assertEqual(
            [event["subject"] for event in unavail],
            [DEV_REF, MASTER_REF],
        )
        for event in unavail:
            self.assertEqual(event["state"], "unavailable", event)
            self.assertEqual(event["summary"], "state=unavailable", event)
        freshness = [
            event
            for event in events
            if event.get("monitor") == BAD_MONITOR
            and event.get("condition") == "malformed_changed"
        ]
        self.assertEqual(len(freshness), 2, freshness)
        for event in freshness:
            self.assertEqual(event["state"], "unavailable", event)
            self.assertEqual(
                event["summary"],
                self._metric_summary("freshness", "local-unavailable"),
                event,
            )
            self.assertNotIn("missing-local", event["summary"])
            self.assertNotIn("remote-unavailable", event["summary"])
        blob = json.dumps(
            [event for event in events if event.get("monitor") == BAD_MONITOR]
        )
        self.assertNotIn(oids["dev"], blob)
        self.assertNotIn(oids["master"], blob)
        self.assertEqual((self.malformed / ".git").read_bytes(), before_malformed)
        self.assertEqual(self.fingerprint(self.git_repo), before_healthy)
        self.assertNotIn("restore the source metadata", (proc.stderr or "").lower())

        other = self.scratch / "state-unread"
        other.mkdir()
        self.state = other
        self.plant_unreadable_gitdir_pointer()
        self.plant_pair_registry(
            malformed_enabled=True,
            malformed_repo=self.pointer_repo,
        )
        unread_proc = self.run_once()
        self.assert_no_files_under(self.unread_store)
        if unread_proc.returncode == 0:
            data = self.parse_json(unread_proc, expect_exit=0)
            self.assertGreaterEqual(data["unavailable"], 1, data)
            for name in _tree_files(self.state):
                self.assertFalse(
                    str(self.unread_store.resolve()) in name,
                    name,
                )
        else:
            self.assert_refusal(unread_proc, "state")
            self.assertEqual(_tree_files(self.state), (), _tree_files(self.state))


class MalformedGitStorageOverlap(_MalformedGitFixture):
    """Nearby inspectable Git storage still protects monitor state."""

    def test_healthy_and_worktree_common_dir_overlap_refuses_with_malformed_sibling(self):
        self.plant_pair()
        self.plant_malformed_git()
        self.git(self.git_repo, "checkout", DEV)
        add_wt = self.git(
            self.git_repo,
            "worktree",
            "add",
            str(self.other_wt),
            MASTER,
            check=False,
        )
        self.assertEqual(add_wt.exit, 0, add_wt.stderr)
        self.assertTrue((self.other_wt / "README").is_file())
        wt_git = self.other_wt / ".git"
        self.assertTrue(wt_git.is_file(), wt_git)
        self.assertTrue(
            wt_git.read_bytes().startswith(b"gitdir: "),
            wt_git.read_bytes(),
        )
        common_raw = self.git(
            self.other_wt, "rev-parse", "--git-common-dir"
        ).stdout.strip()
        common = (
            Path(common_raw)
            if Path(common_raw).is_absolute()
            else (self.other_wt / common_raw)
        )
        self.assertEqual(common.resolve(), (self.git_repo / ".git").resolve())
        self.assert_malformed_git_bytes()
        self.plant_pair_registry(malformed_enabled=False)
        data = self.check_ok()
        self.assertGreaterEqual(data["monitors"], 2, data)

        cases = {
            "healthy-worktree": self.git_repo / "inside-state",
            "common-dir": common / "monitor-overlap",
            "linked-worktree": self.other_wt / "wt-state",
        }
        self.assertEqual(len(cases), 3)
        for name, dest in cases.items():
            with self.subTest(name):
                if self.git_argv_log.exists():
                    self.git_argv_log.unlink()
                    self.assertFalse(self.git_argv_log.exists())
                dest.mkdir()
                self.assertTrue(dest.is_dir(), dest)
                self.state = dest
                before_malformed = (self.malformed / ".git").read_bytes()
                before_healthy = self.fingerprint(self.git_repo)
                before_other = self.fingerprint(self.other_wt)
                proc = self.run_once(now=NOW)
                self.assert_overlap_refusal(proc, dest)
                self.assertEqual(
                    (self.malformed / ".git").read_bytes(), before_malformed
                )
                self.assertEqual(self.fingerprint(self.git_repo), before_healthy)
                self.assertEqual(self.fingerprint(self.other_wt), before_other)
                self.assertEqual(self.ls_remote_argvs(), [])


if __name__ == "__main__":
    unittest.main()
