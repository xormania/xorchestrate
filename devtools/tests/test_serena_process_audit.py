import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools.serena.process_audit import MAX_RECORD_BYTES, PREFIX, ProcessObserver

ROOT = Path(__file__).resolve().parents[2]


class SerenaProcessAuditTests(unittest.TestCase):
    def probe(self, code):
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT,
            capture_output=True, text=True, check=True, timeout=10,
        )

    def records(self, value):
        return [json.loads(line[len(PREFIX):]) for line in value.splitlines()
                if line.startswith(PREFIX.decode())]

    def test_real_launch_and_failed_launch_are_both_attempts_without_environment(self):
        result = self.probe("""
            import subprocess
            import sys
            from devtools.serena.process_audit import install
            assert install() is not None
            output = subprocess.run(
                [sys.executable, '-c', 'print("canary")'],
                env={'OBSERVATION_TEST_SECRET': 'never-capture-this-environment'},
                capture_output=True, text=True, check=True,
            )
            assert output.stdout.strip() == 'canary'
            try:
                subprocess.run(['/definitely-not-an-installed-executable'], check=True)
            except FileNotFoundError:
                pass
            else:
                raise AssertionError('Expected missing executable')
            print('unblocked')
        """)
        records = self.records(result.stderr)
        self.assertEqual("observer_ready", records[0]["kind"])
        attempts = [row for row in records if row["kind"] == "process_attempt"]
        self.assertEqual(2, len(attempts))
        self.assertEqual({"attempt"}, {row["evidence"] for row in attempts})
        self.assertTrue(all(row["event"] == "subprocess.Popen" for row in attempts))
        self.assertEqual("unblocked\n", result.stdout)
        self.assertNotIn("never-capture-this-environment", result.stderr)
        self.assertTrue(all("request_id" not in row for row in attempts))

    def test_subprocess_fast_path_counts_success_and_failure_once_each(self):
        result = self.probe("""
            import subprocess
            import sys
            from devtools.serena.process_audit import install
            assert subprocess._USE_POSIX_SPAWN
            seen = []
            sys.addaudithook(lambda event, values: seen.append(event) if event == 'os.posix_spawn' else None)
            assert install() is not None
            subprocess.run(['/bin/true'], close_fds=False, check=True)
            try:
                subprocess.run(['/definitely-not-an-installed-executable'], close_fds=False, check=True)
            except FileNotFoundError:
                pass
            else:
                raise AssertionError('Expected missing executable')
            assert len(seen) == 2, seen
            print('unblocked')
        """)
        attempts = [row for row in self.records(result.stderr) if row["kind"] == "process_attempt"]
        self.assertEqual(["subprocess.Popen", "subprocess.Popen"], [row["event"] for row in attempts])
        self.assertEqual(["/bin/true", "/definitely-not-an-installed-executable"],
                         [row["executable"] for row in attempts])
        self.assertEqual("unblocked\n", result.stdout)

    def test_direct_posix_spawn_success_and_failure_remain_observed_after_subprocess(self):
        result = self.probe("""
            import os
            import subprocess
            from devtools.serena.process_audit import install
            assert install() is not None
            subprocess.run(['/bin/true'], close_fds=False, check=True)
            pid = os.posix_spawn('/bin/true', ['/bin/true'], {})
            assert os.waitpid(pid, 0)[1] == 0
            try:
                os.posix_spawn('/definitely-not-an-installed-executable', ['missing'], {})
            except FileNotFoundError:
                pass
            else:
                raise AssertionError('Expected missing executable')
        """)
        attempts = [row for row in self.records(result.stderr) if row["kind"] == "process_attempt"]
        self.assertEqual(["subprocess.Popen", "os.posix_spawn", "os.posix_spawn"],
                         [row["event"] for row in attempts])
        self.assertEqual(["/bin/true", "/bin/true", "/definitely-not-an-installed-executable"],
                         [row["executable"] for row in attempts])

    def test_direct_spawn_during_subprocess_audit_dispatch_is_not_suppressed(self):
        result = self.probe("""
            import os
            import subprocess
            import sys
            from devtools.serena.process_audit import install
            def extra_launch(event, values):
                if event == 'subprocess.Popen':
                    pid = os.posix_spawn('/bin/true', ['/bin/true'], {})
                    assert os.waitpid(pid, 0)[1] == 0
            sys.addaudithook(extra_launch)
            assert install() is not None
            subprocess.run(['/bin/true'], close_fds=False, check=True)
        """)
        attempts = [row for row in self.records(result.stderr) if row["kind"] == "process_attempt"]
        self.assertEqual(["os.posix_spawn", "subprocess.Popen"], [row["event"] for row in attempts])

    def test_full_pipe_drops_observations_without_blocking_process_execution(self):
        result = self.probe("""
            import fcntl
            import json
            import os
            import subprocess
            import sys
            from devtools.serena.process_audit import ProcessObserver
            read_fd, original_fd = os.pipe()
            descriptor = os.open(f'/proc/self/fd/{original_fd}', os.O_WRONLY | os.O_NONBLOCK)
            assert not fcntl.fcntl(original_fd, fcntl.F_GETFL) & os.O_NONBLOCK
            while True:
                try:
                    os.write(descriptor, b'x' * 4096)
                except BlockingIOError:
                    break
            observer = ProcessObserver(descriptor)
            sys.addaudithook(observer.audit)
            output = subprocess.run([sys.executable, '-c', 'print(42)'], capture_output=True, check=True)
            assert output.stdout.strip() == b'42'
            assert observer.dropped > 0
            os.set_blocking(read_fd, False)
            while True:
                try:
                    os.read(read_fd, 65536)
                except BlockingIOError:
                    break
            subprocess.run([sys.executable, '-c', 'pass'], check=True)
            print(os.read(read_fd, 65536).decode())
        """)
        attempts = self.records(result.stdout)
        self.assertEqual(1, len(attempts))
        self.assertGreater(attempts[0]["dropped_before"], 0)

    def test_unicode_oversize_records_stay_within_atomic_write_budget(self):
        observer = ProcessObserver(99)
        captured = []

        def write(descriptor, payload):
            captured.append(payload)
            return len(payload)

        with patch("devtools.serena.process_audit.os.write", write):
            observer.audit("subprocess.Popen", ("🚀" * 150, ["🚀" * 150] * 100, "🚀" * 150, {}))
        self.assertEqual(1, len(captured))
        self.assertLessEqual(len(captured[0]), MAX_RECORD_BYTES)
        self.assertTrue(self.records(captured[0].decode())[0]["capture_truncated"])
        self.assertEqual(("[omitted: over limit]", True), observer.text("secret" * 80, 256))
        self.assertEqual(("[omitted: over limit]", True), observer.text(b"secret" * 80, 256))

    def test_available_mcp_context_is_recorded_without_inventing_a_binding(self):
        result = self.probe("""
            import contextvars
            import subprocess
            import sys
            import types
            from devtools.serena.process_audit import install
            context = contextvars.ContextVar('test_request')
            sys.modules['mcp.server.lowlevel.server'] = types.SimpleNamespace(request_ctx=context)
            context.set(types.SimpleNamespace(request_id=27))
            assert install() is not None
            subprocess.run([sys.executable, '-c', 'pass'], check=True)
        """)
        attempts = [row for row in self.records(result.stderr) if row["kind"] == "process_attempt"]
        self.assertEqual(27, attempts[0]["request_id"])
        self.assertFalse({"role", "job", "actor", "work_order"}.intersection(attempts[0]))

    def test_entrypoint_installs_before_invoking_serena_in_same_interpreter(self):
        result = self.probe("""
            import os
            import subprocess
            import sys
            import tempfile
            import types
            from unittest.mock import patch
            from devtools.serena import entry, process_audit
            sys.modules['process_audit'] = process_audit
            def top_level(*, args, prog_name):
                assert prog_name == 'serena'
                assert args[0] == 'start-mcp-server'
                assert args[args.index('--agent-interface') + 1] == 'REPL'
                subprocess.run([sys.executable, '-c', 'print("entry-canary")'], check=True)
            sys.modules['serena.cli'] = types.SimpleNamespace(top_level=top_level)
            with tempfile.TemporaryDirectory() as directory:
                with patch.dict(os.environ, {'HOME': directory, 'SERENA_HOME': directory}):
                    with patch.object(entry.shutil, 'copyfile'):
                        entry.main()
        """)
        self.assertEqual("entry-canary\n", result.stdout)
        self.assertEqual(["observer_ready", "process_attempt"], [row["kind"] for row in self.records(result.stderr)])

    def test_unavailable_observer_does_not_prevent_process_execution(self):
        result = self.probe("""
            import subprocess
            import sys
            from unittest.mock import patch
            from devtools.serena import process_audit
            with patch.object(process_audit.os, 'open', side_effect=OSError('no audit descriptor')):
                assert process_audit.install() is None
            subprocess.run([sys.executable, '-c', 'print("unobserved")'], check=True)
        """)
        self.assertEqual("unobserved\n", result.stdout)
        self.assertEqual([], self.records(result.stderr))

    def test_guest_objects_are_not_formatted_and_observer_failure_does_not_escape(self):
        class Guest:
            def __str__(self):
                raise AssertionError("Do not call guest conversion")

            def __repr__(self):
                raise AssertionError("Do not call guest conversion")

        observer = ProcessObserver(99)
        with patch("devtools.serena.process_audit.os.write", side_effect=RuntimeError("observer broke")):
            observer.audit("subprocess.Popen", (Guest(), [Guest()], None, {}))
        self.assertEqual(1, observer.dropped)

    def test_exec_attempt_survives_interpreter_replacement(self):
        result = self.probe("""
            import os
            import sys
            from devtools.serena.process_audit import install
            assert install() is not None
            os.execv(sys.executable, [sys.executable, '-c', 'print("replaced")'])
        """)
        self.assertEqual("replaced\n", result.stdout)
        attempts = [row for row in self.records(result.stderr) if row["kind"] == "process_attempt"]
        self.assertEqual(["os.exec"], [row["event"] for row in attempts])
        self.assertEqual("attempt", attempts[0]["evidence"])


if __name__ == "__main__":
    unittest.main()
