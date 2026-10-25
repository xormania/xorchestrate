#!/usr/bin/env python3
"""Finite conductor execution; inherits instrument-conventions.md.

Owner-issued plans are authority. Native hooks restrict conductor tools;
this controller reserves actions before spawning and validates actual evidence.
The refusal class names what refused (owner, 2026-09-13, ruling 3):
`arguments`, `identity`, `plan`, `json`, `binding`, `state`, `admission`,
`seat`; the wire and the identity check come from instrument.py beside
this file.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
try:
    from instrument import Refusal, emit, identity, refuse
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

HERE = Path(__file__).resolve()
HOOK = HERE.parents[2] / 'devlane/hooks/conductor-control.py'
LIMIT = 8 * 1024 * 1024
KINDS = {'tests', 'fixture', 'red', 'test-review', 'code', 'green', 'review', 'check'}
PLAN_KEYS = {'version', 'repo', 'branch', 'scope', 'scope_sha256', 'expires_at', 'max_seconds', 'actions'}
ACTION_KEYS = {'id', 'kind', 'argv', 'timeout', 'requires', 'tests', 'producer'}


def need(condition, found, expected='valid owner declaration', needed='inspect status and correct the owner plan', kind='state'):
    if not condition:
        refuse(kind, expected, found, needed)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def strict(data):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON key', kind='json')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: refuse('json', 'valid owner declaration', 'nonfinite JSON number', 'inspect status and correct the owner plan'))


def relative(value):
    need(isinstance(value, str) and value and not value.startswith('-'), 'invalid relative path', kind='plan')
    p = Path(value)
    need(not p.is_absolute() and '..' not in p.parts and p.as_posix() == value and value != '.', 'unnormalized relative path', kind='plan')
    return p


def regular(repo, value):
    path = repo / relative(value)
    need(path.is_file() and path.resolve() == path and not path.is_symlink(), 'missing or symlinked bound file', kind='binding')
    return path


def git(repo, *args):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=5, check=False)
    need(r.returncode == 0, 'unavailable Git binding', kind='binding')
    return r.stdout.strip()


def expiry(plan):
    instant = datetime.fromisoformat(plan['expires_at'])
    need(instant.utcoffset() is not None and instant.utcoffset().total_seconds() == 0, 'non-UTC expiry', kind='plan')
    return instant.timestamp()


def validate_plan(plan):
    need(isinstance(plan, dict) and set(plan) == PLAN_KEYS, 'incorrect plan fields', kind='plan')
    need(type(plan['version']) is int and plan['version'] == 1, 'unsupported plan version', kind='plan')
    need(type(plan['max_seconds']) is int and plan['max_seconds'] > 0, 'invalid total time', kind='plan')
    need(isinstance(plan['repo'], str) and Path(plan['repo']).is_absolute(), 'nonabsolute repository', kind='plan')
    repo = Path(plan['repo'])
    need(repo.resolve() == repo and repo.is_dir(), 'missing repository', kind='plan')
    need(git(repo, 'rev-parse', '--show-toplevel') == str(repo), 'wrong Git worktree', kind='binding')
    need(git(repo, 'symbolic-ref', '--short', 'HEAD') == plan['branch'], 'changed branch', kind='binding')
    need(digest(regular(repo, plan['scope']).read_bytes()) == plan['scope_sha256'], 'changed scope bytes', kind='binding')
    expiry(plan)
    need(isinstance(plan['actions'], list) and plan['actions'], 'empty action declaration', kind='plan')
    seen = set()
    for a in plan['actions']:
        need(isinstance(a, dict) and set(a) == ACTION_KEYS, 'incorrect action fields', kind='plan')
        need(isinstance(a['id'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', a['id']) and a['id'] not in seen, 'invalid or duplicate action id', kind='plan')
        need(a['kind'] in KINDS, 'unknown action kind', kind='plan')
        need(isinstance(a['argv'], list) and a['argv'] and all(isinstance(x, str) and x and '\0' not in x for x in a['argv']), 'invalid action argv', kind='plan')
        need(type(a['timeout']) is int and 0 < a['timeout'] <= plan['max_seconds'], 'invalid action timeout', kind='plan')
        need(isinstance(a['requires'], list) and all(isinstance(x, str) for x in a['requires']) and len(set(a['requires'])) == len(a['requires']) and set(a['requires']) <= seen, 'invalid prerequisites', kind='plan')
        need(isinstance(a['tests'], list) and all(isinstance(x, str) for x in a['tests']) and len(set(a['tests'])) == len(a['tests']), 'invalid test selection', kind='plan')
        for name in a['tests']:
            relative(name)
        need(isinstance(a['producer'], str) and a['producer'].strip() and '\n' not in a['producer'], 'missing producer', kind='plan')
        if a['kind'] in {'red', 'green'}:
            need(a['argv'][:2] == ['unittest', 'discover'], 'RED or GREEN is not controller unittest discovery', kind='plan')
        seen.add(a['id'])
    return plan


def atomic(path, value):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('wb') as out:
        out.write(canonical(value) + b'\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load(state, bindings=True):
    need(state.is_dir() and not state.is_symlink(), 'missing control state')
    data = strict((state / 'state.json').read_bytes())
    need(isinstance(data, dict) and isinstance(data.get('actions_state'), dict), 'corrupt control state')
    if bindings:
        raw = (state / 'plan.json').read_bytes()
        need(digest(raw) == data['plan_sha256'] == digest(Path(data['plan_source']).read_bytes()), 'changed plan bytes', kind='binding')
        need(strict(raw) == data['plan'], 'changed retained declaration', kind='binding')
        validate_plan(data['plan'])
        for name, expected in data['pins'].items():
            need(digest(Path(name).read_bytes()) == expected, 'changed controller or hook bytes', kind='binding')
    return data


@contextlib.contextmanager
def locked(state):
    need(state.is_dir() and not state.is_symlink(), 'missing control state')
    with (state / 'lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def initialize(source, state, who):
    raw = source.read_bytes()
    need(len(raw) <= 1024 * 1024, 'oversize plan', kind='plan')
    plan = validate_plan(strict(raw))
    need(not state.exists() and not state.is_symlink(), 'existing control directory')
    need(not state.is_relative_to(Path(plan['repo'])), 'control state inside worker worktree')
    pins = {str(p): digest(p.read_bytes()) for p in (HERE, HOOK)}
    state.mkdir(mode=0o700)
    (state / 'lock').touch(mode=0o600)
    (state / 'plan.json').write_bytes(raw)
    data = dict(plan=plan, plan_sha256=digest(raw), plan_source=str(source), pins=pins,
                actions_state={}, cumulative_time=0.0, initialized_by=who)
    # A missing/crashed helper must remain a native denial, never an empty allow.
    denial = json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': 'tool: expected live control hook; found unavailable helper; needed restore the pinned control installation'}})
    command = shlex.join([sys.executable, '-I', str(HOOK), '--state', str(state)])
    wrapper = '#!/bin/sh\n'
    for path, expected in pins.items():
        measured = '/usr/bin/sha256sum ' + shlex.quote(path)
        wrapper += 'if [ "$(' + measured + ' 2>/dev/null)" != ' + shlex.quote(expected + '  ' + path) + ' ]; then printf "%s\\n" ' + shlex.quote(denial) + '; exit 0; fi\n'
    wrapper += 'result=$(' + command + ')\nrc=$?\nif [ "$rc" -eq 0 ] && [ -n "$result" ]; then printf "%s\\n" "$result"; else printf "%s\\n" ' + shlex.quote(denial) + '; fi\n'
    (state / 'native-hook.sh').write_text(wrapper)
    (state / 'native-hook.sh').chmod(0o700)
    config = {'hooks': {'PreToolUse': [{'matcher': '.*', 'hooks': [{'type': 'command', 'command': str(state / 'native-hook.sh'), 'timeout': 10}]}]}}
    atomic(state / 'native-hooks.json', config)
    for p in (state / 'native-hook.sh', state / 'native-hooks.json'):
        data['pins'][str(p)] = digest(p.read_bytes())
    atomic(state / 'state.json', data)


def manifest(plan, names):
    rows = []
    for name in sorted(names):
        path = regular(Path(plan['repo']), name)
        git(Path(plan['repo']), 'ls-files', '--error-unmatch', '--', name)
        rows.append({'path': name, 'sha256': digest(path.read_bytes())})
    return rows, digest(canonical(rows))


def ancestors(plan, action):
    by_id = {a['id']: a for a in plan['actions']}
    found = {}
    def visit(key):
        if key not in found:
            found[key] = by_id[key]
            for parent in by_id[key]['requires']:
                visit(parent)
    for key in action['requires']:
        visit(key)
    return list(found.values())


def admit(data, action):
    records = data['actions_state']
    need(action['id'] not in records, 'action already reserved', kind='admission')
    need(not any(r['status'] == 'running' for r in records.values()), 'another action still running', kind='admission')
    need(all(records.get(key, {}).get('status') == 'completed' for key in action['requires']), 'unmet prerequisites', kind='admission')
    need('seat' not in data or data['seat']['status'] == 'running', 'conductor seat already stopped', kind='admission')
    chain = ancestors(data['plan'], action)
    needed = {'fixture': ['tests'], 'red': ['tests', 'fixture'],
              'test-review': ['tests', 'fixture', 'red'],
              'code': ['tests', 'fixture', 'red', 'test-review'],
              'green': ['tests', 'fixture', 'red', 'test-review', 'code']}.get(action['kind'], [])
    phases = {}
    if needed:
        need(bool(action['tests']), 'empty TDD test selection', kind='admission')
        for kind in needed:
            choices = [a for a in chain if a['kind'] == kind]
            need(len(choices) == 1, 'missing or ambiguous TDD predecessor', kind='admission')
            prior = choices[0]
            need(set(prior['tests']) == set(action['tests']) and records.get(prior['id'], {}).get('status') == 'completed', 'unaccepted TDD predecessor', kind='admission')
            phases[kind] = prior
        if action['kind'] == 'test-review':
            need(action['producer'] != phases['tests']['producer'], 'test reviewer is test author', kind='admission')
        if action['kind'] in {'code', 'green'}:
            producer = action['producer'] if action['kind'] == 'code' else phases['code']['producer']
            need(len({producer, phases['tests']['producer'], phases['test-review']['producer']}) == 3, 'nonindependent TDD producers', kind='admission')
        if action['kind'] == 'green':
            need(action['argv'] == phases['red']['argv'], 'changed GREEN discovery arguments', kind='admission')
    rows, sealed = ([], None) if action['kind'] == 'tests' else manifest(data['plan'], action['tests'])
    for prior in phases.values():
        need(records[prior['id']].get('tests_sha256') == sealed, 'changed sealed tests', kind='admission')
        for stream in ('stdout', 'stderr'):
            receipt = records[prior['id']]
            need(digest(Path(receipt[stream + '_path']).read_bytes()) == receipt[stream + '_sha256'], 'changed prerequisite evidence', kind='admission')
    return rows, sealed, phases


def process_identity(pid):
    try:
        return {'pid': pid, 'start': Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]}
    except (OSError, IndexError):
        return None


def stop_owned(token):
    if token and process_identity(token['pid']) == token:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(token['pid'], signal.SIGTERM)
        time.sleep(0.1)
        if process_identity(token['pid']) == token:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(token['pid'], signal.SIGKILL)


def supervise(argv, repo, state, key, seconds, publish, host_env=None):
    start = time.monotonic()
    outpath, errpath = state / f'{key}.stdout', state / f'{key}.stderr'
    aborted = None
    child = None
    old = {}
    def interrupted(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM):
        old[sig] = signal.signal(sig, interrupted)
    try:
        with outpath.open('wb') as out, errpath.open('wb') as err, selectors.DefaultSelector() as selector:
            environment = dict(os.environ, CONTROL_STATE=str(state), PYTHONDONTWRITEBYTECODE='1')
            environment.update(host_env or {})
            child = subprocess.Popen(argv, cwd=repo, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            publish(process_identity(child.pid))
            for pipe, target in ((child.stdout, out), (child.stderr, err)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, target)
            size = 0
            try:
                while selector.get_map() or child.poll() is None:
                    if time.monotonic() - start >= seconds:
                        aborted = 'time allowance exhausted'
                        break
                    for event, _ in selector.select(0.03):
                        chunk = os.read(event.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(event.fileobj)
                            continue
                        take = min(len(chunk), LIMIT - size)
                        event.data.write(chunk[:take])
                        event.data.flush()
                        size += take
                        if take < len(chunk):
                            aborted = 'output allowance exhausted'
                            break
                    if aborted:
                        break
                    if not selector.get_map() and child.poll() is None:
                        time.sleep(0.02)
            except KeyboardInterrupt:
                aborted = 'interrupted'
            finally:
                # This group was created by this invocation, not recovered by PID.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=2)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.stdout.close()
                child.stderr.close()
    except KeyboardInterrupt:
        aborted = 'interrupted'
        if child:
            stop_owned(process_identity(child.pid))
            child.wait(timeout=2)
    except OSError:
        aborted = 'could not start declared command'
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    result = dict(status='aborted' if aborted else 'completed', exit_code=child.returncode if child else None,
                  elapsed=time.monotonic() - start, reason=aborted)
    for name, path in (('stdout', outpath), ('stderr', errpath)):
        if not path.exists():
            path.touch()
        result[name + '_path'] = str(path)
        result[name + '_sha256'] = digest(path.read_bytes())
    return result


def run_action(state, key, who):
    with locked(state):
        data = load(state)
        plan = data['plan']
        choices = [a for a in plan['actions'] if a['id'] == key]
        need(len(choices) == 1, 'unknown action id', kind='admission')
        action = choices[0]
        rows, sealed, phases = admit(data, action)
        allowance = min(action['timeout'], plan['max_seconds'] - data['cumulative_time'], expiry(plan) - time.time())
        need(allowance > 0, 'expired or exhausted plan', kind='admission')
        entry = dict(status='running', exit_code=None, caller=who, tests=rows, tests_sha256=sealed,
                     stdout_path=str(state / f'{key}.stdout'), stderr_path=str(state / f'{key}.stderr'),
                     stdout_sha256=digest(b''), stderr_sha256=digest(b''))
        data['actions_state'][key] = entry
        atomic(state / 'state.json', data)
    def publish(token):
        with locked(state):
            current = load(state, False)
            current['actions_state'][key]['process'] = token
            atomic(state / 'state.json', current)
    argv = action['argv']
    if action['kind'] in {'red', 'green'}:
        argv = [sys.executable, '-I', str(HERE), '_unittest', *argv[2:]]
    result = supervise(argv, plan['repo'], state, key, allowance, publish, data.get('worker_environment'))
    rc = 3 if result['status'] == 'aborted' else (0 if result['exit_code'] == 0 else 1)
    try:
        if rc != 3:
            after, after_hash = manifest(plan, action['tests'])
            need(action['kind'] == 'tests' or after_hash == sealed, 'test bytes changed during execution', kind='admission')
            result.update(tests=after, tests_sha256=after_hash)
            if action['kind'] in {'red', 'green'}:
                report = strict(Path(result['stdout_path']).read_bytes())
                valid = report['testsRun'] > 0 and report['errors'] == 0 and report['skipped'] == 0 and report['unexpectedSuccesses'] == 0
                if action['kind'] == 'red':
                    valid = valid and report['failures'] > 0 and result['exit_code'] == 1
                else:
                    valid = valid and report['failures'] == 0 and result['exit_code'] == 0
                rc = 0 if valid else 1
            elif action['kind'] == 'test-review' and rc == 0:
                verdict = strict(Path(result['stdout_path']).read_bytes())
                red = data['actions_state'][phases['red']['id']]
                expected = dict(verdict='approve', tests_sha256=after_hash, red_sha256=red['stdout_sha256'])
                rc = 0 if verdict == expected else 1
    except Refusal as exc:
        rc = 3
        result['reason'] = exc.line()
    except (OSError, ValueError, KeyError, TypeError):
        rc = 1
        result['reason'] = 'invalid execution evidence'
    result['status'] = {0: 'completed', 1: 'failed', 3: 'aborted'}[rc]
    with locked(state):
        current = load(state, False)
        current['actions_state'][key].update(result)
        current['cumulative_time'] += result['elapsed']
        atomic(state / 'state.json', current)
    return rc


def seat(state, argv, who):
    need(bool(argv), 'missing conductor argv', kind='arguments')
    with locked(state):
        data = load(state)
        need('seat' not in data, 'conductor seat already reserved', kind='admission')
        allowance = min(data['plan']['max_seconds'] - data['cumulative_time'], expiry(data['plan']) - time.time())
        need(allowance > 0, 'expired or exhausted plan', kind='admission')
    host = Path(argv[0]).name
    host_env = {}
    worker_environment = {}
    profile_pins = []
    need(host != 'agy', 'agy has no verified native conductor hook', needed='use a guarded conductor; agy may run only as a fixed bounded worker action', kind='seat')
    if host == 'codex':
        # This automation vets the exact hook bytes above before using Codex's
        # documented per-invocation hook trust mechanism. The model cannot
        # invoke seat or another host through this hook.
        overrides = []
        for index, value in enumerate(argv[1:], 1):
            if value in ('-c', '--config') and index + 1 < len(argv):
                overrides.append(argv[index + 1].split('=', 1)[0])
        need('exec' in argv and '--dangerously-bypass-hook-trust' not in argv and not any(key.startswith(('hooks', 'features.hooks')) for key in overrides), 'unsupported Codex seat options', needed='use codex exec with ordinary model and prompt options', kind='seat')
        host_home = state / 'codex-home'
        host_home.mkdir(mode=0o700, exist_ok=True)
        (host_home / 'hooks.json').write_bytes((state / 'native-hooks.json').read_bytes())
        (host_home / 'config.toml').write_text('[features]\nhooks = true\napps = false\n\n[projects.' + json.dumps(data['plan']['repo']) + ']\ntrust_level = "trusted"\n')
        source_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        credential = source_home / 'auth.json'
        if credential.is_file() and not (host_home / 'auth.json').exists():
            (host_home / 'auth.json').symlink_to(credential.resolve())
        host_env['CODEX_HOME'] = str(host_home)
        worker_environment = {'CODEX_HOME': str(source_home)}
        profile_pins = [host_home / 'hooks.json', host_home / 'config.toml']
        # The isolated profile carries no owner config. Disabling its user layer
        # also disables hooks.json in current Codex, so retain this active layer.
        argv = argv[:1] + ['--dangerously-bypass-hook-trust'] + [x for x in argv[1:] if x != '--ignore-user-config']
    elif host == 'claude':
        forbidden = ('--settings', '--setting-sources', '--bare', '--safe-mode')
        need(any(x in argv for x in ('-p', '--print')) and not any(x.split('=', 1)[0] in forbidden for x in argv[1:]) and not any(os.environ.get(x) for x in ('CLAUDE_CODE_SIMPLE', 'CLAUDE_CODE_SAFE_MODE')), 'unsupported Claude seat options', needed='use claude print without hook-disabling settings or environment', kind='seat')
        argv = argv[:1] + ['--setting-sources', '', '--settings', str(state / 'native-hooks.json')] + argv[1:]
    elif host == 'grok':
        need(any(x in argv for x in ('-p', '--single', '--prompt-file', '--prompt-json')), 'unsupported Grok seat options', needed='use a headless Grok prompt', kind='seat')
        host_home = state / 'grok-home'
        (host_home / 'hooks').mkdir(mode=0o700, parents=True, exist_ok=True)
        hook_file = host_home / 'hooks/conductor.json'
        hook_file.write_bytes((state / 'native-hooks.json').read_bytes())
        source_home = Path(os.environ.get('GROK_HOME', str(Path.home() / '.grok')))
        credential = source_home / 'auth.json'
        if credential.is_file() and not (host_home / 'auth.json').exists():
            (host_home / 'auth.json').symlink_to(credential.resolve())
        host_env.update(HOME=str(host_home), GROK_HOME=str(host_home))
        worker_environment.update(HOME=str(Path.home()), GROK_HOME=str(source_home))
        profile_pins = [hook_file]
    with locked(state):
        data = load(state)
        need('seat' not in data, 'conductor seat already reserved', kind='admission')
        data['seat'] = dict(status='running', caller=who)
        if worker_environment:
            # Worker commands must not inherit the conductor-only hook profile.
            data['worker_environment'] = worker_environment
        for path in profile_pins:
            data['pins'][str(path)] = digest(path.read_bytes())
        atomic(state / 'state.json', data)
    result = supervise(argv, data['plan']['repo'], state, 'seat', allowance, lambda _: None, host_env)
    with locked(state):
        current = load(state, False)
        tokens = [r.get('process') for r in current['actions_state'].values() if r['status'] == 'running']
    for token in tokens:
        stop_owned(token)
    with locked(state):
        current = load(state, False)
        current['seat'].update(result)
        atomic(state / 'state.json', current)
    return 3 if result['status'] == 'aborted' else (0 if result['exit_code'] == 0 else 1)


def unittest_runner(args):
    sys.path.insert(0, os.getcwd())
    with contextlib.redirect_stdout(sys.stderr):
        program = unittest.TestProgram(module=None, argv=['unittest', 'discover', *args], exit=False)
    result = program.result
    print(json.dumps(dict(testsRun=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                          skipped=len(result.skipped), unexpectedSuccesses=len(result.unexpectedSuccesses),
                          failed_tests=[{'id': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors])))
    return 0 if result.wasSuccessful() else 1


class Parser(argparse.ArgumentParser):
    def error(self, message):
        refuse('arguments', 'a verb with its arguments', 'invalid command arguments', 'run control.py --help')


def main():
    if sys.argv[1:2] == ['_unittest']:
        return unittest_runner(sys.argv[2:])
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest='verb', required=True)
    for verb in ('init', 'run', 'status', 'seat'):
        p = sub.add_parser(verb)
        if verb in {'init', 'run'}:
            p.add_argument('target')
        p.add_argument('--state', required=True)
        if verb == 'seat':
            p.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    state = Path(args.state).absolute()
    if args.verb == 'status':
        print(json.dumps(load(state, False), sort_keys=True))
        return 0
    who = identity()
    if args.verb == 'init':
        initialize(Path(args.target).resolve(), state, who)
        return 0
    if args.verb == 'run':
        return run_action(state, args.target, who)
    return seat(state, args.argv[1:] if args.argv[:1] == ['--'] else args.argv, who)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Refusal as exc:
        sys.exit(emit(exc))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('state: expected readable valid control state; found unavailable or malformed data; needed inspect owner plan and permission doctor', file=sys.stderr)
        sys.exit(2)
