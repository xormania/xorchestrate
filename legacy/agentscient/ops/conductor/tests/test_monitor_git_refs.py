"""Independent TDD tests for the monitor git-refs adapter.

Contracts (only): `ops/conductor/monitor-lever.md` §Branch freshness
adapter amendment and inherited `ops/conductor/instrument-conventions.md`.
`ops/conductor/tools/monitor.py` is the subject and is not read here.
Tests reuse `_MonitorFixture` and drive the existing CLI (`check`,
`run --once`, `digest`). Local Git fixtures plus a PATH git shim; no
network. No new public helper is demanded.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_monitor import (
    CONSUMER,
    EXAMPLE,
    NOW,
    NOW_30,
    NOW_60,
    NOW_400,
    TOOL,
    _MonitorFixture,
    _tree_files,
)

from ops.testkit import Result, World, run

REPO_USER = "Git Refs Tester"
REPO_EMAIL = "git-refs-tester@example.invalid"
GIT_SECRET = "GIT_RAW_STDERR_aa41_must_not_leak"
REMOTE_NAME = "origin"
DEV = "dev"
MASTER = "master"
DEV_REF = "refs/heads/dev"
MASTER_REF = "refs/heads/master"
PIPE_CAP = 1048576
OID_RE = r"^[0-9a-f]{40}([0-9a-f]{24})?$"


class _GitArgvLog:
    """Legacy array-lines view over the kit fake registry's call log."""

    def __init__(self, world: World):
        self.world = world

    @property
    def path(self) -> Path:
        return self.world.tools.log_path("git")

    def exists(self) -> bool:
        return self.path.exists()

    def is_file(self) -> bool:
        return self.path.is_file()

    def read_text(self, *, encoding: str = "utf-8") -> str:
        return "".join(
            json.dumps(call["argv"]) + "\n"
            for call in self.world.tools.calls("git")
        )

    def unlink(self) -> None:
        self.path.unlink()


