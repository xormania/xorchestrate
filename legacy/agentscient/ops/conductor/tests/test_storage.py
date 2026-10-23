"""Storage invariants, shared by every instrument; kit-owned scratch only.

Contract: instrument-conventions.md, Storage primitives. Owner-authorized
dev-lane consolidation; independent test producer absent.
"""

from __future__ import annotations

import ast
import io
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stdout
from unittest import mock

from test_instrument import TOOLS, load

from ops.testkit import World, run


class Storage(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="storage-")
        self.addCleanup(self.world.close)
        self.m = load()
        self.path = self.world.root / "state ?#%.db"

    def seed(self):
        conn = self.m.connect_store(self.path, mode="rwc", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE value(n INTEGER)")
        conn.execute("INSERT INTO value VALUES (0)")
        return conn

    def test_access_modes_and_escaped_paths(self):
        writer = self.seed()
        self.assertTrue(self.path.is_file(), "SQLite must open the literal path, including URI punctuation")
        for mode in ("ro", "rw", "rwc"):
            with self.subTest(mode=mode), closing(self.m.connect_store(self.path, mode=mode)) as conn:
                self.assertEqual(conn.execute("SELECT n FROM value").fetchone()[0], 0)
                if mode == "ro":
                    for sql in ("UPDATE value SET n=1", "CREATE TABLE extra(n)", "CREATE TEMP TABLE extra(n)"):
                        with self.subTest(sql=sql), self.assertRaises(sqlite3.OperationalError):
                            conn.execute(sql)
                else:
                    conn.execute("UPDATE value SET n=1")
                    conn.rollback()

        # A read-only connection must see uncheckpointed committed WAL rows;
        # suppressing SQLite's read infrastructure with immutable=1 is not safe.
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE value SET n=2")
        with closing(self.m.connect_store(self.path)) as reader:
            self.assertEqual(reader.execute("SELECT n FROM value").fetchone()[0], 2)

    def test_only_create_mode_creates_directories_or_store(self):
        path = self.world.root / "absent" / "state.db"
        for mode in ("ro", "rw"):
            with self.subTest(mode=mode), self.assertRaises(sqlite3.OperationalError):
                self.m.connect_store(path, mode=mode)
            self.assertFalse(path.parent.exists())
        with closing(self.m.connect_store(path, mode="rwc")):
            self.assertTrue(path.is_file())
        with self.assertRaises(ValueError):
            self.m.connect_store(path, mode="invalid")

    def test_commit_and_every_exception_leave_no_partial_transaction(self):
        conn = self.seed()
        with self.m.transaction(conn):
            conn.execute("UPDATE value SET n=1")
        for failure in (ValueError("fault"), KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)), self.m.transaction(conn):
                    conn.execute("UPDATE value SET n=2")
                    raise failure
                self.assertFalse(conn.in_transaction)
                self.assertEqual(conn.execute("SELECT n FROM value").fetchone()[0], 1)

    def test_commit_failure_rolls_back_and_releases_writer(self):
        conn = self.seed()
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE child(id REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
        with self.assertRaises(sqlite3.IntegrityError), self.m.transaction(conn):
            conn.execute("INSERT INTO child VALUES (9)")
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.execute("SELECT count(*) FROM child").fetchone()[0], 0)
        with self.m.transaction(conn):
            conn.execute("INSERT INTO parent VALUES (9)")

    def test_nested_transaction_does_not_commit_or_rollback_its_callers_work(self):
        conn = self.seed()
        with self.m.transaction(conn):
            conn.execute("UPDATE value SET n=1")
            with self.assertRaises(sqlite3.OperationalError), self.m.transaction(conn):
                self.fail("nested transaction must not start")
            self.assertTrue(conn.in_transaction)
        self.assertEqual(conn.execute("SELECT n FROM value").fetchone()[0], 1)

    def test_lock_is_acquired_before_reading_and_released_after_failure(self):
        first = self.seed()
        with closing(self.m.connect_store(self.path, mode="rw", timeout=0, isolation_level=None)) as second:
            with self.m.transaction(first):
                with self.assertRaises(sqlite3.OperationalError), self.m.transaction(second):
                    self.fail("a second writer must not reach its read")
                self.assertTrue(first.in_transaction)
            with self.m.transaction(second):
                second.execute("UPDATE value SET n=n+1")
            self.assertEqual(first.execute("SELECT n FROM value").fetchone()[0], 1)

    def test_read_adapters_share_the_same_permission_boundary(self):
        conf = self.world.plant_text("lane.conf", "LANE_NAME='storage-test'\n")
        # A fresh interpreter keeps file-loaded modules from other suites out
        # of this integration reading; every adapter uses the kit's state root.
        code = """
import sys
from contextlib import closing, redirect_stdout
from unittest import mock
import sqlite3
sys.path.insert(0, sys.argv[1])
import ci, coordination, decisions, handoff, ledger, machine, sessions
writers = [lambda: ci.connect(write=True), lambda: decisions.connect(write=True),
           handoff.writer, ledger.reservation_writer, sessions.writer]
for writer in writers:
    with closing(writer()):
        pass
readers = [lambda: ci.connect(write=False), lambda: decisions.connect(write=False),
           coordination.reader, handoff.reader, ledger.reservation_reader,
           lambda: machine.connect(create=False), sessions.reader,
           sessions.coordination_reader]
for reader in readers:
    with closing(reader()) as conn:
        assert conn.execute('SELECT 1').fetchone()[0] == 1
        try:
            conn.execute('CREATE TABLE forbidden_write(n INTEGER)')
        except sqlite3.OperationalError:
            continue
        raise AssertionError(f'{reader} gave a read caller write authority')
"""
        result = run([sys.executable, "-B", "-c", code, str(TOOLS)],
                     cwd=self.world.cwd, env=self.world.env({"LANE_CONF": str(conf)}), timeout=30)
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)

    def test_storage_mechanisms_have_one_runtime_owner(self):
        copies = []
        for path in TOOLS.glob("*.py"):
            if path.name == "instrument.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                # A synthetic empty register has no file authority.
                if (ast.unparse(node.func) == "sqlite3.connect"
                        and (not node.args or not isinstance(node.args[0], ast.Constant)
                             or node.args[0].value != ":memory:")):
                    copies.append(f"{path.name}:{node.lineno}: private file connection")
                if (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and str(node.args[0].value).upper().startswith("BEGIN")):
                    copies.append(f"{path.name}:{node.lineno}: private transaction")
        self.assertEqual(copies, [], "use the shared storage primitives")


