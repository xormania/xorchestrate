"""The state engine: declared machines, one place per subject, an append-only marking.

Contract: `ops/conductor/machine-lever.md`. The module
`ops/conductor/tools/machine.py` is the subject: its library is loaded
from the file and exercised on a scratch store; its CLI verbs run as a
subprocess under a scratch XDG_STATE_HOME and a staged lane conf.

Written by hand under grant 220 (owner, 2026-09-13: "hand grant ok'd -
build the engine and the handoff manager"), one producer (Claude Fable
5.1), the second producer absent by the owner's word (AGENTS.md
§Ground rules). The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_machine.py`:
red while `machine.py` is absent (`test_the_module_file_is_present`
fails and every load errors), then read again after the module is
written; both readings are quoted in the commit that carries it.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
MODULE = TOOLS / "machine.py"
AGENT = "Engine Referee <noreply@example.test>"
OTHER = "Second Session <noreply@example.test>"


def load_module(path: Path):
    """The engine from its file, importing the instrument module beside it.

    Another suite in the same process may have loaded a staged copy of
    `instrument` (a scratch repo that is gone by now) under that name;
    the engine's `from instrument import` would take it and read a lane
    conf that no longer exists. The real one is put first for this load
    and whatever was there is put back by the caller's cleanup."""
    sys.modules.pop("instrument", None)
    tool_dir = path.parent
    sys.path.insert(0, str(tool_dir))
    try:
        spec = importlib.util.spec_from_file_location("machine_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(tool_dir))
    return module


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"RED: module is absent: {MODULE}")
        # Cleanups, not tearDown: a setUp that errors skips tearDown and
        # would leave this scratch environment on every later suite in
        # the process (measured 2026-09-13: seven skills, watchdog and
        # corpus tests red behind one engine setUp error).
        modules = {name: sys.modules.get(name) for name in ("instrument", "machine_under_test")}

        def restore():
            for name, module in modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.addCleanup(restore)
        self.world = World(prefix="machine-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.tmp = self.world.root
        self.xdg = self.world.xdg_state
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")
        os.environ["LANE_CONF"] = str(self.conf)
        os.environ.pop("WF_AGENT", None)
        self.store = self.xdg / "testlane" / "state.db"
        self.module = self.world.stage(
            MODULE, siblings=("instrument.py", "laneconf.py")
        )

    def env(self, identity: str | None = None) -> dict[str, str]:
        extra = {"LANE_CONF": str(self.conf)}
        if identity:
            extra["WF_AGENT"] = identity
        return self.world.env(extra)

    def cli(self, *argv: str, identity: str | None = None):
        return run(
            [sys.executable, str(self.module), *argv],
            cwd=self.world.cwd, env=self.env(identity), timeout=60,
        )

    def assert_refusal(self, proc, klass: str) -> None:
        self.assertEqual(proc.exit, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "", "a refusal keeps stdout empty")
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertRegex(lines[0], rf"^{klass}: expected .+; found .+; needed .+$")


class ThePresence(unittest.TestCase):
    def test_the_module_file_is_present(self):
        self.assertTrue(MODULE.is_file(), f"RED: module is absent: {MODULE}")


def a_machine(machine):
    """A two-hold ladder: open -> taken -> done, with a release back."""
    T = machine.Transition
    return machine.Machine(
        name="probe",
        version=1,
        initial="open",
        places=("open", "taken", "done"),
        transitions=(
            T("take", "open", "taken", "any", "none", "taken", "take"),
            T("release", "taken", "open", "holder-or-lapsed", "required", "place", "clear"),
            T("done", "taken", "done", "holder", "required", "place", "keep"),
        ),
        ttl_s=3600,
    )


class TheDeclaration(_Scratch):
    def setUp(self):
        super().setUp()
        self.machine = load_module(self.module)
        self.probe = a_machine(self.machine)

    def test_declare_writes_the_machine_and_its_transitions_as_rows(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
            rows = conn.execute(
                "SELECT name, source, target, actor, evidence, failure, hold "
                "FROM transitions WHERE machine = ? ORDER BY rowid", ("probe",)
            ).fetchall()
        self.assertEqual([tuple(r) for r in rows], [
            ("take", "open", "taken", "any", "none", "taken", "take"),
            ("release", "taken", "open", "holder-or-lapsed", "required", "place", "clear"),
            ("done", "taken", "done", "holder", "required", "place", "keep"),
        ])

    def test_declare_twice_is_one_declaration(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
            self.machine.declare(conn, self.probe)
            count = conn.execute(
                "SELECT count(*) FROM transitions WHERE machine = ?", ("probe",)
            ).fetchone()[0]
            versions = conn.execute(
                "SELECT version FROM machines WHERE name = ?", ("probe",)
            ).fetchall()
        self.assertEqual(count, 3)
        self.assertEqual([r[0] for r in versions], [1])

    def test_a_transition_naming_an_undeclared_place_refuses_declaration(self):
        T = self.machine.Transition
        bad = self.machine.Machine(
            name="bad", version=1, initial="open", places=("open",),
            transitions=(T("go", "open", "elsewhere", "any", "none", "place", "keep"),),
            ttl_s=1,
        )
        with self.machine.connect(create=True) as conn, self.assertRaises(self.machine.Refusal) as caught:
            self.machine.declare(conn, bad)
        self.assertEqual(caught.exception.kind, "declaration")
        self.assertIn("elsewhere", caught.exception.found)

    def test_the_store_is_in_wal_mode_with_a_busy_timeout(self):
        with self.machine.connect(create=True) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        self.assertGreaterEqual(timeout, 1000)

    def test_a_transient_initialization_busy_uses_the_same_store(self):
        faults = [sqlite3.SQLITE_BUSY]
        original = sqlite3.connect

        class BusyOnce(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA journal_mode = WAL" and faults:
                    error = sqlite3.OperationalError("planted initialization busy")
                    error.sqlite_errorcode = faults.pop()
                    raise error
                return super().execute(sql, *args)

        def connect(*args, **kwargs):
            return original(*args, **kwargs, factory=BusyOnce)

        with patch.object(self.machine.sqlite3, "connect", side_effect=connect):
            conn = self.machine.connect(create=True)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.machine.declare(conn, self.probe)
        self.assertEqual(conn.execute("SELECT count(*) FROM machines").fetchone()[0], 1)
        self.assertEqual(list(self.xdg.rglob("state.db")), [self.store])

    def test_failed_initialization_closes_its_connection(self):
        original = sqlite3.connect
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_IOERR):
            with self.subTest(code=code):
                opened = []

                class BrokenInit(sqlite3.Connection):
                    failure_code = code

                    def execute(self, sql, *args):
                        if sql == "PRAGMA journal_mode = WAL":
                            error = sqlite3.OperationalError("planted initialization failure")
                            error.sqlite_errorcode = self.failure_code
                            raise error
                        return super().execute(sql, *args)

                def connect(*args, opened=opened, factory=BrokenInit, **kwargs):
                    conn = original(*args, **kwargs, factory=factory)
                    opened.append(conn)
                    self.addCleanup(conn.close)
                    return conn

                with patch.object(self.machine.sqlite3, "connect", side_effect=connect), \
                        patch("time.monotonic", side_effect=(0, 6)):
                    with self.assertRaises(sqlite3.OperationalError):
                        self.machine.connect(create=True)
                with self.assertRaises(sqlite3.ProgrammingError):
                    opened[0].execute("SELECT 1")

    def test_a_read_connection_never_creates_the_store(self):
        self.assertFalse(self.store.exists())
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.connect(create=False)
        self.assertEqual(caught.exception.kind, "store")
        self.assertFalse(self.store.exists(), "a read created the store")
        with self.machine.connect(create=True) as writer:
            writer.execute("CREATE TABLE read_probe(n INTEGER)")
        with self.machine.connect(create=False) as reader:
            self.assertEqual(reader.execute("SELECT count(*) FROM read_probe").fetchone()[0], 0)
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute("INSERT INTO read_probe VALUES (1)")


class TheSubjects(_Scratch):
    def setUp(self):
        super().setUp()
        self.machine = load_module(self.module)
        self.probe = a_machine(self.machine)
        self.conn = self.machine.connect(create=True)
        self.machine.declare(self.conn, self.probe)

    def tearDown(self):
        self.conn.close()

    def test_create_puts_the_subject_at_the_initial_place_and_records_it(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        row = self.machine.subject(self.conn, self.probe, sid)
        self.assertEqual(row["place"], "open")
        self.assertIsNone(row["holder"])
        marking = self.machine.history(self.conn, self.probe, sid)
        self.assertEqual(len(marking), 1)
        self.assertEqual(marking[0]["transition"], "create")
        self.assertEqual(marking[0]["target"], "open")
        self.assertEqual(marking[0]["agent"], AGENT)

    def test_take_moves_the_subject_and_sets_the_holder(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT, note="lane/x")
        row = self.machine.subject(self.conn, self.probe, sid)
        self.assertEqual(row["place"], "taken")
        self.assertEqual(row["holder"], AGENT)
        self.assertEqual(row["holder_note"], "lane/x")
        self.assertRegex(row["held_since"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertEqual(row["ttl_s"], 3600)

    def test_a_second_take_refuses_the_declared_failure_class_naming_the_holder(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT, note="lane/x")
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "take", OTHER)
        self.assertEqual(caught.exception.kind, "taken")
        self.assertIn("open", caught.exception.expected)
        self.assertIn(AGENT, caught.exception.found)
        self.assertIn("lane/x", caught.exception.found)
        self.assertEqual(self.machine.subject(self.conn, self.probe, sid)["holder"], AGENT)

    def test_done_by_someone_other_than_the_holder_refuses_holder(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "done", OTHER, evidence="abc1234")
        self.assertEqual(caught.exception.kind, "holder")
        self.assertIn(AGENT, caught.exception.expected)
        self.assertIn(OTHER, caught.exception.found)

    def test_done_without_evidence_refuses_evidence(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "done", AGENT)
        self.assertEqual(caught.exception.kind, "evidence")
        self.assertEqual(self.machine.subject(self.conn, self.probe, sid)["place"], "taken")

    def test_done_keeps_the_holder_as_the_record_of_who_did_it(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        self.machine.move(self.conn, self.probe, sid, "done", AGENT, evidence="abc1234")
        row = self.machine.subject(self.conn, self.probe, sid)
        self.assertEqual(row["place"], "done")
        self.assertEqual(row["holder"], AGENT)

    def test_move_many_moves_every_subject_in_one_transaction_or_none(self):
        """T-9 (2026-09-14): a package take moved each thread in its own
        transaction, so two sessions taking one package could split it.
        move_many takes a list, or a callable that chooses the ids under
        the write lock, and either every subject moves at one stamp or
        none does."""
        first = self.machine.create(self.conn, self.probe, AGENT)
        second = self.machine.create(self.conn, self.probe, AGENT)
        third = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, second, "take", OTHER, note="lane/y")
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move_many(self.conn, self.probe, [first, second, third], "take", AGENT, note="lane/x")
        self.assertEqual(caught.exception.kind, "taken")
        for sid in (first, third):
            self.assertEqual(self.machine.subject(self.conn, self.probe, sid)["place"], "open")
            self.assertEqual(len(self.machine.history(self.conn, self.probe, sid)), 1)
        rows = self.machine.move_many(
            self.conn, self.probe,
            lambda conn: [r["id"] for r in conn.execute(
                "SELECT id FROM subjects WHERE place = 'open' ORDER BY id")],
            "take", AGENT, note="lane/x",
        )
        self.assertEqual([r["subject"] for r in rows], [first, third])
        self.assertEqual(len({r["at"] for r in rows}), 1, "one stamp for one transaction")
        for sid in (first, third):
            row = self.machine.subject(self.conn, self.probe, sid)
            self.assertEqual((row["place"], row["holder"], row["holder_note"]), ("taken", AGENT, "lane/x"))
        self.assertEqual(self.machine.subject(self.conn, self.probe, second)["holder"], OTHER)
        self.assertEqual(self.machine.move_many(self.conn, self.probe, [], "take", AGENT), [])

    def test_release_clears_the_holder(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        self.machine.move(self.conn, self.probe, sid, "release", AGENT, evidence="changed lanes")
        row = self.machine.subject(self.conn, self.probe, sid)
        self.assertEqual(row["place"], "open")
        self.assertIsNone(row["holder"])
        self.assertIsNone(row["held_since"])

    def test_release_by_another_before_the_ttl_refuses_holder(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "release", OTHER, evidence="mine now")
        self.assertEqual(caught.exception.kind, "holder")
        self.assertIn("ttl", caught.exception.needed)

    def test_release_by_another_after_the_ttl_lapsed_is_allowed(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        stale = self.machine.stamp(self.machine.now() - timedelta(seconds=3601))
        self.conn.execute("UPDATE subjects SET held_since = ? WHERE id = ?", (stale, sid))
        self.conn.commit()
        self.machine.move(self.conn, self.probe, sid, "release", OTHER, evidence="lapsed")
        self.assertEqual(self.machine.subject(self.conn, self.probe, sid)["place"], "open")

    def test_done_by_the_holder_after_the_ttl_still_refuses_another(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        stale = self.machine.stamp(self.machine.now() - timedelta(seconds=3601))
        self.conn.execute("UPDATE subjects SET held_since = ? WHERE id = ?", (stale, sid))
        self.conn.commit()
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "done", OTHER, evidence="abc1234")
        self.assertEqual(caught.exception.kind, "holder")

    def test_an_unknown_transition_refuses_transition(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "teleport", AGENT)
        self.assertEqual(caught.exception.kind, "transition")
        self.assertIn("take", caught.exception.needed)

    def test_an_unknown_subject_refuses_unknown(self):
        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, 404, "take", AGENT)
        self.assertEqual(caught.exception.kind, "unknown")

    def test_a_gate_that_refuses_leaves_the_place_unchanged(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)

        def gate(row):
            self.machine.refuse("deliverable", "a lock", "none", "add one")

        with self.assertRaises(self.machine.Refusal) as caught:
            self.machine.move(self.conn, self.probe, sid, "take", AGENT, gate=gate)
        self.assertEqual(caught.exception.kind, "deliverable")
        self.assertEqual(self.machine.subject(self.conn, self.probe, sid)["place"], "open")
        self.assertEqual(len(self.machine.history(self.conn, self.probe, sid)), 1)

    def test_the_marking_is_the_chain_of_every_move_oldest_first(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.machine.move(self.conn, self.probe, sid, "take", AGENT)
        self.machine.move(self.conn, self.probe, sid, "release", AGENT, evidence="not yet")
        self.machine.move(self.conn, self.probe, sid, "take", OTHER)
        self.machine.move(self.conn, self.probe, sid, "done", OTHER, evidence="#12")
        chain = [(r["transition"], r["source"], r["target"], r["agent"], r["evidence"])
                 for r in self.machine.history(self.conn, self.probe, sid)]
        self.assertEqual(chain, [
            ("create", None, "open", AGENT, None),
            ("take", "open", "taken", AGENT, None),
            ("release", "taken", "open", AGENT, "not yet"),
            ("take", "open", "taken", OTHER, None),
            ("done", "taken", "done", OTHER, "#12"),
        ])

    def test_the_marking_refuses_update_and_delete(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM marking WHERE subject = ?", (sid,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE marking SET agent = 'x' WHERE subject = ?", (sid,))

    def test_two_connections_racing_for_one_open_subject_yield_one_holder(self):
        sid = self.machine.create(self.conn, self.probe, AGENT)
        self.conn.close()

        def take(agent: str) -> str:
            conn = self.machine.connect(create=False, write=True)
            try:
                for _ in range(20):
                    try:
                        self.machine.move(conn, self.probe, sid, "take", agent)
                        return "won"
                    except self.machine.Refusal as refusal:
                        return refusal.kind
                    except sqlite3.OperationalError:
                        continue
                return "gave up"
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(take, [AGENT, OTHER]))
        self.assertEqual(sorted(outcomes), ["taken", "won"], outcomes)
        self.conn = self.machine.connect(create=False)
        row = self.machine.subject(self.conn, self.probe, sid)
        self.assertIn(row["holder"], (AGENT, OTHER))
        takes = [r for r in self.machine.history(self.conn, self.probe, sid) if r["transition"] == "take"]
        self.assertEqual(len(takes), 1)


class TheCli(_Scratch):
    def setUp(self):
        super().setUp()
        self.machine = load_module(self.module)
        self.probe = a_machine(self.machine)

    def test_an_unknown_verb_refuses_arguments(self):
        self.assert_refusal(self.cli("teleport"), "arguments")

    def test_machines_on_an_absent_store_says_so_and_creates_nothing(self):
        proc = self.cli("machines")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "no state store")
        self.assertFalse(self.store.exists())

    def test_machines_lists_declared_machines_with_versions(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
        proc = self.cli("machines")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "probe v1 initial=open places=open,taken,done")

    def test_show_prints_the_transition_table(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
        proc = self.cli("show", "probe")
        self.assertEqual(proc.exit, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(lines[0], "take: open -> taken; actor any; evidence none; failure taken; hold take")
        self.assertEqual(len(lines), 3)

    def test_show_of_an_undeclared_machine_refuses_machine(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
        self.assert_refusal(self.cli("show", "ghost"), "machine")

    def test_history_prints_the_marking_oldest_first(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
            sid = self.machine.create(conn, self.probe, AGENT)
            self.machine.move(conn, self.probe, sid, "take", AGENT, note="lane/x")
        proc = self.cli("history", "probe", str(sid))
        self.assertEqual(proc.exit, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("create -> open", lines[0])
        self.assertIn(AGENT, lines[0])
        self.assertIn("take open -> taken", lines[1])
        self.assertIn("lane/x", lines[1])

    def test_history_of_an_unknown_subject_refuses_unknown(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
        self.assert_refusal(self.cli("history", "probe", "404"), "unknown")

    def test_history_with_a_non_integer_id_refuses_arguments(self):
        with self.machine.connect(create=True) as conn:
            self.machine.declare(conn, self.probe)
        self.assert_refusal(self.cli("history", "probe", "T-1"), "arguments")


if __name__ == "__main__":
    unittest.main()
