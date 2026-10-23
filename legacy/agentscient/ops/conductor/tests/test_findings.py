"""Findings ledger reads LANE_NAME from the lane conf.

Contract (only): the unit 1 lane-conf edge — every state-root reader
reads LANE_NAME through the reader. findings.py is the subject and is
not read here. Tests plant a scratch conf through LANE_CONF and never
write the tracked file.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "findings.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")
LIVE_STORE = Path.home() / ".local" / "state" / "agentscient" / "findings.db"
AGENT = "Findings Tester <findings-tester@example.invalid>"
REC = "20260901T000000Z-review-grok-aaaaaa"
class _FindingsFixture(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="findings-laneconf-")
        self._live = self.world.watch(LIVE_STORE)
        root = self.world.root
        self.home = self.world.home
        self.scratch = root / "stage"
        self.records = self.world.path("repo/.dev/records/dispatches")
        self.records.mkdir(parents=True)
        self.staged = self.world.stage(
            TOOL,
            "findings.py",
            siblings=SIBLINGS if TOOL.is_file() else (),
        )
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under the world's state root is asserted.
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='agentscient'\n")
        self.assertFalse(
            (self.world.xdg_state / "agentscient" / "findings.db").exists(),
            "plant: default store must start absent",
        )

    def tearDown(self):
        try:
            self._live.assert_unchanged(self, f"live store mutated: {LIVE_STORE}")
        finally:
            self.world.close()

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = self.world.env({
            "WF_AGENT": AGENT,
            "LANE_CONF": str(self.conf),
        })
        if extra:
            env.update(extra)
        return env

    def invoke(self, argv: list[str], *, extra_env: dict[str, str] | None = None):
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=self.records.parents[2],
            env=self.env(extra_env),
            timeout=60,
        )


class LaneNameSelectsStore(_FindingsFixture):
    """LANE_NAME from the conf selects findings.db under the world's state root."""

    def test_lane_name_from_conf_selects_the_store(self):
        conf = self.scratch / "lane.conf"
        text = (
            "LANE_NAME='lanex'\n"
            "TRUNK_BRANCH='trunkx'\n"
            "RELEASE_BRANCH='relx'\n"
            "REPO_SLUG='org/projx'\n"
            "CHECK_NAMES='checkx'\n"
            "WF_AGENT_DEFAULT='Conf Agent <conf@example.invalid>'\n"
            "LANE_AUTHOR='Lane Owner <lane-owner@example.invalid>'\n"
            "MODEL_GROK='model-grok'\n"
            "MODEL_CODEX='model-codex'\n"
            "MODEL_CLAUDE_READ='model-claude-read'\n"
            "MODEL_CLAUDE_PLAN='model-claude-plan'\n"
            "EFFORT_GROK='high'\n"
            "EFFORT_CODEX='high'\n"
            "EFFORT_CLAUDE='high'\n"
            "WALL_DEFAULT='900'\n"
            "SCOPE_CAP='1024'\n"
        )
        self._plant_bytes(conf, text.encode("utf-8"))
        marker = "laneconf-findings-store-marker"
        envelope = {
            "id": REC,
            "lane": "dev",
            "stage": "review",
            "unit": "lane/lane-conf",
            "job": "adversarial-review",
            "role": "read",
            "status": "closed",
            "harness": {"name": "grok"},
            "model": {"ran": "grok-4.6"},
            "result": {
                "envelope": {
                    "status": "ok",
                    "verdict": "changes",
                    "findings": [
                        {
                            "severity": "p1",
                            "where": "ops/x.py",
                            "claim": marker,
                            "reproduce": None,
                        }
                    ],
                }
            },
        }
        rec_path = self.records / f"{REC}.json"
        payload = json.dumps(envelope, indent=2) + "\n"
        self._plant_bytes(rec_path, payload.encode("utf-8"))
        self.assertIn(marker, rec_path.read_text(encoding="utf-8"))
        lanex = self.world.xdg_state / "lanex" / "findings.db"
        default = self.world.xdg_state / "agentscient" / "findings.db"
        self.assertFalse(lanex.exists())
        self.assertFalse(default.exists())
        proc = self.invoke(["import"], extra_env={"LANE_CONF": str(conf)})
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        self.assertTrue(lanex.is_file(), f"store missing under lanex: {lanex}")
        self.assertFalse(
            default.exists(),
            f"store used the literal lane name: {default}",
        )


if __name__ == "__main__":
    unittest.main()