class _GitRefsFixture(_MonitorFixture):
    """Synthetic local Git pair, PATH git shim, git-refs registry."""

    def setUp(self):
        self._example_before = EXAMPLE.read_bytes()
        self._tool_before = TOOL.is_file()
        self.world = World(prefix="monitor-git-refs-")
        self.root = self.world.root
        self.home = self.world.home
        self.xdg = self.world.xdg_state
        self.scratch = self.root / "scratch"
        self.bin = self.world.bin
        self.conf_dir = self.root / "conf"
        self.cwd = self.world.cwd
        self.state = self.root / "state"
        self.store = self.conf_dir / "jobs"
        self.cwd_store = self.cwd / "jobs"
        self.rollout = self.conf_dir / "rollout.jsonl"
        for path in (self.scratch, self.conf_dir, self.state):
            path.mkdir()
        self.gh = self.bin / "gh"
        self.argv_log = self.scratch / "gh-argv.jsonl"
        self.counter = self.scratch / "gh-calls"
        self.sleep_marker = self.scratch / "gh-sleep-started"
        self.worker_cwd = self.scratch / "worker-cwd"
        self.worker_cwd.mkdir()
        self.staged = self.world.stage(
            TOOL,
            "monitor.py",
            siblings=("laneconf.py", "instrument.py", "dispatch_observation.py"),
        )
        self.config = self.conf_dir / "monitor.json"
        self._workers = []
        self.plant_lane_conf()
        self.real_git = self.world.require("git")
        self.assertTrue(Path(self.real_git).is_file(), self.real_git)
        self.origin = self.scratch / "origin.git"
        self.git_repo = self.scratch / "repo"
        self.pusher = self.scratch / "pusher"
        self.other_wt = self.scratch / "occupied-wt"
        self._plant_git_shim()
        self.git_argv_log = _GitArgvLog(self.world)
        self.assertEqual(self.world.tools.calls("git"), [], "plant: git argv log absent")
        self.assertFalse(self.origin.exists(), "plant: origin starts absent")
        self.assertFalse(self.git_repo.exists(), "plant: work repo starts absent")

    def tearDown(self):
        try:
            self.assertEqual(
                EXAMPLE.read_bytes(),
                self._example_before,
                "example registry mutated",
            )
            self.assertEqual(TOOL.is_file(), self._tool_before)
        finally:
            self.world.close()

    def env(self, *, agent=None, extra=None):
        additions = {
            "PATH": self.path_without_gh(),
            "LANE_CONF": str(self.conf_path),
        }
        if agent is not None:
            additions["WF_AGENT"] = agent
        env = self.world.env(additions)
        if extra:
            for name, value in extra.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
        return env

    def git_env(self) -> dict[str, str]:
        return self.world.env({
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_AUTHOR_NAME": REPO_USER,
            "GIT_AUTHOR_EMAIL": REPO_EMAIL,
            "GIT_COMMITTER_NAME": REPO_USER,
            "GIT_COMMITTER_EMAIL": REPO_EMAIL,
        })

    def git(self, repo: Path, *args: str, check: bool = True):
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
            env=self.git_env(),
            cwd=self.cwd,
            timeout=60,
        )
        if check:
            self.assertEqual(
                proc.exit,
                0,
                f"git {args} in {repo}: {proc.stderr or proc.stdout}",
            )
        return proc

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def _plant_git_shim(self) -> None:
        dest = self.world.tools.wrap("git")
        which = shutil.which("git", path=self.path_without_gh())
        self.assertEqual(Path(which).resolve(), dest.resolve(), which)

    def set_ls_remote(self, **ctrl) -> None:
        answer = {
            "mode": "passthrough",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "sleep_s": 0,
        }
        answer.update(ctrl)
        if answer["mode"] == "passthrough":
            self.world.tools.wrap("git")
            return
        self.world.tools.plant(
            "git",
            responses=[
                {
                    "match": ["ls-remote"],
                    "stdout": answer["stdout"],
                    "stderr": answer["stderr"],
                    "exit": answer["exit_code"],
                    "delay": answer["sleep_s"],
                }
            ],
        )

    def ls_remote_argvs(self) -> list[list[str]]:
        rows = []
        for call in self.world.tools.calls("git"):
            argv = call["argv"]
            if "ls-remote" in argv:
                rows.append(argv)
        return rows

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = None,
        extra: dict[str, str | None] | None = None,
        cwd: Path | None = None,
        timeout: int | float | None = None,
        new_session: bool = False,
    ) -> Result:
        env = self.env(agent=agent, extra=extra)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        else:
            self.assertEqual(env.get("WF_AGENT"), agent)
        result = run(
            [sys.executable, str(self.staged), *argv],
            cwd=cwd or self.cwd,
            env=env,
            timeout=20 if timeout is None else timeout,
        )
        return result

    def assert_one_ls_remote(self, repo: Path) -> list[str]:
        rows = self.ls_remote_argvs()
        self.assertEqual(len(rows), 1, rows)
        argv = rows[0]
        self.assertIn("ls-remote", argv)
        self.assertIn("--refs", argv)
        self.assertIn("--", argv)
        self.assertIn(REMOTE_NAME, argv)
        self.assertIn(DEV_REF, argv)
        self.assertIn(MASTER_REF, argv)
        self.assertIn("-C", argv)
        c_idx = argv.index("-C")
        self.assertLess(c_idx + 1, len(argv), argv)
        self.assertEqual(
            Path(argv[c_idx + 1]).resolve(),
            repo.resolve(),
            argv,
        )
        self.assertLess(argv.index("ls-remote"), argv.index("--refs"), argv)
        self.assertLess(argv.index("--refs"), argv.index("--"), argv)
        self.assertLess(argv.index("--"), argv.index(REMOTE_NAME), argv)
        self.assertLess(argv.index(REMOTE_NAME), argv.index(DEV_REF), argv)
        self.assertLess(argv.index(DEV_REF), argv.index(MASTER_REF), argv)
        return argv

    def oid(self, repo: Path, ref: str) -> str:
        out = self.git(repo, "rev-parse", "--verify", ref).stdout.strip()
        self.assertRegex(out, OID_RE, out)
        self.assertEqual(out, out.lower(), out)
        return out

    def tracking_oid(self, repo: Path, branch: str) -> str:
        return self.oid(repo, f"refs/remotes/{REMOTE_NAME}/{branch}")

    def plant_pair(self) -> dict[str, str]:
        self.origin.mkdir()
        init_b = run(
            [self.real_git, "init", "--bare", "-b", MASTER, str(self.origin)],
            env=self.git_env(),
            cwd=self.cwd,
            timeout=60,
        )
        self.assertEqual(init_b.exit, 0, init_b.stderr)
        self.git_repo.mkdir()
        init_w = run(
            [self.real_git, "init", "-b", MASTER, str(self.git_repo)],
            env=self.git_env(),
            cwd=self.cwd,
            timeout=60,
        )
        self.assertEqual(init_w.exit, 0, init_w.stderr)
        self.git(self.git_repo, "config", "user.name", REPO_USER)
        self.git(self.git_repo, "config", "user.email", REPO_EMAIL)
        readme = self.git_repo / "README"
        self._plant_bytes(readme, b"git-refs fixture master\n")
        self.assertEqual(readme.read_bytes(), b"git-refs fixture master\n")
        self.git(self.git_repo, "add", "README")
        self.git(self.git_repo, "commit", "-m", "master seed\n")
        master_oid = self.oid(self.git_repo, "HEAD")
        self.git(self.git_repo, "checkout", "-b", DEV)
        self._plant_bytes(readme, b"git-refs fixture dev\n")
        self.assertEqual(readme.read_bytes(), b"git-refs fixture dev\n")
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

    def advance_origin(self, branch: str, payload: bytes) -> str:
        if self.pusher.exists():
            shutil.rmtree(self.pusher)
        clone = run(
            [
                self.real_git,
                "clone",
                "--quiet",
                str(self.origin),
                str(self.pusher),
            ],
            env=self.git_env(),
            cwd=self.cwd,
            timeout=60,
        )
        self.assertEqual(clone.exit, 0, clone.stderr)
        self.git(self.pusher, "checkout", branch)
        target = self.pusher / "README"
        self._plant_bytes(target, payload)
        self.assertEqual(target.read_bytes(), payload)
        self.git(self.pusher, "add", "README")
        self.git(self.pusher, "commit", "-m", f"advance {branch}\n")
        new_oid = self.oid(self.pusher, "HEAD")
        self.git(self.pusher, "push", REMOTE_NAME, branch)
        self.assertEqual(
            self.git(self.origin, "rev-parse", f"refs/heads/{branch}").stdout.strip(),
            new_oid,
        )
        return new_oid

    def delete_origin_branch(self, branch: str) -> None:
        proc = self.git(
            self.origin,
            "update-ref",
            "-d",
            f"refs/heads/{branch}",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        miss = self.git(
            self.origin, "rev-parse", "--verify", f"refs/heads/{branch}", check=False
        )
        self.assertNotEqual(miss.returncode, 0, miss.stdout)

    def delete_local_branch(self, branch: str) -> None:
        current = self.git(self.git_repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
        if current == branch:
            other = MASTER if branch == DEV else DEV
            self.git(self.git_repo, "checkout", other)
        self.git(self.git_repo, "branch", "-D", branch)
        miss = self.git(
            self.git_repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False
        )
        self.assertNotEqual(miss.returncode, 0, miss.stdout)

    def relative_repo(self) -> str:
        rel = os.path.relpath(self.git_repo, start=self.config.parent)
        self.assertFalse(os.path.isabs(rel), rel)
        return rel

    def git_refs_reg(
        self,
        *,
        repo: str | None = None,
        remote: str = REMOTE_NAME,
        branches: list[str] | None = None,
        timeout_s: float = 5,
        enabled: bool = True,
        extra_conditions: dict | None = None,
        extra_routes: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        if params is None:
            planted_branches = [DEV, MASTER] if branches is None else list(branches)
            params = {
                "repo": self.relative_repo() if repo is None else repo,
                "remote": remote,
                "branches": planted_branches,
                "timeout_s": timeout_s,
            }
            if not planted_branches:
                self.assertEqual(
                    params["branches"],
                    [],
                    "plant: empty branches remain empty",
                )
        conditions = {
            "refs_changed": {
                "enabled": True,
                "monitor": "branch_refs",
                "op": "changed",
                "field": "freshness",
                "threshold": None,
                "cooldown_s": 0,
            },
            "local_changed": {
                "enabled": True,
                "monitor": "branch_refs",
                "op": "changed",
                "field": "local_oid",
                "threshold": None,
                "cooldown_s": 0,
            },
            "remote_changed": {
                "enabled": True,
                "monitor": "branch_refs",
                "op": "changed",
                "field": "remote_oid",
                "threshold": None,
                "cooldown_s": 0,
            },
            "refs_unavailable": {
                "enabled": True,
                "monitor": "branch_refs",
                "op": "unavailable",
                "field": None,
                "threshold": None,
                "cooldown_s": 0,
            },
        }
        routes = {
            "refs_changed": {
                "enabled": True,
                "condition": "refs_changed",
                "adapter": "queue",
                "consumer": CONSUMER,
            },
            "local_changed": {
                "enabled": True,
                "condition": "local_changed",
                "adapter": "queue",
                "consumer": CONSUMER,
            },
            "remote_changed": {
                "enabled": True,
                "condition": "remote_changed",
                "adapter": "queue",
                "consumer": CONSUMER,
            },
            "refs_unavailable": {
                "enabled": True,
                "condition": "refs_unavailable",
                "adapter": "queue",
                "consumer": CONSUMER,
            },
        }
        if extra_conditions:
            conditions.update(extra_conditions)
        if extra_routes:
            routes.update(extra_routes)
        return self.registry(
            monitors={
                "branch_refs": {
                    "enabled": enabled,
                    "adapter": "git-refs",
                    "schedule": "fast",
                    "params": params,
                }
            },
            conditions=conditions,
            routes=routes,
        )

    def plant_git_refs(self, **kw) -> dict:
        body = self.git_refs_reg(**kw)
        self.plant_config(body)
        landed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(landed["monitors"]["branch_refs"]["adapter"], "git-refs")
        return body

    def fingerprint(self, repo: Path) -> dict[str, object]:
        show = self.git(repo, "show-ref", "--head", check=False)
        status = self.git(
            repo, "status", "--porcelain=v1", "--untracked-files=all"
        )
        head = self.git(repo, "rev-parse", "HEAD", check=False)
        wt = self.git(repo, "worktree", "list", "--porcelain")
        index = self.git(repo, "ls-files", "-s")
        cfg_path = repo / ".git" / "config"
        if cfg_path.is_file():
            config = cfg_path.read_bytes()
        else:
            gitdir = (repo / ".git").read_text(encoding="utf-8")
            config = gitdir.encode("utf-8")
        return {
            "show-ref": show.stdout,
            "status": status.stdout,
            "head": head.stdout,
            "worktrees": wt.stdout,
            "index": index.stdout,
            "config": config,
        }

    def events_where(
        self, events: list, *, subject: str | None = None, condition: str | None = None
    ) -> list:
        found = []
        for event in events:
            if subject is not None and event.get("subject") != subject:
                continue
            if condition is not None and event.get("condition") != condition:
                continue
            found.append(event)
        return found

    def _as_utc(self, value) -> datetime:
        self.assertIsInstance(value, str, value)
        text = value.strip()
        self.assertTrue(text, value)
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            self.fail(f"observed_at is not timezone-aware ISO: {value!r}")
        self.assertIsNotNone(parsed.tzinfo, value)
        return parsed.astimezone(timezone.utc)

    def assert_utc_observed_at(self, value, expected: str = NOW, msg=None) -> None:
        self.assertEqual(
            self._as_utc(value),
            self._as_utc(expected),
            msg if msg is not None else value,
        )

    def _metric_summary(self, field: str | None, value) -> str:
        if field is None:
            return f"state={value}"
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        return f"{field}={encoded}"

    def _field_for_condition(self, condition: str) -> str | None:
        mapping = {
            "local_changed": "local_oid",
            "remote_changed": "remote_oid",
            "refs_changed": "freshness",
            "refs_unavailable": None,
        }
        self.assertIn(condition, mapping, condition)
        return mapping[condition]

    def assert_metric_event(
        self,
        events: list,
        *,
        subject: str,
        condition: str,
        value,
        state: str | None = None,
    ) -> dict:
        found = self.events_where(events, subject=subject, condition=condition)
        self.assertEqual(len(found), 1, found)
        event = found[0]
        field = self._field_for_condition(condition)
        self.assertEqual(
            event["summary"],
            self._metric_summary(field, value),
            event,
        )
        if state is not None:
            self.assertEqual(event["state"], state, event)
        return event

    def assert_oid_metric(
        self, events: list, *, subject: str, condition: str, oid: str
    ) -> None:
        self.assertRegex(oid, OID_RE, oid)
        self.assert_metric_event(
            events, subject=subject, condition=condition, value=oid
        )

    def assert_absent_oid_metric(
        self, events: list, *, subject: str, condition: str, other_oid: str
    ) -> None:
        event = self.assert_metric_event(
            events, subject=subject, condition=condition, value=None
        )
        text = event["summary"]
        self.assertNotEqual(text, other_oid, event)
        self.assertNotIn(other_oid, text, event)

    def assert_encoded_oid_events_if_present(
        self, events: list, *, condition: str
    ) -> None:
        field = self._field_for_condition(condition)
        self.assertIsNotNone(field, condition)
        prefix = f"{field}="
        for event in self.events_where(events, condition=condition):
            summary = event["summary"]
            self.assertIsInstance(summary, str, event)
            self.assertTrue(summary.startswith(prefix), event)
            encoded = summary[len(prefix) :]
            try:
                value = json.loads(encoded)
            except json.JSONDecodeError:
                self.fail(
                    f"{condition} summary is not field=JSON(value): {event}"
                )
            self.assertEqual(summary, self._metric_summary(field, value), event)
            if value is not None:
                self.assertIsInstance(value, str, event)
                self.assertRegex(value, OID_RE, event)

    def digest_events(self) -> list:
        _, data = self.digest_data()
        events = data["events"]
        self.assert_events_ordered(events)
        return events

    def assert_secret_absent(self, proc) -> None:
        blob = self.combined(proc)
        self.assertNotIn(GIT_SECRET, blob)
        self.assertNotIn("\x1b", blob)


class GitRefsRegistration(_GitRefsFixture):
    """git-refs is a registered adapter; valid params pass check."""

    def test_valid_git_refs_params_pass_check_without_observing(self):
        self.plant_pair()
        self.plant_git_refs()
        before = self.owned_source_bytes()
        data = self.check_ok()
        self.assertGreaterEqual(data["monitors"], 1, data)
        self.assertGreaterEqual(data["conditions"], 2, data)
        self.assertGreaterEqual(data["routes"], 2, data)
        self.assert_state_empty()
        self.assertEqual(self.ls_remote_argvs(), [])
        self.assert_owned_sources(before)


class GitRefsEqualAndDifferent(_GitRefsFixture):
    """Equal pair; each trunk can differ while the other stays equal."""

    def test_two_equal_refs_are_ok_equal_in_configured_order(self):
        oids = self.plant_pair()
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self.assert_secret_absent(proc)
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        self.assert_one_ls_remote(self.git_repo)
        events = self.digest_events()
        freshness = self.events_where(events, condition="refs_changed")
        self.assertEqual(len(freshness), 2, freshness)
        self.assertEqual([e["subject"] for e in freshness], [DEV_REF, MASTER_REF])
        for name, subject in (("dev", DEV_REF), ("master", MASTER_REF)):
            event = self.assert_metric_event(
                events,
                subject=subject,
                condition="refs_changed",
                value="equal",
                state="ok",
            )
            self.assert_utc_observed_at(event["observed_at"], NOW, event)
            self.assert_oid_metric(
                events,
                subject=subject,
                condition="local_changed",
                oid=oids[name],
            )
            self.assert_oid_metric(
                events,
                subject=subject,
                condition="remote_changed",
                oid=oids[name],
            )
        local_ev = self.events_where(events, condition="local_changed")
        remote_ev = self.events_where(events, condition="remote_changed")
        self.assertEqual(len(local_ev), 2, local_ev)
        self.assertEqual(len(remote_ev), 2, remote_ev)
        blob = json.dumps(events)
        self.assertNotIn("missing-local", blob)
        self.assertNotIn("ahead", blob.lower())
        self.assertNotIn("behind", blob.lower())

    def test_dev_different_with_master_equal(self):
        oids = self.plant_pair()
        new_dev = self.advance_origin(DEV, b"live remote dev only\n")
        self.assertNotEqual(new_dev, oids["dev"])
        self.assertEqual(self.oid(self.git_repo, DEV_REF), oids["dev"])
        self.assertEqual(self.tracking_oid(self.git_repo, DEV), oids["dev"])
        self.assertEqual(self.oid(self.git_repo, MASTER_REF), oids["master"])
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self.assert_secret_absent(proc)
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        self.assert_one_ls_remote(self.git_repo)
        events = self.digest_events()
        freshness = self.events_where(events, condition="refs_changed")
        self.assertEqual([e["subject"] for e in freshness], [DEV_REF, MASTER_REF])
        self.assert_metric_event(
            events,
            subject=DEV_REF,
            condition="refs_changed",
            value="different",
            state="ok",
        )
        self.assert_metric_event(
            events,
            subject=MASTER_REF,
            condition="refs_changed",
            value="equal",
            state="ok",
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="local_changed", oid=oids["dev"]
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="remote_changed", oid=new_dev
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="local_changed", oid=oids["master"]
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="remote_changed", oid=oids["master"]
        )
        blob = json.dumps(events)
        self.assertIn(oids["dev"], blob)
        self.assertIn(new_dev, blob)
        self.assertIn(oids["master"], blob)
        self.assertNotIn("ahead", blob.lower())
        self.assertNotIn("behind", blob.lower())
        self.assertNotIn("diverg", blob.lower())

    def test_master_different_with_dev_equal(self):
        oids = self.plant_pair()
        new_master = self.advance_origin(MASTER, b"live remote master only\n")
        self.assertNotEqual(new_master, oids["master"])
        self.assertEqual(self.oid(self.git_repo, DEV_REF), oids["dev"])
        self.assertEqual(self.oid(self.git_repo, MASTER_REF), oids["master"])
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        events = self.digest_events()
        freshness = self.events_where(events, condition="refs_changed")
        self.assertEqual([e["subject"] for e in freshness], [DEV_REF, MASTER_REF])
        self.assert_metric_event(
            events,
            subject=DEV_REF,
            condition="refs_changed",
            value="equal",
            state="ok",
        )
        self.assert_metric_event(
            events,
            subject=MASTER_REF,
            condition="refs_changed",
            value="different",
            state="ok",
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="local_changed", oid=oids["dev"]
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="remote_changed", oid=oids["dev"]
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="local_changed", oid=oids["master"]
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="remote_changed", oid=new_master
        )
        blob = json.dumps(events)
        self.assertIn(new_master, blob)
        self.assertIn(oids["dev"], blob)


class GitRefsMissing(_GitRefsFixture):
    """Each missing-ref row is ok, never unavailable."""

    def test_missing_local_when_remote_present(self):
        oids = self.plant_pair()
        self.delete_local_branch(DEV)
        self.assertEqual(self.tracking_oid(self.git_repo, DEV), oids["dev"])
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        events = self.digest_events()
        self.assert_metric_event(
            events,
            subject=DEV_REF,
            condition="refs_changed",
            value="missing-local",
            state="ok",
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="remote_changed", oid=oids["dev"]
        )
        self.assert_absent_oid_metric(
            events, subject=DEV_REF, condition="local_changed", other_oid=oids["dev"]
        )

    def test_missing_remote_when_local_present(self):
        oids = self.plant_pair()
        self.delete_origin_branch(MASTER)
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        events = self.digest_events()
        self.assert_metric_event(
            events,
            subject=MASTER_REF,
            condition="refs_changed",
            value="missing-remote",
            state="ok",
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="local_changed", oid=oids["master"]
        )
        self.assert_absent_oid_metric(
            events,
            subject=MASTER_REF,
            condition="remote_changed",
            other_oid=oids["master"],
        )

    def test_missing_both_is_ok_not_unavailable(self):
        self.plant_pair()
        self.delete_local_branch(DEV)
        self.delete_origin_branch(DEV)
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        events = self.digest_events()
        self.assert_metric_event(
            events,
            subject=DEV_REF,
            condition="refs_changed",
            value="missing-both",
            state="ok",
        )
        self.assertEqual(self.events_where(events, condition="refs_unavailable"), [])


class GitRefsLiveVsCached(_GitRefsFixture):
    """Tracking refs and missing objects are not live remote truth."""

    def test_stale_tracking_equal_to_local_while_live_remote_differs(self):
        oids = self.plant_pair()
        new_dev = self.advance_origin(DEV, b"stale tracking vs live\n")
        self.assertEqual(self.oid(self.git_repo, DEV_REF), oids["dev"])
        self.assertEqual(self.tracking_oid(self.git_repo, DEV), oids["dev"])
        self.assertNotEqual(new_dev, oids["dev"])
        cat = self.git(self.git_repo, "cat-file", "-t", new_dev, check=False)
        self.assertNotEqual(cat.returncode, 0, "INVALID: new oid already local")
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["unavailable"], 0, counts)
        events = self.digest_events()
        self.assert_metric_event(
            events,
            subject=DEV_REF,
            condition="refs_changed",
            value="different",
            state="ok",
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="local_changed", oid=oids["dev"]
        )
        self.assert_oid_metric(
            events, subject=DEV_REF, condition="remote_changed", oid=new_dev
        )
        blob = json.dumps(events)
        self.assertIn(new_dev, blob)
        self.assertIn(oids["dev"], blob)

    def test_remote_oid_absent_from_local_objects_still_different(self):
        oids = self.plant_pair()
        new_master = self.advance_origin(MASTER, b"unfetched master object\n")
        cat = self.git(self.git_repo, "cat-file", "-t", new_master, check=False)
        self.assertNotEqual(cat.returncode, 0, cat.stdout)
        self.assertEqual(self.oid(self.git_repo, MASTER_REF), oids["master"])
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        events = self.digest_events()
        self.assert_metric_event(
            events,
            subject=MASTER_REF,
            condition="refs_changed",
            value="different",
            state="ok",
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="local_changed", oid=oids["master"]
        )
        self.assert_oid_metric(
            events, subject=MASTER_REF, condition="remote_changed", oid=new_master
        )
        blob = json.dumps(events)
        self.assertIn(new_master, blob)
        self.assertNotIn("ahead", blob.lower())
        self.assertNotIn("behind", blob.lower())


class GitRefsUnavailable(_GitRefsFixture):
    """Query/output failures are unavailable, never a missing-ref row."""

    def _assert_both_unavailable(self, proc, counts) -> list:
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assert_secret_absent(proc)
        self.assertEqual(counts["observations"], 2, counts)
        self.assertEqual(counts["unavailable"], 2, counts)
        events = self.digest_events()
        unavail = self.events_where(events, condition="refs_unavailable")
        self.assertEqual(len(unavail), 2, unavail)
        self.assertEqual([e["subject"] for e in unavail], [DEV_REF, MASTER_REF])
        for event in unavail:
            self.assertEqual(event["state"], "unavailable", event)
            self.assertEqual(event["summary"], "state=unavailable", event)
            blob = json.dumps(event)
            self.assertNotIn("remote-unavailable", blob)
            self.assertNotIn("missing-local", blob)
            self.assertNotIn("missing-remote", blob)
            self.assertNotIn("missing-both", blob)
            self.assertNotIn(GIT_SECRET, blob)
        freshness = self.events_where(events, condition="refs_changed")
        self.assertEqual(len(freshness), 2, freshness)
        self.assertEqual([e["subject"] for e in freshness], [DEV_REF, MASTER_REF])
        for event in freshness:
            self.assert_metric_event(
                events,
                subject=event["subject"],
                condition="refs_changed",
                value="remote-unavailable",
                state="unavailable",
            )
            self.assertNotIn("missing-local", event["summary"])
            self.assertNotIn("missing-remote", event["summary"])
            self.assertNotIn("missing-both", event["summary"])
            self.assertNotIn(GIT_SECRET, json.dumps(event))
        self.assert_encoded_oid_events_if_present(events, condition="local_changed")
        self.assert_encoded_oid_events_if_present(events, condition="remote_changed")
        return unavail

    def test_unreachable_auth_failure_is_unavailable_not_absence(self):
        self.plant_pair()
        self.set_ls_remote(
            mode="exit",
            exit_code=128,
            stdout="",
            stderr=f"fatal: {GIT_SECRET}\n",
        )
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self._assert_both_unavailable(proc, counts)
        self.assert_one_ls_remote(self.git_repo)

    def test_timeout_is_unavailable_not_absence(self):
        self.plant_pair()
        self.set_ls_remote(mode="sleep", sleep_s=3, exit_code=0, stdout="")
        self.plant_git_refs(timeout_s=0.2)
        t0 = time.monotonic()
        proc, counts = self.run_counts()
        elapsed = time.monotonic() - t0
        self._assert_both_unavailable(proc, counts)
        self.assertLess(elapsed, 2.5)
        self.assert_one_ls_remote(self.git_repo)

    def test_malformed_output_is_unavailable_not_absence(self):
        self.plant_pair()
        self.set_ls_remote(
            mode="stdout",
            stdout="NOT-AN-OID\trefs/heads/dev\n",
            stderr=f"{GIT_SECRET}\n",
        )
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self._assert_both_unavailable(proc, counts)

    def test_duplicate_rows_are_unavailable_not_absence(self):
        oids = self.plant_pair()
        line = f"{oids['dev']}\t{DEV_REF}\n{oids['dev']}\t{DEV_REF}\n"
        self.set_ls_remote(mode="stdout", stdout=line)
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self._assert_both_unavailable(proc, counts)

    def test_stdout_overflow_is_unavailable_not_absence(self):
        self.plant_pair()
        huge = ("a" * (PIPE_CAP + 1)) + "\n"
        self.assertGreater(len(huge.encode("utf-8")), PIPE_CAP)
        self.set_ls_remote(mode="stdout", stdout=huge)
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self._assert_both_unavailable(proc, counts)


class GitRefsQueryDedupDisabled(_GitRefsFixture):
    """One argv, first/transition events, restart dedup, disabled silence."""

    def test_one_argv_covers_both_branches_even_with_both_routes(self):
        self.plant_pair()
        self.plant_git_refs()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 2, counts)
        self.assert_one_ls_remote(self.git_repo)
        events = self.digest_events()
        self.assertTrue(self.events_where(events, condition="refs_changed"))
        self.assertTrue(self.events_where(events, condition="refs_unavailable") == [])

    def test_first_transition_unchanged_restart_dedup_and_recovery(self):
        oids = self.plant_pair()
        self.plant_git_refs()
        _, first = self.run_counts(now=NOW)
        self.assertGreaterEqual(first["events"], 2, first)
        self.assertEqual(len(self.ls_remote_argvs()), 1)
        _, persist = self.run_counts(now=NOW_30)
        self.assertEqual(persist["events"], 0, persist)
        self.assertEqual(len(self.ls_remote_argvs()), 2)
        saved = list(_tree_files(self.state))
        self.assertTrue(saved, "INVALID: first run wrote no state")
        _, again = self.run_counts(now=NOW_60)
        self.assertEqual(again["events"], 0, again)
        new_dev = self.advance_origin(DEV, b"recovery transition\n")
        self.assertNotEqual(new_dev, oids["dev"])
        _, changed = self.run_counts(now=NOW_400)
        self.assertGreaterEqual(changed["events"], 1, changed)
        events = self.digest_events()
        freshness = self.events_where(
            events, subject=DEV_REF, condition="refs_changed"
        )
        self.assertGreaterEqual(len(freshness), 2, freshness)
        self.assertEqual(
            freshness[0]["summary"],
            self._metric_summary("freshness", "equal"),
            freshness[0],
        )
        self.assertEqual(
            freshness[-1]["summary"],
            self._metric_summary("freshness", "different"),
            freshness[-1],
        )
        self.git(self.git_repo, "fetch", REMOTE_NAME, DEV)
        self.git(self.git_repo, "checkout", DEV)
        self.git(self.git_repo, "reset", "--hard", new_dev)
        self.assertEqual(self.oid(self.git_repo, DEV_REF), new_dev)
        _, recovered = self.run_counts(now="2026-09-04T12:10:00Z")
        self.assertGreaterEqual(recovered["events"], 1, recovered)
        events = self.digest_events()
        freshness = self.events_where(
            events, subject=DEV_REF, condition="refs_changed"
        )
        self.assertEqual(
            freshness[-1]["summary"],
            self._metric_summary("freshness", "equal"),
            freshness[-1],
        )

    def test_disabled_or_removed_source_makes_no_query(self):
        self.plant_pair()
        self.plant_git_refs(enabled=False)
        data = self.check_ok()
        self.assertGreaterEqual(data["monitors"], 1, data)
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(counts["observations"], 0, counts)
        self.assertEqual(counts["events"], 0, counts)
        self.assertEqual(self.ls_remote_argvs(), [])
        other = self.scratch / "state-removed"
        other.mkdir()
        self.state = other
        self.plant_config(self.registry())
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 0, counts)
        self.assertEqual(self.ls_remote_argvs(), [])


