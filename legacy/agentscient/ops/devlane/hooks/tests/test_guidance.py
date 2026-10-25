"""Exercise installed hook entry points: warnings help, but cannot stop work."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.devlane.hooks import enforcement, native
from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[4]
HOOKS = ROOT / 'ops/devlane/hooks'


class Guidance(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix='guidance-')
        self.addCleanup(self.world.close)
        self.repo = self.world.cwd
        self.world.git('init', '-q', '-b', 'topic')
        self.world.plant_text('ops/lane.conf', "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\nRELEASE_BRANCH='master'\n")

    def check_wire(self, output):
        data = json.loads(output) if output.strip() else {}
        self.assertNotIn('decision', data)
        self.assertNotIn('permissionDecision', data.get('hookSpecificOutput', {}))
        self.assertNotEqual(data.get('continue'), False)
        return data.get('hookSpecificOutput', {}).get('additionalContext') or data.get('systemMessage', '')

    def invoke(self, script, payload):
        result = run([sys.executable, str(HOOKS / script)], cwd=self.repo,
                     env=self.world.env(), stdin=json.dumps(payload), timeout=20)
        self.assertEqual(result.exit, 0, result.stderr)
        return self.check_wire(result.stdout)

    def test_edits_need_neither_grants_nor_a_workflow(self):
        for path in ('src/example.py', '.github/workflows/ci.yml', 'AGENTS.md'):
            with self.subTest(path=path):
                self.invoke('claude/matrix.py', {'tool_name': 'Write', 'cwd': str(self.repo),
                                               'tool_input': {'file_path': path, 'content': 'example'}})
        self.assertFalse((self.world.home / '.local/state/agentscient/handwork.db').exists())

    def test_command_diagnostics_warn_without_permission_decisions(self):
        text = self.invoke('claude/unsafe-command.py', {
            'tool_name': 'Bash', 'cwd': str(self.repo),
            'tool_input': {'command': 'python3 -m pytest | tail -2'}})
        self.assertIn('Diagnostic (advisory)', text)
        self.assertIn('tail', text)

    def test_broken_registry_warns_and_stop_does_not_block(self):
        registry = self.world.plant_text('invalid.json', '{bad json')
        for event in ('PreToolUse', 'Stop'):
            payload = {'hook_event_name': event, 'tool_name': 'Bash', 'cwd': str(self.repo),
                       'tool_input': {'command': 'echo example'}, 'last_assistant_message': 'Done.'}
            result = run([sys.executable, str(HOOKS / 'claude/engine.py')], cwd=self.repo,
                         env=self.world.env({'BOUNDARIES_PATH': str(registry)}),
                         stdin=json.dumps(payload), timeout=20)
            self.assertEqual(result.exit, 0, result.stderr)
            self.assertIn('hook-error', self.check_wire(result.stdout))

    def test_shared_adapters_keep_warning_and_native_permissions(self):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'cwd': str(self.repo),
                   'tool_input': {'command': 'echo example'}}
        with patch.object(enforcement.matrix, 'decide', side_effect=OSError('diagnostic unavailable')):
            result = enforcement.evaluate(payload)
        self.assertEqual(result['outcome'], 'allow')
        self.assertIn('diagnostic unavailable', result['context'])
        for harness in ('claude', 'codex', 'grok'):
            wire = native.respond(harness, {**result, 'advisory': True}, 'PreToolUse')
            self.assertIn('diagnostic unavailable', self.check_wire(json.dumps(wire)))

    def test_missing_profile_warns_instead_of_stranding_a_session(self):
        result = run([sys.executable, str(HOOKS / 'enforcement.py'), '--harness', 'codex',
                      '--event', 'PreToolUse', '--binding', str(self.repo / 'missing.json')],
                     cwd=self.repo, env=self.world.env(), stdin='{}', timeout=20)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn('hook-input', self.check_wire(result.stdout))

    def test_batch_keeps_findings_from_an_earlier_edit(self):
        payload = {'tool_name': 'MultiEdit', 'cwd': str(self.repo),
                   'tool_input': {'file_path': 'example.py', 'edits': [
                       {'old_string': 'before', 'new_string': 'first'},
                       {'old_string': 'first', 'new_string': 'second'}]}}
        binding = {'harness': 'claude', 'cwd': str(self.repo), 'home': str(self.world.home)}
        profile = SimpleNamespace(read_binding=lambda _: binding, validate=lambda _: None,
                                  initialize=lambda *args, **kwargs: None)
        stream = SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
        output = io.StringIO()
        with patch.object(enforcement, 'load', return_value=profile), \
             patch.object(enforcement, 'evaluate', side_effect=[
                 enforcement.decision(context='first edit needs attention'), enforcement.decision()]), \
             patch.object(sys, 'stdin', stream), redirect_stdout(output):
            self.assertEqual(enforcement.main(['--harness', 'claude', '--event', 'PreToolUse',
                                              '--binding', 'fixture.json']), 0)
        self.assertIn('first edit needs attention', self.check_wire(output.getvalue()))

    def test_repeated_stop_warnings_do_not_record_a_missed_skill(self):
        marker = self.repo / 'skill-miss-recorded'
        self.world.plant_text('ops/conductor/tools/skills.py',
                              f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        for index in range(4):
            warning = self.invoke('claude/engine.py', {'hook_event_name': 'Stop', 'cwd': str(self.repo),
                                                      'session_id': 'repeated',
                                                      'last_assistant_message': 'The work is done.'})
            if index < 3:
                self.assertIn('done-gate', warning)
        self.assertFalse(marker.exists())

    def test_delegation_is_available_and_warnings_are_not_skill_misses(self):
        self.invoke('claude/no-subagents.py', {'tool_name': 'Agent', 'tool_input': {'prompt': 'task'}})
        self.invoke('claude/unsafe-command.py', {'tool_name': 'Bash', 'cwd': str(self.repo),
                                               'tool_input': {'command': 'python3 -m pytest | tail -2'}})
        self.assertEqual(list(self.world.home.rglob('skills.db')), [])
