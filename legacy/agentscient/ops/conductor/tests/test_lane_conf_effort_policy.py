"""The tracked lane.conf carries the effort policy per job.

Owner, 2026-09-12: "effort is something we need to look at - per role -
xhigh or max is ok for the initial planing - but that's it. Everything
else should be high". PR #20 (2026-09-04) made effort resolvable per
harness and job (`EFFORT_<HARNESS>_<JOB>` beats `EFFORT_<HARNESS>`), but
the tracked conf only ever set the per-harness rows, Grok and Codex at
xhigh. This test pins the policy onto the file the dispatcher reads.

One producer (Claude Fable 5.1) wrote this test and the conf change under
hand grant 155; the second producer is absent by the owner's word.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
CONF = REPO / "ops" / "lane.conf"
WRAPPER = REPO / "ops" / "conductor" / "tools" / "dispatch.sh"
DECL_RE = re.compile(r"^([A-Z][A-Z0-9_]*)='([^']*)'\s*$")
PLAN_EFFORTS = {"high"}  # owner, 2026-09-13: token thrift #4; xhigh/max only by --effort at dispatch
FLOOR = "high"


def _conf(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = DECL_RE.match(line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


class EffortPolicyIsPerJobInTheTrackedConf(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CONF.is_file(), f"missing {CONF}")
        self.world = World(prefix="lane-conf-effort-")
        self.addCleanup(self.world.close)
        self.conf_path = self.world.stage(CONF)
        self.wrapper_path = self.world.stage(WRAPPER)
        self.conf = _conf(self.conf_path)
        self.efforts = {k: v for k, v in self.conf.items() if k.startswith("EFFORT_")}
        self.assertTrue(self.efforts, "lane.conf declares no EFFORT_ rows")

    def test_every_harness_default_is_high(self):
        for harness in ("GROK", "CODEX", "CLAUDE"):
            self.assertEqual(self.efforts.get(f"EFFORT_{harness}"), FLOOR, harness)

    def test_planning_is_the_only_job_above_high(self):
        self.assertIn("EFFORT_CLAUDE_PLAN", self.efforts)
        self.assertIn(self.efforts["EFFORT_CLAUDE_PLAN"], PLAN_EFFORTS)
        for key, value in self.efforts.items():
            if key.endswith("_PLAN"):
                self.assertIn(value, PLAN_EFFORTS, key)
            else:
                self.assertEqual(value, FLOOR, key)

    def test_the_wrapper_reads_the_per_job_row_first(self):
        text = self.wrapper_path.read_text(encoding="utf-8")
        self.assertIn('effort_job="EFFORT_${harness^^}_${job_key}"', text)
        self.assertIn("job_key=${job^^}", text)


if __name__ == "__main__":
    unittest.main()