class GitRefsRefusalsAndRepoState(_GitRefsFixture):
    """Invalid params refuse before observation; Git state is untouched."""

    def test_invalid_params_refuse_before_any_query(self):
        self.plant_pair()
        cases = {
            "timeout-zero": (self.git_refs_reg(timeout_s=0), "timeout"),
            "timeout-31": (self.git_refs_reg(timeout_s=31), "timeout"),
            "timeout-bool": (
                self.git_refs_reg(
                    params={
                        "repo": self.relative_repo(),
                        "remote": REMOTE_NAME,
                        "branches": [DEV, MASTER],
                        "timeout_s": True,
                    }
                ),
                "timeout",
            ),
            "empty-branches": (self.git_refs_reg(branches=[]), "branches"),
            "duplicate-branches": (self.git_refs_reg(branches=[DEV, DEV]), "branches"),
            "pattern-branch": (
                self.git_refs_reg(branches=["dev*", MASTER]),
                "branches",
            ),
            "leading-dash": (
                self.git_refs_reg(branches=["-dev", MASTER]),
                "branches",
            ),
            "bad-remote": (self.git_refs_reg(remote="origin/extra"), "remote"),
            "extra-param": (
                self.git_refs_reg(
                    params={
                        "repo": self.relative_repo(),
                        "remote": REMOTE_NAME,
                        "branches": [DEV, MASTER],
                        "timeout_s": 5,
                        "fetch": True,
                    }
                ),
                "fetch",
            ),
        }
        self.assertEqual(len(cases), 9)
        for name, (payload, needle) in cases.items():
            with self.subTest(name):
                log = self.world.tools.log_path("git")
                if log.exists():
                    log.unlink()
                    self.assertFalse(log.exists())
                self.plant_config(payload)
                before_state = _tree_files(self.state)
                proc = self.check()
                self.assert_refusal(proc, "config")
                err = (proc.stderr or "").lower()
                self.assertIn(needle, err, proc.stderr)
                self.assertNotIn("choose a registered observation adapter", err)
                proc_run = self.run_once()
                self.assert_refusal(proc_run, "config")
                self.assertIn(needle, (proc_run.stderr or "").lower(), proc_run.stderr)
                self.assertEqual(_tree_files(self.state), before_state)
                self.assertEqual(self.ls_remote_argvs(), [])

    def test_observation_leaves_refs_index_worktree_config_untouched(self):
        oids = self.plant_pair()
        self.git(self.git_repo, "checkout", DEV)
        extra = self.git_repo / "dirty-tracked.txt"
        self._plant_bytes(extra, b"tracked then dirty\n")
        self.git(self.git_repo, "add", "dirty-tracked.txt")
        self.git(self.git_repo, "commit", "-m", "local only commit\n")
        local_commit = self.oid(self.git_repo, DEV_REF)
        self.assertNotEqual(local_commit, oids["dev"])
        self._plant_bytes(extra, b"dirty working tree\n")
        self.assertEqual(extra.read_bytes(), b"dirty working tree\n")
        untracked = self.git_repo / "untracked-aa41.txt"
        self._plant_bytes(untracked, b"untracked\n")
        add_wt = run(
            [
                self.real_git,
                "-C",
                str(self.git_repo),
                "worktree",
                "add",
                str(self.other_wt),
                MASTER,
            ],
            env=self.git_env(),
            cwd=self.cwd,
            timeout=60,
        )
        self.assertEqual(add_wt.exit, 0, add_wt.stderr)
        self.assertTrue((self.other_wt / "README").is_file())
        before_repo = self.fingerprint(self.git_repo)
        before_other = self.fingerprint(self.other_wt)
        before_origin = self.git(
            self.origin, "show-ref", "--head", check=False
        ).stdout
        porcelain = self.git(
            self.git_repo, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        self.assertIn("dirty-tracked.txt", porcelain)
        self.assertIn("untracked-aa41.txt", porcelain)
        self.plant_git_refs()
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(counts["observations"], 2, counts)
        self.assert_one_ls_remote(self.git_repo)
        self.assertEqual(self.fingerprint(self.git_repo), before_repo)
        self.assertEqual(self.fingerprint(self.other_wt), before_other)
        after_origin = self.git(
            self.origin, "show-ref", "--head", check=False
        ).stdout
        self.assertEqual(after_origin, before_origin)
        self.assertEqual(self.oid(self.git_repo, DEV_REF), local_commit)
        self.assertEqual(extra.read_bytes(), b"dirty working tree\n")
        self.assertEqual(untracked.read_bytes(), b"untracked\n")
        state_files = _tree_files(self.state)
        for name in state_files:
            self.assertFalse(
                str(self.git_repo.resolve()) in name,
                f"monitor state escaped into repo: {name}",
            )


if __name__ == "__main__":
    unittest.main()
