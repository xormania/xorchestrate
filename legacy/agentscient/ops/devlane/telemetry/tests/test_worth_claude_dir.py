"""Item 48/R6: --claude-dir has no argparse default.

Resolution is the flag, else laneconf.get("CLAUDE_DIR"). A missing key
is exit 2, class conf-key. A relative CLAUDE_DIR is against Path.home().
The tracked conf declares CLAUDE_DIR='.claude/projects'.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

import test_worth as worth_contract

from ops.testkit import World, plant_text, run

NOW = "2026-08-22T12:00:00.000Z"
WINDOW = ("--since", "2026-08-22T09:00:00.000Z", "--until", "2026-08-22T12:00:00.000Z")

TRACKED_CONF = Path(__file__).resolve().parents[3] / "lane.conf"

REQUIRED_CONF = {
    "LANE_NAME": "agentscient",
    "TRUNK_BRANCH": "dev",
    "RELEASE_BRANCH": "master",
    "REPO_SLUG": "uscient/agentscient",
    "CHECK_NAMES": "lane app",
    "WF_AGENT_DEFAULT": "Conf Agent <conf@example.invalid>",
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

REFUSAL = re.compile(
    r"^conf-key: expected .+; found .+; needed .+$",
    re.MULTILINE,
)


class ClaudeDirFromConf(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.w = worth_contract.WorthContractTests(
            "test_unusable_arguments_exit_two_and_explain_the_reason"
        )
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.world = World()
        self.addCleanup(self.world.close)
        self.root = self.w.root
        self.repo = self.w.repo
        self.claude = self.w.claude
        self.codex = self.w.codex
        self.grok = self.w.grok
        self.home = self.world.home
        self.conf_path = self.root / "lane.conf"
        self._plant_claude = self.w._plant_claude
        self._session_ids = self.w._session_ids

    def _plant_conf(self, **overrides):
        omit = tuple(overrides.pop("omit", ()) or ())
        keys = dict(REQUIRED_CONF)
        keys.update({name: str(value) for name, value in overrides.items()})
        for name in omit:
            keys.pop(name, None)
        lines = ["# scratch lane conf for worth --claude-dir\n"]
        for name, value in keys.items():
            lines.append(f"{name}='{value}'\n")
        text = "".join(lines)
        plant_text(self.conf_path, text)
        landed = self.conf_path.read_text(encoding="utf-8")
        self.assertEqual(landed, text, "conf plant did not land intact")
        for name in omit:
            self.assertNotIn(f"{name}=", landed)
        for name, value in keys.items():
            self.assertIn(f"{name}='{value}'", landed)
        return self.conf_path

    def _env(self, extra=None):
        extra_env = {
            "LANE_CONF": str(self.conf_path),
            "TZ": "Etc/GMT+11",
        }
        if extra:
            extra_env.update(extra)
        return self.world.env(extra=extra_env)

    def _run(
        self,
        verb,
        *args,
        now=NOW,
        claude_dir="omit",
        extra_env=None,
        cwd=None,
    ):
        cmd = [
            sys.executable,
            str(worth_contract.WORTH),
            verb,
            *args,
            "--now",
            now,
            "--repo",
            str(self.repo),
            "--codex-dir",
            str(self.codex),
            "--grok-dir",
            str(self.grok),
        ]
        if claude_dir == "flag":
            cmd.extend(["--claude-dir", str(self.claude)])
        elif claude_dir is not None and claude_dir != "omit":
            cmd.extend(["--claude-dir", str(claude_dir)])
        self.assertNotIn(
            "CLAUDE_DIR",
            self._env(extra_env),
            "CLAUDE_DIR must not leak in from the host environment",
        )
        return run(
            cmd,
            cwd=str(cwd or self.root),
            env=self._env(extra_env),
            timeout=30,
        )

    def _json_waste(self, *args, **kwargs):
        proc = self._run("waste", *WINDOW, "--format", "json", *args, **kwargs)
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, dict)
        return proc, data

    def test_tracked_conf_declares_claude_dir_relative_to_home(self):
        self.assertTrue(TRACKED_CONF.is_file(), TRACKED_CONF)
        text = TRACKED_CONF.read_text(encoding="utf-8")
        self.assertIn(
            "CLAUDE_DIR='.claude/projects'",
            text,
            "tracked ops/lane.conf must declare CLAUDE_DIR='.claude/projects'",
        )

    def test_omitting_claude_dir_without_conf_key_is_conf_key(self):
        """Scenario: --claude-dir has no argparse default"""
        self._plant_conf()
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertNotIn("CLAUDE_DIR=", planted)
        for verb in ("waste", "report"):
            with self.subTest(verb=verb):
                proc = self._run(verb, *WINDOW, "--format", "json")
                self.assertEqual(proc.exit, 2, proc)
                self.assertEqual(proc.stdout, "")
                self.assertRegex(proc.stderr.strip(), REFUSAL)
                self.assertIn("conf-key", proc.stderr)
                self.assertIn("CLAUDE_DIR", proc.stderr)

    def test_relative_claude_dir_from_conf_resolves_against_home(self):
        """Scenario: CLAUDE_DIR from lane conf is resolved relative to HOME"""
        self._plant_conf(CLAUDE_DIR="from-conf")
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("CLAUDE_DIR='from-conf'", planted)

        decoy = self.home / ".claude" / "projects"
        self.w.claude = decoy
        self._plant_claude("decoy-default", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 88_001,
        }])
        self.assertTrue(
            (decoy / self.w._slug() / "decoy-default.jsonl").is_file(),
            "the hardcoded-default decoy did not land",
        )

        resolved = self.home / "from-conf"
        self.w.claude = resolved
        self._plant_claude("from-conf-sess", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 40,
        }])
        self.assertTrue(
            (resolved / self.w._slug() / "from-conf-sess.jsonl").is_file(),
        )

        _proc, data = self._json_waste()
        ids = self._session_ids(data)
        self.assertIn("from-conf-sess", ids)
        self.assertNotIn("decoy-default", ids)
        self.assertNotIn("88001", json.dumps(data))

    def test_relative_claude_dir_does_not_resolve_against_cwd(self):
        self._plant_conf(CLAUDE_DIR="from-conf")
        cwd = self.root / "other-cwd"
        cwd.mkdir()
        decoy = cwd / "from-conf"
        self.w.claude = decoy
        self._plant_claude("cwd-decoy", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 77_001,
        }])
        self.assertTrue(
            (decoy / self.w._slug() / "cwd-decoy.jsonl").is_file(),
        )

        resolved = self.home / "from-conf"
        self.w.claude = resolved
        self._plant_claude("home-sess", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 40,
        }])

        _proc, data = self._json_waste(cwd=cwd)
        ids = self._session_ids(data)
        self.assertIn("home-sess", ids)
        self.assertNotIn("cwd-decoy", ids)
        self.assertNotIn("77001", json.dumps(data))

    def test_absolute_claude_dir_from_conf_is_used_as_is(self):
        self._plant_conf(CLAUDE_DIR=str(self.claude))
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertIn(f"CLAUDE_DIR='{self.claude}'", planted)
        self._plant_claude("abs-sess", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 40,
        }])
        _proc, data = self._json_waste()
        self.assertIn("abs-sess", self._session_ids(data))


if __name__ == "__main__":
    unittest.main()
