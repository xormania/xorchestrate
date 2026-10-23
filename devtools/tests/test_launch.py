import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import require_tmux, until

from devtools import launch, processes, terminals
from devtools.api import API
from devtools.store import Store


class LaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_tmux()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state'
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        launch.initialize(self.state, self.workspace, ['owner:external', 'worker:grok'])
        fixture = Path(__file__).with_name('native_fixture.py')
        fixture.chmod(0o755)
        config = json.loads((self.state / 'team.json').read_text())
        config['members'][1]['binary'] = str(fixture)
        (self.state / 'team.json').write_text(json.dumps(config))
        self.store = Store(self.state)
        owner = API(self.state, 'owner', operator=True)
        order = owner.work.work_order_create(title='Launch recovery', objective='Recover sessions', done_when='Restarted')
        owner.control.bind('owner', 'co', order['work_order']['id'])
        owner.control.bind('worker', 'coder', order['work_order']['id'], reports_to='owner')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        try:
            launch.stop(self.state, 'worker', grace=0)
        finally:
            terminals.command(self.state, 'kill-server', check=False)

    def start(self):
        with patch.dict(os.environ, {'GROK_HOME': str(self.root / 'login'), 'TEST_API_KEY': 'launch-secret-sentinel'}):
            result = launch.start(self.state, 'worker')
        until(lambda: self.store.member('worker')['state'] == 'idle')
        return result

    def test_secret_free_launch_and_prompt_cancellation_allow_restart(self):
        first = self.start()
        for path in self.state.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'launch-secret-sentinel', path.read_bytes(), str(path))
        self.assertFalse((self.state / 'launches').exists())
        owner = API(self.state, 'owner', operator=True)
        until(lambda: self.store.member('worker')['state'] == 'idle'
              and self.store.events(actor='worker', kind='readiness_ready')['events'])
        gates = self.workspace / 'fixture-turns'
        gates.mkdir()
        held = owner.store.send('owner', 'worker', 'Hold the prompt', key='held')
        # Readiness has its own startup turn. Cancel this specific unfinished
        # work prompt, rather than accidentally stopping the readiness exchange
        # and leaving the work queued for the replacement session.
        until(lambda: (gates / ('started-' + str(held['id']))).exists())
        self.assertFalse((gates / ('completed-' + str(held['id']))).exists())
        self.assertEqual('active', self.store.member('worker')['state'])
        began = time.monotonic()
        stopped = owner.call('capability_invoke', {'capability': 'session_stop', 'arguments': {'member': 'worker'}})
        self.assertTrue(stopped['stopped'])
        self.assertLess(time.monotonic() - began, 5)
        self.assertFalse(terminals.status(self.store, 'worker')['exists'])
        self.assertFalse(processes.owned(self.store, 'worker', first['session']['generation']))
        second = self.start()
        self.assertNotEqual(first['session']['generation'], second['session']['generation'])
        self.assertTrue(launch.stop(self.state, 'worker', expected_generation=first['session']['generation'])['superseded'])
        self.assertEqual('running', self.store.member('worker')['desired'])

    def test_hung_supervisor_is_reaped_without_touching_an_unrelated_session(self):
        self.start()
        current = self.store.member('worker')
        owned = processes.owned(self.store, 'worker', current['generation'])
        terminals.command(self.state, 'new-session', '-d', '-s', 'unrelated', '--', 'sleep', '30')
        processes.signal_owned(owned, signal.SIGSTOP)
        result = launch.stop(self.state, 'worker', grace=0.1)
        self.assertTrue(result['forced'])
        self.assertFalse(processes.alive(owned))
        self.assertEqual(0, terminals.command(self.state, 'has-session', '-t', '=unrelated', check=False).returncode)
        self.start()

    def test_crashed_supervisor_leaves_recoverable_native_processes(self):
        self.start()
        current = self.store.member('worker')
        owned = processes.owned(self.store, 'worker', current['generation'])
        native = [row for row in owned if row['pid'] != current['pid']]
        self.assertTrue(native, 'The ready supervisor must own native processes')
        # An idle protocol fixture normally exits on stdin EOF when its parent
        # dies. Suspend only the children and observe that state before crashing
        # the supervisor, so this test deterministically creates orphaned work.
        processes.signal_owned(native, signal.SIGSTOP)

        def suspended(row):
            try:
                state = (Path('/proc') / str(row['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                return False
            return state == 'T' and processes.identity(row['pid']) == row

        until(lambda: all(suspended(row) for row in native))
        os.kill(current['pid'], signal.SIGKILL)
        until(lambda: not terminals.status(self.store, 'worker')['exists'])
        self.assertEqual(native, processes.alive(native))
        launch.stop(self.state, 'worker', grace=0.1)
        self.assertFalse(processes.alive(owned))
        self.start()

    def test_slow_registration_exceeds_old_two_second_limit(self):
        original = terminals.start

        def delayed(store, member, argv, workspace):
            program = 'import os,sys,time; time.sleep(2.2); os.execv(sys.argv[1],sys.argv[1:])'
            return original(store, member, [sys.executable, '-c', program, *argv], workspace)

        with patch('devtools.terminals.start', side_effect=delayed):
            self.start()

    def test_registration_timeout_reaps_launch_and_can_retry(self):
        original = terminals.start

        def never_connect(store, member, argv, workspace):
            return original(store, member, ['sleep', '30'], workspace)

        with patch('devtools.terminals.start', side_effect=never_connect), patch('devtools.launch.STARTUP_TIMEOUT', 0.1):
            with self.assertRaisesRegex(RuntimeError, 'registration failed'):
                launch.start(self.state, 'worker')
        self.assertFalse(terminals.status(self.store, 'worker')['exists'])
        self.start()

    def test_dead_pane_is_reaped_but_live_collision_is_preserved(self):
        terminals.command(self.state, 'new-session', '-d', '-s', 'agent-worker', '--', 'sleep', '30')
        with self.assertRaisesRegex(ValueError, 'live owned terminal'):
            launch.start(self.state, 'worker')
        self.assertTrue(terminals.status(self.store, 'worker')['exists'])
        terminals.command(self.state, 'set-option', '-w', '-t', '=agent-worker:', 'remain-on-exit', 'on')
        pid = terminals.status(self.store, 'worker')['panes'][0]['pid']
        os.kill(pid, signal.SIGTERM)
        until(lambda: terminals.status(self.store, 'worker')['panes'][0]['dead'])
        self.start()


class ProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            processes.boot_id()
        except RuntimeError as error:
            if os.environ.get('AGENTSCIENT_REQUIRE_TMUX') == '1':
                raise
            raise unittest.SkipTest(str(error)) from error

    def test_stale_process_identity_cannot_signal_a_reused_pid(self):
        child = subprocess.Popen(['sleep', '30'], start_new_session=True)
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        record = processes.identity(child.pid)
        stale = {**record, 'start': str(int(record['start']) - 1)}
        processes.signal_owned([stale], signal.SIGKILL)
        self.assertIsNone(child.poll())

    def test_recovery_tracks_children_that_create_separate_sessions(self):
        program = 'import subprocess,time; p=subprocess.Popen(["sleep","30"],start_new_session=True); print(p.pid,flush=True); time.sleep(30)'
        parent = subprocess.Popen([sys.executable, '-c', program], stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(parent.wait)
        self.addCleanup(parent.terminate)
        child_pid = int(parent.stdout.readline())
        child = processes.identity(child_pid)
        self.addCleanup(processes.signal_owned, [child], signal.SIGKILL)
        family = processes.family([processes.identity(parent.pid)])
        self.assertIn(child, family)
        parent.kill()
        parent.wait()
        self.assertIn(child, processes.alive(family))
        processes.signal_owned(family, signal.SIGTERM)
        until(lambda: not processes.alive(family))
