"""A findings store from before the label keeps its dispositions.

Written from ops/conductor/findings/README.md ("the store holds both
columns and the export writes them only on a row whose finding carried
them, so rows exported before the label keep their shape"), by a
producer other than the author of findings.py's label (02f36400). The
store on a host that ran findings.py before the change holds disposed
rows; the first import after it adds the columns to that store, and the
dispositions a session wrote must survive it.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

from test_findings import REC, SIBLINGS, TOOL, _FindingsFixture

from ops.testkit import World

ORIGIN = "second producer on #273, 02f36400"
OLD_KEYS = ["claim", "disposed_at", "disposed_by", "disposition", "evidence", "harness", "job", "key",
            "model", "n", "record", "ref", "reproduce", "severity", "stage", "unit", "verdict", "where"]
# The table as findings.py created it before the label, written out here.
OLD_SCHEMA = """
CREATE TABLE findings (
    key TEXT PRIMARY KEY, record TEXT NOT NULL, job TEXT, stage TEXT, harness TEXT, model TEXT,
    unit TEXT, ref TEXT, verdict TEXT, n INTEGER, severity TEXT, "where" TEXT, claim TEXT, reproduce TEXT,
    disposition TEXT NOT NULL DEFAULT 'open', evidence TEXT, disposed_at TEXT, disposed_by TEXT
)
"""
DISPOSED = ("fixed", "a1b2c3d4", "2026-09-20T00:00:00Z", "A Session <noreply@example.invalid>")


class APreLabelStoreWithDispositions(_FindingsFixture):
    world: World

    def setUp(self):
        super().setUp()
        self.staged = self.world.stage(TOOL, "repo/ops/conductor/tools/findings.py", siblings=SIBLINGS)
        self.records = self.staged.parents[3] / ".dev" / "records" / "dispatches"
        self.records.mkdir(parents=True)
        store = self.world.xdg_state / "agentscient" / "findings.db"
        store.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(store)
        con.execute(OLD_SCHEMA)
        con.execute(
            'INSERT INTO findings (key, record, job, n, severity, "where", claim, reproduce,'
            " disposition, evidence, disposed_at, disposed_by) VALUES (?, ?, ?, 1, 'p2', 'ops/x.py',"
            " 'an old finding', 'python3 x', ?, ?, ?, ?)",
            (f"{REC}#1", REC, "adversarial-review", *DISPOSED))
        con.commit()
        con.close()
        findings = [
            {"severity": "p2", "where": "ops/x.py", "claim": "an old finding", "reproduce": "python3 x"},
            {"severity": "p1", "where": "ops/y.py", "claim": "labelled", "reproduce": "python3 y",
             "practical": "yes", "failing": "ops/bin/dispatch.sh review --for A-1"},
        ]
        record = {"id": REC, "job": "adversarial-review", "stage": "review", "unit": "lane/x",
                  "harness": {"name": "claude"}, "model": {"ran": "claude-x"},
                  "result": {"envelope": {"status": "ok", "verdict": "changes", "findings": findings}}}
        self._plant_bytes(self.records / f"{REC}.json", (json.dumps(record) + "\n").encode("utf-8"))

    def exported(self):
        proc = self.invoke(["import"])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        out = self.scratch / "findings.jsonl"
        proc = self.invoke(["export", "--write", str(out)])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return {row["n"]: row for row in map(json.loads, out.read_text(encoding="utf-8").splitlines())}

    def test_the_first_import_keeps_a_disposed_row_and_its_shape(self):
        store = self.world.xdg_state / "agentscient" / "findings.db"
        before = store.read_bytes()
        refused = self.invoke(["export"])
        self.assertEqual(refused.refusal().cls, "store")
        self.assertIn("incompatible", refused.stderr)
        self.assertEqual(store.read_bytes(), before, "a legacy read migrated the store")
        rows = self.exported()
        with self.subTest(id="disposition-survives-migration", origin=ORIGIN):
            old = rows[1]
            self.assertEqual((old["disposition"], old["evidence"], old["disposed_at"], old["disposed_by"]),
                             DISPOSED)
            self.assertEqual(sorted(old), OLD_KEYS)
        with self.subTest(id="label-beside-migrated-row", origin=ORIGIN):
            self.assertEqual((rows[2]["practical"], rows[2]["failing"], rows[2]["disposition"]),
                             ("yes", "ops/bin/dispatch.sh review --for A-1", "open"))


if __name__ == "__main__":
    unittest.main()
