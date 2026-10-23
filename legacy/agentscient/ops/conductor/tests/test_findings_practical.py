# testkit: exempt inherits World and run from test_findings._FindingsFixture
"""The findings store carries a reviewer's practical label.

A finding may carry `practical` (yes|no) and `failing` beside its four
fields (ops/devlane/task/envelope.py PRACTICAL_FIELDS). Import stores
both; the export adds them to a row only when the finding carried them,
so every row exported before the label is unchanged. A store created
before the columns existed gains them on first use.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

from test_findings import REC, SIBLINGS, TOOL, _FindingsFixture

OLD_KEYS = ["claim", "disposed_at", "disposed_by", "disposition", "evidence", "harness", "job", "key",
            "model", "n", "record", "ref", "reproduce", "severity", "stage", "unit", "verdict", "where"]
OLD_SCHEMA = """
CREATE TABLE findings (
    key TEXT PRIMARY KEY, record TEXT NOT NULL, job TEXT, stage TEXT, harness TEXT, model TEXT,
    unit TEXT, ref TEXT, verdict TEXT, n INTEGER, severity TEXT, "where" TEXT, claim TEXT, reproduce TEXT,
    disposition TEXT NOT NULL DEFAULT 'open', evidence TEXT, disposed_at TEXT, disposed_by TEXT
)
"""


class PracticalLabelInTheStore(_FindingsFixture):
    def setUp(self):
        super().setUp()
        # findings.py reads records under its own repository root, three
        # levels up: stage it inside the world's repo, not beside it.
        self.staged = self.world.stage(TOOL, "repo/ops/conductor/tools/findings.py", siblings=SIBLINGS)
        self.records = self.staged.parents[3] / ".dev" / "records" / "dispatches"
        self.records.mkdir(parents=True)

    def plant_record(self):
        findings = [
            {"severity": "p2", "where": "ops/x.py", "claim": "old shape", "reproduce": None},
            {"severity": "p1", "where": "ops/y.py", "claim": "labelled", "reproduce": "python3 y",
             "practical": "yes", "failing": "ops/bin/dispatch.sh review --for A-1"},
            {"severity": "p3", "where": "ops/z.py", "claim": "not practical", "reproduce": "python3 z",
             "practical": "no", "failing": None},
        ]
        record = {"id": REC, "job": "adversarial-review", "stage": "review", "unit": "lane/x",
                  "harness": {"name": "grok"}, "model": {"ran": "grok-4.6"},
                  "result": {"envelope": {"status": "ok", "verdict": "changes", "findings": findings}}}
        self._plant_bytes(self.records / f"{REC}.json", (json.dumps(record) + "\n").encode("utf-8"))

    def exported(self):
        self.plant_record()
        proc = self.invoke(["import"])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        self.assertIn("imported 3 finding(s)", proc.stdout)
        out = self.scratch / "findings.jsonl"
        proc = self.invoke(["export", "--write", str(out)])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return {row["n"]: row for row in map(json.loads, out.read_text(encoding="utf-8").splitlines())}

    def test_a_finding_without_the_label_exports_the_old_row(self):
        self.assertEqual(sorted(self.exported()[1]), OLD_KEYS)

    def test_a_practical_finding_exports_its_failing_invocation(self):
        row = self.exported()[2]
        self.assertEqual((row["practical"], row["failing"]),
                         ("yes", "ops/bin/dispatch.sh review --for A-1"))

    def test_a_finding_that_is_not_practical_exports_no(self):
        row = self.exported()[3]
        self.assertEqual((row["practical"], row["failing"]), ("no", None))

    def test_a_store_from_before_the_label_gains_its_columns(self):
        store = self.world.xdg_state / "agentscient" / "findings.db"
        store.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(store)
        con.execute(OLD_SCHEMA)
        con.commit()
        con.close()
        self.assertEqual(self.exported()[2]["practical"], "yes")


if __name__ == "__main__":
    unittest.main()
