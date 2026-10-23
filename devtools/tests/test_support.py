"""Fixture infrastructure isolates credentials and owns only its direct child."""

import os
import signal
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from threading import Event
from unittest.mock import patch

from support import Client, fixture_environment, retain_native

from devtools.store import State

ECHO = """import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' in request:
        print(json.dumps({'id': request['id'], 'result': request.get('params', {})}), flush=True)
"""


class SupportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def client(self, source=ECHO, **kwargs):
        entry = self.root / "fixture.py"
        entry.write_text(source)
        with patch("support.ENTRY", entry):
            client = Client(self.root / "state", "fixture", **kwargs)
        self.addCleanup(client.close)
        return client

    @staticmethod
    def reap(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            with suppress(OSError):
                stream.close()

    def test_environment_excludes_ambient_credentials_and_isolates_login_paths(self):
        inherited = {"OPENAI_API_KEY": "ambient-openai", "TYPESAFE_API_KEY": "ambient-jev",
                     "AWS_SESSION_TOKEN": "ambient-cloud", "GH_TOKEN": "ambient-github",
                     "HOME": "/private/real-home", "CODEX_HOME": "/private/real-codex",
                     "PYTHONPATH": "/private/user-python", "HTTPS_PROXY": "https://private-proxy",
                     "AGENTSCIENT_REQUIRE_TMUX": "1", "UNRELATED_SECRET": "ambient-custom"}
        with patch.dict(os.environ, inherited):
            before = dict(os.environ)
            env = fixture_environment(self.root, {"FIXTURE_KEY": "explicit-dummy"})
            self.assertEqual(before, dict(os.environ))
        for name in ("OPENAI_API_KEY", "TYPESAFE_API_KEY", "AWS_SESSION_TOKEN", "GH_TOKEN",
                     "PYTHONPATH", "HTTPS_PROXY", "UNRELATED_SECRET"):
            self.assertNotIn(name, env)
        self.assertEqual("1", env["AGENTSCIENT_REQUIRE_TMUX"])
        self.assertEqual("explicit-dummy", env["FIXTURE_KEY"])
        self.assertEqual(os.environ.get("PATH"), env.get("PATH"))
        for name in ("HOME", "CODEX_HOME", "GROK_HOME", "CLAUDE_CONFIG_DIR", "XDG_RUNTIME_DIR"):
            self.assertTrue(Path(env[name]).is_relative_to(self.root))
            self.assertEqual(0, Path(env[name]).stat().st_mode & 0o077)
        self.assertNotEqual(fixture_environment(self.root / "other")["HOME"], env["HOME"])

    def test_real_client_process_receives_only_explicit_fixture_credentials(self):
        source = """import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' in request:
        result = {key: os.environ.get(key) for key in ('HOME', 'OPENAI_API_KEY', 'FIXTURE_KEY')}
        print(json.dumps({'id': request['id'], 'result': result}), flush=True)
"""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ambient-credential-never-forward"}):
            client = self.client(source, env={"FIXTURE_KEY": "deliberate-dummy"})
        self.assertIsNone(client.initialization["OPENAI_API_KEY"])
        self.assertEqual("deliberate-dummy", client.initialization["FIXTURE_KEY"])
        self.assertTrue(Path(client.initialization["HOME"]).is_relative_to(self.root))

    def test_retained_native_evidence_copies_live_wal_and_closes_connections(self):
        source = self.root / "source"
        source.mkdir()
        telemetry = source / "telemetry"
        telemetry.mkdir()
        (telemetry / "fixture.jsonl").write_text('{"observed":true}\n')
        target = self.root / "evidence"
        connections = []
        connect = sqlite3.connect

        def track_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            self.addCleanup(connection.close)
            return connection

        with State(source, read_only=False).connect() as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE measured (value TEXT NOT NULL)")
            writer.execute("PRAGMA user_version=1")
            writer.execute("INSERT INTO measured VALUES ('actual fixture result')")
            writer.commit()
            with patch.dict(os.environ, {"AGENTSCIENT_EVIDENCE_DIR": str(target)}):
                with patch("devtools.store.sqlite3.connect", side_effect=track_connection):
                    retain_native(source, "observed")
            self.assertEqual(2, len(connections))
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

        retained = target / "observed"
        with State(retained).connect() as db:
            self.assertEqual(1, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual("actual fixture result", db.execute("SELECT value FROM measured").fetchone()[0])
            self.assertEqual(["measured"], [row[0] for row in db.execute("SELECT name FROM sqlite_master")])
        self.assertEqual((telemetry / "fixture.jsonl").read_bytes(),
                         (retained / "telemetry/fixture.jsonl").read_bytes())
        self.assertIn("Not authenticated", (retained / "interpretation.txt").read_text())

    def test_large_non_utf8_stderr_does_not_block_protocol_or_cleanup(self):
        client = self.client("import os\nos.write(2, b'noise' * 50000 + b'\\xff' * 10000)\n" + ECHO)
        self.assertEqual({"token": "observed"}, client.call("echo", {"token": "observed"}))
        client.close()
        client.close()
        self.assertIsNotNone(client.process.poll())
        self.assertFalse(client.reader.is_alive())
        self.assertFalse(client.errors.is_alive())

    def test_malformed_initialization_reaps_child_without_echoing_payload(self):
        entry = self.root / "invalid.py"
        entry.write_text("import sys\nsys.stdin.readline()\nprint('private-malformed-payload', flush=True)\n")
        spawned = []
        popen = subprocess.Popen

        def start(*args, **kwargs):
            process = popen(*args, **kwargs)
            spawned.append(process)
            self.addCleanup(self.reap, process)
            return process

        with patch("support.ENTRY", entry), patch("support.subprocess.Popen", side_effect=start):
            with self.assertRaisesRegex(RuntimeError, "unreadable protocol") as caught:
                Client(self.root / "state", "fixture")
        self.assertNotIn("private-malformed-payload", str(caught.exception))
        self.assertEqual(1, len(spawned))
        self.assertIsNotNone(spawned[0].poll())
        self.assertTrue(all(stream.closed for stream in (spawned[0].stdin, spawned[0].stdout, spawned[0].stderr)))

    def test_failed_initialized_notification_also_reaps_the_child(self):
        spawned = []
        popen = subprocess.Popen

        def start(*args, **kwargs):
            process = popen(*args, **kwargs)
            spawned.append(process)
            self.addCleanup(self.reap, process)
            wait = process.wait
            process.wait = lambda timeout: wait(timeout=min(timeout, 0.1))
            return process

        source = """import json, os, sys, time
request = json.loads(sys.stdin.readline())
os.close(0)
print(json.dumps({'id': request['id'], 'result': {}}), flush=True)
time.sleep(60)
"""
        with patch("support.subprocess.Popen", side_effect=start):
            with self.assertRaises(BrokenPipeError):
                self.client(source)
        self.assertIsNotNone(spawned[0].poll())
        self.assertTrue(all(stream.closed for stream in (spawned[0].stdin, spawned[0].stdout, spawned[0].stderr)))

    def test_close_does_not_wait_for_another_holder_of_the_output_pipes(self):
        popen = subprocess.Popen
        readers, writers = zip(*(os.pipe() for _ in range(2)))
        for descriptor in writers:
            self.addCleanup(os.close, descriptor)

        def start(*args, **kwargs):
            process = popen(*args, **{**kwargs, "stdout": writers[0], "stderr": writers[1]})
            process.stdout = os.fdopen(readers[0], "r")
            process.stderr = os.fdopen(readers[1], "r")
            return process

        # Keeping the write descriptors models surviving background work without
        # creating an orphan process or signaling anyone beyond Client's child.
        with patch("support.subprocess.Popen", side_effect=start):
            client = self.client()
        client.close()
        self.assertIsNotNone(client.process.poll())
        self.assertFalse(client.reader.is_alive())
        self.assertFalse(client.errors.is_alive())
        for descriptor in writers:
            os.fstat(descriptor)  # Client did not close another owner's handle.

    @unittest.skipUnless(os.name == "posix", "Fixture kill escalation uses POSIX signals")
    def test_close_reaps_child_that_ignores_eof_and_termination(self):
        client = self.client("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n" +
                             ECHO + "while True: time.sleep(60)\n")
        wait = client.process.wait
        with patch.object(client.process, "wait", side_effect=lambda timeout: wait(timeout=min(timeout, 0.1))):
            client.close()
        self.assertEqual(-signal.SIGKILL, client.process.returncode)
        self.assertFalse(client.reader.is_alive())
        self.assertFalse(client.errors.is_alive())

    def test_concurrent_requests_keep_their_own_ids_when_writes_cross(self):
        client = self.client()
        first_waiting, second_written = Event(), Event()
        write = client.write

        def crossing(packet):
            token = packet.get("params", {}).get("token")
            if token == "first":
                first_waiting.set()
                self.assertTrue(second_written.wait(3), "Second request never crossed the first write")
            write(packet)
            if token == "second":
                second_written.set()

        with patch.object(client, "write", side_effect=crossing), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.call, "echo", {"token": "first"})
            self.assertTrue(first_waiting.wait(3))
            second = pool.submit(client.call, "echo", {"token": "second"})
            self.assertEqual({"token": "first"}, first.result(timeout=5))
            self.assertEqual({"token": "second"}, second.result(timeout=5))


if __name__ == "__main__":
    unittest.main()
