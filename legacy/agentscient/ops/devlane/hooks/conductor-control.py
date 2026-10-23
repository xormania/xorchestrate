#!/usr/bin/env python3
"""Native PreToolUse adapter for a frozen conductor control plan."""
import argparse
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path

CONTROL = Path(__file__).resolve().parents[2] / 'conductor/tools/control.py'
READ_TOOLS = {'Read', 'Glob', 'Grep', 'read_file', 'list_directory', 'view_image', 'grep', 'list_dir'}
SHELL_TOOLS = {'Bash', 'bash', 'exec_command', 'shell_command', 'run_terminal_command'}
GIT_READS = {'status', 'diff', 'show', 'log', 'rev-parse', 'ls-files', 'ls-tree'}


def answer(allowed, reason='undeclared tool action'):
    output = {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow' if allowed else 'deny'}
    if not allowed:
        output['permissionDecisionReason'] = 'tool: expected safe read or exact declared control action; found ' + reason + '; needed use the owner control plan or inspect permission doctor'
    print(json.dumps({'hookSpecificOutput': output}))
    return 0


def words(command):
    if not isinstance(command, str) or not command or re.search(r'[\n\r;&|<>`$\\]', command):
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def safe_read(argv):
    if not argv:
        return False
    if argv == ['pwd']:
        return True
    if argv[0] == 'cat':
        return len(argv) > 1 and '-' not in argv[1:]
    if argv[0] == 'rg':
        return not any(a.startswith(('--pre', '--hostname-bin')) for a in argv[1:])
    if argv[0] == 'git' and len(argv) > 1 and argv[1] in GIT_READS:
        bad = ('--output', '--ext-diff', '--textconv', '--exec', '--config', '--open-files-in-pager')
        return not any(a.startswith(bad) or a in {'-c', '-O'} for a in argv[2:])
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        return answer(False, 'oversize native payload')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate native field')
            result[key] = value
        return result
    payload = json.loads(raw, object_pairs_hook=pairs)
    if isinstance(payload, dict) and 'toolName' in payload:
        if payload.get('tool_name', payload['toolName']) != payload['toolName'] or payload.get('tool_input', payload.get('toolInput')) != payload.get('toolInput') or payload.get('toolInputTruncated'):
            return answer(False, 'ambiguous or truncated native payload')
        payload['tool_name'] = payload['toolName']
        payload['tool_input'] = payload.get('toolInput')
    if not isinstance(payload, dict) or not isinstance(payload.get('tool_input'), dict) or not isinstance(payload.get('cwd'), str):
        return answer(False, 'malformed native payload')
    tool, inp = payload.get('tool_name'), payload['tool_input']
    if tool in READ_TOOLS:
        return answer(True)
    if tool == 'write_stdin':
        return answer(not inp.get('chars'), 'interactive input')
    if tool not in SHELL_TOOLS:
        return answer(False)
    if inp.get('tty') or inp.get('env') or inp.get('shell') not in (None, '/bin/bash', '/usr/bin/bash'):
        return answer(False, 'interactive or alternate shell')
    argv = words(inp.get('command', inp.get('cmd')))
    if safe_read(argv):
        return answer(True)
    state = Path(args.state).absolute()
    # Status remains available even after expiration or invalid bindings.
    prefix = [sys.executable, str(CONTROL)]
    if argv == prefix + ['status', '--state', str(state)]:
        return answer(True)
    if argv in ([sys.executable, str(CONTROL.parent / 'co_doc.py')], [sys.executable, str(CONTROL.parent / 'co_doc.py'), '--list-probes']):
        return answer(True)
    if len(argv) != 6 or argv[:3] != prefix + ['run'] or argv[4:] != ['--state', str(state)]:
        return answer(False, 'noncanonical action command')
    spec = importlib.util.spec_from_file_location('conductor_control', CONTROL)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    data = control.load(state)
    cwd = Path(inp.get('workdir', payload['cwd'])).resolve()
    if cwd != Path(data['plan']['repo']) or argv[3] not in {a['id'] for a in data['plan']['actions']}:
        return answer(False, 'wrong repository or undeclared action')
    return answer(True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        answer(False, 'unavailable or invalid control binding')
