"""Launcher reads of the lane conf, written from the unit 1 contract.

Contract (only): the unit 1 contract's launch.py edge — WALL_DEFAULT,
SCOPE_CAP, LANE_NAME, TRUNK_BRANCH, RELEASE_BRANCH; a missing key is
refusal `conf-key`, exit 3. launch.py is the subject and is not read
here. Tests plant a scratch conf through LANE_CONF on the existing
launch_support fixtures and never write the tracked file.

The existing `test_launch_verbs.py` and `test_record_paths.py` keep
pinning `agentscient` and stay green: the tracked conf says so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import launch_support as ls

from ops.testkit import World, run  # noqa: F401 - World via _TempLaunch

CONF_ENV = "LANE_CONF"

REQUIRED_DEFAULTS = {
    "LANE_NAME": "agentscient",
    "TRUNK_BRANCH": "dev",
    "RELEASE_BRANCH": "master",
    "REPO_SLUG": "uscient/agentscient",
    "CHECK_NAMES": "lane app",
    "WF_AGENT_DEFAULT": ls.AGENT,
    "LANE_AUTHOR": "Lane Owner <lane-owner@example.invalid>",
    "MODEL_GROK": "model-grok",
    "MODEL_CODEX": "model-codex",
    "MODEL_CLAUDE_READ": "model-claude-read",
    "MODEL_CLAUDE_PLAN": "model-claude-plan",
    "EFFORT_GROK": "high",
    "EFFORT_CODEX": "high",
    "EFFORT_CLAUDE": "high",
    "WALL_DEFAULT": "900",
    "SCOPE_CAP": "1024",
}

LAUNCHER_KEYS = (
    "WALL_DEFAULT",
    "SCOPE_CAP",
    "LANE_NAME",
    "TRUNK_BRANCH",
    "RELEASE_BRANCH",
)


class _LaneConfLaunch(ls._TempLaunch):
    """Scratch LANE_CONF beside the existing throwaway repo fixture."""

    def setUp(self):
        self._lane_conf_was = os.environ.get(CONF_ENV)
        super().setUp()
        self.conf_path = self.home / "lane.conf"
        self.plant_conf()
        os.environ[CONF_ENV] = str(self.conf_path)

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            if self._lane_conf_was is None:
                os.environ.pop(CONF_ENV, None)
            else:
                os.environ[CONF_ENV] = self._lane_conf_was

    def plant_conf(self, **overrides: object) -> Path:
        omit = tuple(overrides.pop("omit", ()) or ())
        keys = dict(REQUIRED_DEFAULTS)
        for name, value in overrides.items():
            keys[name] = str(value)
        for name in omit:
            keys.pop(name, None)
        lines = ["# scratch lane conf\n"]
        for name, value in keys.items():
            lines.append(f"{name}='{value}'\n")
        text = "".join(lines)
        self.world.plant_text(self.conf_path, text)
        landed = self.conf_path.read_text(encoding="utf-8")
        self.assertEqual(landed, text, "conf plant did not land intact")
        for name, value in keys.items():
            self.assertIn(name, landed)
            self.assertIn(value, landed)
        os.environ[CONF_ENV] = str(self.conf_path)
        return self.conf_path

    def git(self, *args):
        return run(
            ["git", *args], cwd=self.repo, env=self.env, timeout=60, expect=0,
        )


class WallDefaultFromConf(_LaneConfLaunch):
    """A catalogue entry without caps and WALL_DEFAULT='7' records timeout
    7.0 with timeout_source `default`. Every job declares its caps since
    2026-09-16, so the capless entry is planted (ls.strip_job_caps)."""

    def test_wall_default_from_conf_is_recorded_as_timeout_source_default(self):
        self.assertNotIn("DISPATCH_TIMEOUT", os.environ)
        ls.strip_job_caps(self, "plan")
        jobs = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        self.assertNotIn("caps", jobs["plan"])
        self.plant_conf(WALL_DEFAULT="7")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("WALL_DEFAULT='7'", planted)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertIn("caps", rec)
        caps = rec["caps"]
        self.assertEqual(float(caps["timeout"]), 7.0)
        self.assertNotEqual(float(caps["timeout"]), 900.0)
        self.assertEqual(caps["timeout_source"], "default")


class ScopeCapFromConf(_LaneConfLaunch):
    """SCOPE_CAP='10' and an 11-byte --scope refuse scope-cap naming 11."""

    def test_scope_cap_from_conf_refuses_one_byte_over_naming_the_count(self):
        self.plant_conf(SCOPE_CAP="10")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("SCOPE_CAP='10'", planted)
        scope = "x" * 11
        self.assertEqual(len(scope.encode("utf-8")), 11)
        code, out, err = self.dispatch(self.argv_for(
            stage="plan", scope=scope,
        ))
        self.assert_wire_refusal(
            code, out, err, ident="scope-cap",
            named=("11",),
            phrases=["scope-cap", "11"],
        )
        self.assert_not_started()


class LaneNameJobsRoot(_LaneConfLaunch):
    """LANE_NAME='lanex' puts the jobs root at <xdg>/lanex/dispatch."""

    def test_lane_name_from_conf_selects_the_xdg_jobs_root(self):
        os.environ.pop("DISPATCH_JOBS", None)
        self.plant_conf(LANE_NAME="lanex")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("LANE_NAME='lanex'", planted)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        expected = (self.world.xdg_state / "lanex" / "dispatch").resolve()
        root = Path(rec["snapshot"]["root"]).resolve()
        self.assertTrue(
            str(root).startswith(str(expected) + os.sep) or root == expected,
            f"jobs root should sit under {expected}, snapshot is {root}",
        )
        self.assertNotIn(
            f"{os.sep}agentscient{os.sep}",
            str(root),
        )


class TrunkBranchRecordTarget(_LaneConfLaunch):
    """TRUNK_BRANCH='trunkx' and a checkout on trunkx refuse
    record-target; a checkout on `dev` with that conf is not refused
    for that reason."""

    def test_checkout_on_the_conf_trunk_refuses_record_target(self):
        self.plant_conf(TRUNK_BRANCH="trunkx", RELEASE_BRANCH="relx")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("TRUNK_BRANCH='trunkx'", planted)
        self.git("checkout", "-b", "trunkx")
        code, out, err = self.dispatch(
            self.argv_for(lineage="trunkx", stage="plan"),
        )
        self.assert_wire_refusal(
            code, out, err, ident="record-target",
            named=("trunkx",),
            phrases=["record-target", "trunkx"],
        )
        self.assertEqual(self.job_dirs(), [])
        self.assert_not_started()

    def test_checkout_on_dev_is_not_record_target_when_trunk_is_elsewhere(self):
        self.plant_conf(TRUNK_BRANCH="trunkx", RELEASE_BRANCH="relx")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("TRUNK_BRANCH='trunkx'", planted)
        self.assertIn("RELEASE_BRANCH='relx'", planted)
        self.git("checkout", "-b", "dev")
        rec, *_ = self.launch_ok(
            self.argv_for(lineage="dev", stage="plan"),
        )
        self.assertEqual(rec.get("status"), "closed")
        text = str(rec)
        self.assertNotIn("record-target", text)

    def test_checkout_on_the_conf_release_refuses_record_target(self):
        self.plant_conf(TRUNK_BRANCH="trunkx", RELEASE_BRANCH="relx")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("RELEASE_BRANCH='relx'", planted)
        self.git("checkout", "-b", "relx")
        code, out, err = self.dispatch(
            self.argv_for(lineage="relx", stage="plan"),
        )
        self.assert_wire_refusal(
            code, out, err, ident="record-target",
            named=("relx",),
            phrases=["record-target", "relx"],
        )
        self.assertEqual(self.job_dirs(), [])
        self.assert_not_started()


class RecordCommitAuthorFromConf(_LaneConfLaunch):
    """A11: record commits use LANE_AUTHOR from the conf, not a
    hardcoded owner identity."""

    HARDCODED = "xormania <127287135+xormania@users.noreply.github.com>"

    def test_record_commit_author_is_lane_author_from_conf(self):
        ident = "Conf Author <conf-author@example.invalid>"
        self.assertNotEqual(ident, self.HARDCODED)
        self.assertNotEqual(ident, ls.AGENT)
        self.plant_conf(LANE_AUTHOR=ident)
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("LANE_AUTHOR='Conf Author <conf-author@example.invalid>'", planted)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        rel = f".dev/records/dispatches/{rec['id']}.json"
        path = self.repo / rel
        self.assertTrue(path.is_file(), path)
        sha = self.git("log", "-1", "--format=%H", "--", rel).stdout.strip()
        self.assertRegex(sha, r"^[0-9a-f]{40}$", rel)
        author = self.git(
            "log", "-1", "--format=%an <%ae>", sha,
        ).stdout.strip()
        self.assertEqual(author, ident)
        self.assertNotEqual(author, self.HARDCODED)
        committer = self.git(
            "log", "-1", "--format=%cn <%ce>", sha,
        ).stdout.strip()
        self.assertEqual(committer, ident)
        self.assertNotEqual(committer, self.HARDCODED)


class MissingLauncherKeyIsConfKey(_LaneConfLaunch):
    """A conf missing any launcher key refuses conf-key naming the key
    and the file, exit 3, before anything is minted and without a child."""

    def test_missing_launcher_key_refuses_conf_key_before_any_mint(self):
        self.assertEqual(len(LAUNCHER_KEYS), 5, LAUNCHER_KEYS)
        for key in LAUNCHER_KEYS:
            with self.subTest(key=key):
                self.plant_conf(omit=(key,))
                planted = self.conf_path.read_text(encoding="utf-8")
                self.assertNotIn(f"{key}=", planted)
                self.assertTrue(self.conf_path.is_file())
                if self.start_witness.is_file():
                    self.start_witness.unlink()
                before = list(self.jobs_root.iterdir())
                code, out, err = self.dispatch(self.argv_for(stage="plan"))
                self.assert_wire_refusal(
                    code, out, err, ident="conf-key",
                    named=(key, str(self.conf_path)),
                    phrases=["conf-key", key],
                )
                self.assert_not_started()
                self.assertEqual(list(self.jobs_root.iterdir()), before)


if __name__ == "__main__":
    import unittest
    unittest.main()
