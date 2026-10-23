"""Retired grants retain read-only historical evidence."""
import sqlite3
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "ops/conductor/tools/hand.py"

class HistoricalHandwork(unittest.TestCase):
    def test_old_records_are_readable_and_grants_cannot_be_declared(self):
        with World(prefix="historical-handwork-") as world:
            store = world.home / ".local/state/agentscient/handwork.db"
            store.parent.mkdir(parents=True)
            with sqlite3.connect(store) as conn:
                conn.execute("CREATE TABLE handwork(id INTEGER PRIMARY KEY, ts TEXT, action TEXT, status TEXT, ttl_s INTEGER, lever_class TEXT, missing_lever TEXT, verified INTEGER, late INTEGER)")
                conn.execute("INSERT INTO handwork VALUES(1, '2026-09-01T00:00:00Z', 'old work', 'settled', 60, 'other', '', 1, 0)")
            before = store.read_bytes()
            read = run([sys.executable, str(TOOL), "report"], cwd=ROOT, env=world.env(), timeout=20)
            self.assertEqual(read.exit, 0, read.stderr)
            self.assertIn("settled=1", read.stdout)
            denied = run([sys.executable, str(TOOL), "declare", "work", "why", "other"], cwd=ROOT, env=world.env(), timeout=20)
            self.assertNotEqual(denied.exit, 0)
            self.assertEqual(store.read_bytes(), before)

    def test_absent_history_is_never_created_by_a_read(self):
        with World(prefix="absent-handwork-") as world:
            result = run([sys.executable, str(TOOL), "report"], cwd=ROOT, env=world.env(), timeout=20)
            self.assertNotEqual(result.exit, 0)
            self.assertFalse((world.home / ".local/state/agentscient/handwork.db").exists())