class ObservationStores(unittest.TestCase):
    """PR-01 fault rows share the real domain readers and storage primitive."""

    def test_domain_views_never_initialize_migrate_or_change_stores(self):
        for name in ("hand", "findings", "skills"):
            for state in ("missing", "corrupt", "incompatible", "locked", "unreadable", "populated"):
                with self.subTest(domain=name, state=state), World(prefix="cheap-read-") as world:
                    module = load(TOOLS / f"{name}.py")
                    path = world.root / "domain" / "store.db"
                    path_name = "db_path" if name == "skills" else "database_path"
                    opener = module.con_ if name == "skills" else module.connect
                    with mock.patch.object(module, path_name, return_value=path):
                        if state != "missing":
                            path.parent.mkdir()
                        if state == "corrupt":
                            path.write_bytes(b"not a SQLite database")
                        elif state == "incompatible":
                            with closing(sqlite3.connect(path)) as con:
                                con.execute("CREATE TABLE old_data(value TEXT)")
                                con.execute("INSERT INTO old_data VALUES ('preserve me')")
                                con.commit()
                        elif state == "unreadable":
                            path.mkdir()
                        elif state in ("populated", "locked"):
                            create = sqlite3.connect(path) if name == "hand" else opener(write=True)
                            with closing(create) as con:
                                if name == "hand":
                                    con.execute(module.SCHEMA)
                                    con.execute("INSERT INTO handwork(ts,session,agent,action,why_by_hand,"
                                                "status,ttl_s) VALUES ('2026-01-01T00:00:00Z','s','a','a',"
                                                "'fixture','open',60)")
                                elif name == "findings":
                                    con.execute("INSERT INTO findings(key,record,n) VALUES ('case#1','case',1)")
                                else:
                                    con.execute("INSERT INTO skills(name,kind,status,created_ts,updated_ts) "
                                                "VALUES ('example','project','active','now','now')")
                                con.commit()
                        locker = None
                        if state == "locked":
                            locker = sqlite3.connect(path)
                            locker.execute("BEGIN EXCLUSIVE")
                        views = {
                            "hand": [(module.open_grants, None), (module.report, None)],
                        } if name == "hand" else {
                            "findings": [(module.do_summary, []), (module.do_export, []),
                                         (module.do_export, ["--calibration"]), (module.do_cited, ["case#1"])],
                        } if name == "findings" else {
                            "skills": [(module.list_, []), (module.show, ["example"]), (module.report, [])],
                        }
                        def snapshot(world=world):
                            return {str(p.relative_to(world.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                                    if p.is_file() else None for p in world.root.rglob("*")}
                        before = snapshot()
                        primitive = module.instrument.connect_store
                        def immediate(*args, primitive=primitive, **kwargs):
                            return primitive(*args, **{**kwargs, "timeout": 0})
                        try:
                            with mock.patch.object(module.instrument, "connect_store", side_effect=immediate), \
                                    mock.patch.object(module, "identity", side_effect=AssertionError("read acquired identity"), create=True):
                                for view, args in views[name]:
                                    with self.subTest(view=view.__name__), redirect_stdout(io.StringIO()) as output:
                                        if state == "populated":
                                            view(args)
                                        else:
                                            with self.assertRaises(module.Refusal) as raised:
                                                view(args)
                                            self.assertEqual(raised.exception.kind, "store")
                                            self.assertIn(state, raised.exception.found)
                                            self.assertEqual(output.getvalue(), "")
                                    self.assertEqual(before, snapshot(), "observation changed domain files or schema")
                        finally:
                            if locker is not None:
                                locker.rollback()
                                locker.close()

    def test_cli_missing_store_is_on_the_wire_without_initialization(self):
        for name, verb in (("hand", "open"), ("findings", "summary"), ("skills", "list")):
            with self.subTest(domain=name), World(prefix="missing-cli-") as world:
                conf = world.plant_text("lane.conf", "LANE_NAME='missing-cli'\n")
                result = run([sys.executable, str(TOOLS / f"{name}.py"), verb], cwd=world.cwd,
                             env=world.env({"LANE_CONF": str(conf)}), timeout=10)
                self.assertEqual(result.refusal().cls, "store")
                self.assertIn("missing", result.stderr)
                self.assertFalse((world.xdg_state / "missing-cli").exists())
                self.assertFalse((world.home / ".local/state/missing-cli").exists())

    def test_reader_connections_cannot_write_even_after_writer_initialization(self):
        for name in ("hand", "findings", "skills"):
            with self.subTest(domain=name), World(prefix="cheap-permission-") as world:
                module = load(TOOLS / f"{name}.py")
                opener = module.con_ if name == "skills" else module.connect
                path_name = "db_path" if name == "skills" else "database_path"
                with mock.patch.object(module, path_name, return_value=world.root / "store.db"):
                    if name == "hand":
                        with closing(sqlite3.connect(world.root / "store.db")) as seed:
                            seed.execute(module.SCHEMA)
                            seed.commit()
                    else:
                        with closing(opener(write=True)):
                            pass
                    with closing(opener()) as con:
                        with self.assertRaises(sqlite3.OperationalError):
                            con.execute("CREATE TABLE forbidden(value)")
