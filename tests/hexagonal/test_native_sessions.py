"""Active native adapters across real process/MCP boundaries; no vendor inference."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.adapters.sessions import CodexSessionDriver
from xorchestrate.model import Capability, Conflict, OrchestrationError

PEER = Path(__file__).parent / 'fixtures' / 'native_progress_peer.py'


def eventually(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Conflict as error:
            # Public reads reauthorize a snapshot; concurrent callback evidence
            # may invalidate it before commit. Only an explicit safe retry is
            # part of this polling schedule, never an uncertain effect/error.
            if not error.details.get("retry_safe"):
                raise
        time.sleep(.01)
    assert predicate(), 'expected native observation did not arrive'


def driver(protocol, **options):
    cls = CodexSessionDriver if protocol == 'codex' else GrokSessionDriver
    return cls({'command': [sys.executable, str(PEER), protocol], 'timeout': 5, 'callback_timeout': 10,
                'turn_timeout': 10, **options})


@pytest.mark.parametrize('protocol', ['codex', 'grok'])
def test_submission_then_callback_busy_delivery_and_unrelated_progress(protocol):
    adapter = driver(protocol)
    held = threading.Event()
    release = threading.Event()
    callbacks = []
    observations = []
    adapter.bind_observer(lambda context, event: observations.append((context, event)))
    def callback(action, args, context, key):
        callbacks.append((action, args, context, key))
        if args.get('hold'):
            held.set()
            assert release.wait(5), 'test must release its native hold'
        return {'received': args}
    adapter.bind_application_tools([Capability('echo', 'echo', {'type': 'object'})], callback)
    try:
        first = adapter.start({'context': {'session_id': 'app-first', 'actor': 'worker'}})
        second = adapter.start({'context': {'session_id': 'app-second', 'actor': 'other'}})
        submitted = adapter.send(first, {'text': json.dumps({'action': 'tool', 'arguments': {'hold': True}})})
        assert submitted['submitted'] is True
        assert held.wait(3), 'callback after submission must progress without poll'
        envelope = {'message_id': 'm', 'recipient': 'worker', 'attempt_id': 'a', 'incarnation': first['incarnation'],
                    'body': {'action': 'tool', 'arguments': {'delivery': 'challenge', 'attempt_id': 'a'}}}
        delivery = adapter.deliver(first, envelope)
        assert delivery['transport'] == ('codex/turn/start.toolOutput' if protocol == 'codex' else 'grok/_x.ai/interject')
        adapter.send(second, {'text': json.dumps({'action': 'tool', 'arguments': {'other': 'progress'}})})
        eventually(lambda: any(c[1].get('delivery') == 'challenge' for c in callbacks))
        eventually(lambda: any(c[1].get('other') == 'progress' for c in callbacks))
        assert not release.is_set()
        assert all(c[2]['native_id'] and c[2]['incarnation'] and c[2]['call_id'] for c in callbacks)
        assert any(e['kind'] == 'native_tool_call' and e['arguments'].get('delivery') == 'challenge' for _, e in observations)
        release.set()
        eventually(lambda: any(e['kind'] == 'native_completion' for _, e in observations))
    finally:
        release.set()
        adapter.close()


def test_grok_requires_execution_observation_and_rejects_conflicting_call_ids():
    adapter = driver('grok', observation_timeout=.15)
    called = []
    adapter.bind_application_tools([Capability('echo', 'echo')], lambda *args: called.append(args) or {})
    try:
        session = adapter.start({})
        adapter.send(session, {'text': json.dumps({'action': 'tool', 'arguments': {'missing': True}, 'observed': False})})
        time.sleep(.3)
        assert called == [], "GROK_MCP_REQUIRES_NATIVE_EXECUTION"
        live = adapter._get(session)
        params = {'sessionId': session['native_id'], 'promptId': 'current', 'update': {
            'sessionUpdate': 'tool_call', 'toolCallId': 'same', 'title': 'xorchestrate__echo',
            'rawInput': {'v': 1}, '_meta': {'x.ai/tool': {'version': 1, 'namespace': 'mcp', 'name': 'xorchestrate__echo'}}}}
        adapter._notification(live, 'session/update', params)
        params['update']['rawInput'] = {'v': 2}
        with pytest.raises(OrchestrationError, match='conflicting'):
            adapter._notification(live, 'session/update', params)
    finally:
        adapter.close()


def test_grok_late_completion_cannot_clear_newer_activity():
    adapter = driver('grok')
    try:
        session = adapter.start({})
        live = adapter._get(session)
        for prompt in ('old', 'new'):
            adapter._notification(live, 'session/update', {'sessionId': session['native_id'], 'promptId': prompt,
                'update': {'sessionUpdate': 'agent_message_chunk'}})
        adapter._notification(live, '_x.ai/session/prompt_complete', {'sessionId': session['native_id'], 'promptId': 'old'})
        assert live.activity == 'active'
        assert live.grok['current'] == 'new'
        adapter._notification(live, '_x.ai/session/prompt_complete', {'sessionId': session['native_id'], 'promptId': 'new'})
        assert live.activity == 'idle'
    finally:
        adapter.close()


@pytest.mark.parametrize('protocol', ['codex', 'grok'])
def test_settings_preserve_requested_resolved_observed_and_require_changed_config_policy(protocol):
    options = {'request_parameters': {'type': 'object', 'properties': {'model': {'type': 'string'}}, 'additionalProperties': False},
               'thread' if protocol == 'codex' else 'session': {'model': 'trusted-model'}, 'resume_supported': True}
    adapter = driver(protocol, **options)
    try:
        source = adapter.start({'parameters': {'model': 'requested-model'}})
        assert source['settings']['requested']['model'] == 'requested-model'
        assert source['settings']['resolved']['model'] == 'trusted-model'
        assert source['settings']['observed']['model'] == 'observed-' + protocol
    finally:
        adapter.close()
    same = driver(protocol, **options)
    changed = driver(protocol, **{**options, 'thread' if protocol == 'codex' else 'session': {'model': 'changed-model'}})
    authorized = driver(protocol, **{**options, 'thread' if protocol == 'codex' else 'session': {'model': 'changed-model'},
                                     'allow_configuration_change_on_resume': True})
    try:
        resumed = same.resume(source, {})
        assert resumed['native_id'] == source['native_id']
        assert resumed['incarnation'] != source['incarnation']
        assert resumed['settings']['requested'].get('model') == 'requested-model', 'NATIVE_REQUESTED_SELECTION_RETAINED'
        with pytest.raises(OrchestrationError) as error:
            changed.resume(source, {})
        assert error.value.code == 'configuration_changed'
        explicit = authorized.resume(source, {})
        assert explicit['configuration_change']['authorized_by'] == 'trusted_configuration'
    finally:
        same.close()
        changed.close()
        authorized.close()


@pytest.mark.parametrize('allowed', [True, False])
def test_grok_native_permissions_separate_from_application_callback(allowed):
    adapter = driver('grok', native_permissions={'allow': ['shell'] if allowed else [], 'default': 'deny'})
    observed = []
    adapter.bind_observer(lambda context, event: observed.append(event))
    try:
        session = adapter.start({})
        adapter.send(session, {'text': json.dumps({'action': 'permission', 'tool': 'shell'})})
        eventually(lambda: any(e['kind'] == 'native_permission' for e in observed))
        permission = next(e for e in observed if e['kind'] == 'native_permission')
        assert permission['allowed'] is allowed
        assert permission['result']['outcome']['optionId'] == ('yes' if allowed else 'no')
    finally:
        adapter.close()


@pytest.mark.parametrize('protocol', ['codex', 'grok'])
def test_native_callback_persists_trusted_evidence_through_active_composition(tmp_path, protocol):
    from xorchestrate.composition import build
    config = {'store': {'adapter': 'sqlite', 'options': {'path': str(tmp_path / 'state.sqlite')}},
              'sessions': {'native': {'adapter': protocol, 'options': {
                  'command': [sys.executable, str(PEER), protocol], 'timeout': 5,
                  'application_tools': True, 'resume_supported': True}}}}
    with build(config) as runtime:
        app = runtime.application
        work = app.call('create_work', {'goal': 'Retain native evidence'})['work']
        session = app.call('start_session', {'driver': 'native', 'specification': {}, 'work_id': work['id'],
                                            'idempotency_key': 'start'})['session']
        submitted = app.call('send_session', {'session_id': session['id'], 'idempotency_key': 'send', 'message': {
            'text': json.dumps({'action': 'tool', 'name': 'record_result', 'arguments': {'result': {'marker': 'retained'}}})}})
        assert submitted['output']['submitted']
        eventually(lambda: any(e['result'] == {'marker': 'retained'} for e in app.call('get_work', {'work_id': work['id']})['evidence']))
        with app.store.transaction() as tx:
            observations = [row.data for row in tx.list('native_observation')]
        assert any(row['observation'].get('native_id') == session['backend']['native_id'] and row['observation'].get('call_id') for row in observations)
        assert all(row.get('session_id') == session['id'] for row in observations)
    with build(config) as runtime:
        retained = runtime.application.call('get_work', {'work_id': work['id']})['evidence']
        assert any(e['result'] == {'marker': 'retained'} for e in retained)


@pytest.mark.parametrize('protocol', ['codex', 'grok'])
def test_reconnect_releases_old_owned_process_and_rejects_old_handle(protocol):
    adapter = driver(protocol, resume_supported=True)
    try:
        source = adapter.start({})
        old_live = adapter._get(source)
        process = old_live.process.process
        resumed = adapter.reconnect(source, {})
        assert resumed['native_id'] == source['native_id']
        assert resumed['incarnation'] != source['incarnation']
        assert process.poll() is not None
        with pytest.raises(OrchestrationError) as stale:
            adapter.send(source, {'text': 'old'})
        assert stale.value.code == 'session_unavailable'
        with pytest.raises(OrchestrationError) as callback:
            adapter._verified_request(old_live, {})
        assert callback.value.code == 'session_unavailable'
    finally:
        adapter.close()


@pytest.mark.parametrize('protocol', ['codex', 'grok'])
def test_private_native_marker_retained_across_exchanges_and_resume(tmp_path, protocol):
    options = {'env': {'PEER_STATE_FILE': str(tmp_path / 'peer-state.json')}, 'resume_supported': True}
    calls = []
    observations = []
    adapter = driver(protocol, **options)
    adapter.bind_application_tools([Capability('echo', 'echo')], lambda action, args, context, key: calls.append((args, context)) or {})
    adapter.bind_observer(lambda context, event: observations.append(event))
    try:
        source = adapter.start({})
        adapter.send(source, {'text': json.dumps({'action': 'remember', 'key': 'private', 'private': 'not-repeated-in-challenges'})})
        eventually(lambda: any(e['kind'] == 'native_completion' for e in observations))
        for nonce in ('first', 'second'):
            adapter.send(source, {'text': json.dumps({'action': 'recall', 'key': 'private', 'nonce': nonce})})
            eventually(lambda: any(args.get('nonce') == nonce for args, _ in calls))
        assert all(args['memory'] == 'not-repeated-in-challenges' for args, _ in calls)
        assert all(context['native_id'] == source['native_id'] for _, context in calls)
    finally:
        adapter.close()
    replacement = driver(protocol, **options)
    replacement.bind_application_tools([Capability('echo', 'echo')], lambda action, args, context, key: calls.append((args, context)) or {})
    try:
        resumed = replacement.resume(source, {})
        replacement.send(resumed, {'text': json.dumps({'action': 'recall', 'key': 'private', 'nonce': 'resumed'})})
        eventually(lambda: any(args.get('nonce') == 'resumed' for args, _ in calls))
        last_args, last_context = next((args, context) for args, context in calls if args.get('nonce') == 'resumed')
        assert last_args['memory'] == 'not-repeated-in-challenges'
        assert last_context['native_id'] == source['native_id']
        assert last_context['incarnation'] != source['incarnation']
    finally:
        replacement.close()


@pytest.mark.parametrize('decision', ['accept', 'decline'])
def test_codex_native_permission_response_is_explicit_and_independent(decision):
    adapter = driver('codex', native_permissions={'item/commandExecution/requestApproval': {'decision': decision}})
    observed = []
    adapter.bind_observer(lambda context, event: observed.append(event))
    try:
        session = adapter.start({})
        adapter.send(session, {'text': json.dumps({'action': 'permission', 'tool': 'shell'})})
        eventually(lambda: any(e['kind'] == 'native_permission' for e in observed))
        permission = next(e for e in observed if e['kind'] == 'native_permission')
        assert permission['decision'] == {'decision': decision}
    finally:
        adapter.close()


def test_codex_late_start_cannot_replace_current_turn_or_reactivate_completed_turn():
    adapter = driver('codex')
    try:
        session = adapter.start({})
        live = adapter._get(session)
        for method, turn in (('turn/started', 'old'), ('turn/completed', 'old'), ('turn/started', 'current'), ('turn/started', 'old')):
            adapter._notification(live, method, {'threadId': session['native_id'], 'turn': {'id': turn}})
        assert set(live.pending) == {'current'}
        adapter._notification(live, 'turn/completed', {'threadId': session['native_id'], 'turn': {'id': 'current'}})
        adapter._notification(live, 'turn/started', {'threadId': session['native_id'], 'turn': {'id': 'current'}})
        assert live.activity == 'idle'
    finally:
        adapter.close()


def test_codex_application_callback_requires_actual_native_call_and_thread_ids():
    adapter = driver('codex')
    called = []
    adapter.bind_application_tools([Capability('echo', 'echo')], lambda *args: called.append(args) or {})
    try:
        session = adapter.start({})
        live = adapter._get(session)
        for missing in ('callId', 'threadId'):
            params = {'tool': 'echo', 'arguments': {}, 'callId': 'native-id', 'threadId': session['native_id']}
            params.pop(missing)
            assert adapter._request(live, 'item/tool/call', params)['success'] is False
        assert called == [], 'CODEX_NATIVE_CALL_IDENTITY_REQUIRED'
    finally:
        adapter.close()


@pytest.mark.parametrize('protocol', ['codex', 'grok'])
def test_operational_limits_do_not_change_material_selection_identity(protocol):
    original = driver(protocol, resume_supported=True, timeout=5)
    try:
        source = original.start({})
    finally:
        original.close()
    replacement = driver(protocol, resume_supported=True, timeout=9, callback_workers=4, max_native_calls=5000)
    try:
        resumed = replacement.resume(source, {})
        assert resumed['configuration_id'] == source['configuration_id']
        assert resumed['configuration_snapshot_id'] != source['configuration_snapshot_id']
    finally:
        replacement.close()


def test_grok_mcp_request_replay_and_identical_input_observations_remain_one_to_one():
    adapter = driver('grok')
    called = []
    adapter.bind_application_tools([Capability('echo', 'echo')], lambda action, args, context, key: called.append(context['call_id']) or {'ok': True})
    try:
        session = adapter.start({})
        live = adapter._get(session)
        def native(ident, args):
            adapter._notification(live, 'session/update', {'sessionId': session['native_id'], 'update': {
                'sessionUpdate': 'tool_call', 'toolCallId': ident, 'title': 'xorchestrate__echo', 'rawInput': args,
                '_meta': {'x.ai/tool': {'version': 1, 'namespace': 'mcp', 'name': 'xorchestrate__echo'}}}})
        native('one', {'n': 1})
        request = {'mcp_connection_id': 'connection', 'mcp_request_id': 1, 'name': 'echo', 'arguments': {'n': 1}}
        assert adapter._gateway(live, 'tools/call', request) == {'ok': True}
        native('two', {'n': 1})
        assert adapter._gateway(live, 'tools/call', request) == {'ok': True}
        assert called == ['one']
        with pytest.raises(OrchestrationError) as conflict:
            adapter._gateway(live, 'tools/call', {**request, 'arguments': {'n': 2}})
        assert conflict.value.code == 'call_id_conflict'
        assert adapter._gateway(live, 'tools/call', {**request, 'mcp_connection_id': 'restarted'}) == {'ok': True}
        assert called == ['one', 'two']
        native('three', {'n': 3})
        native('four', {'n': 3})
        adapter._gateway(live, 'tools/call', {**request, 'mcp_request_id': 3, 'arguments': {'n': 3}})
        adapter._gateway(live, 'tools/call', {**request, 'mcp_request_id': 4, 'arguments': {'n': 3}})
        assert called == ['one', 'two', 'three', 'four']
    finally:
        adapter.close()


def test_unknown_prompt_completion_is_not_idle_and_interjections_are_bounded():
    adapter = driver('grok', max_pending_messages=1)
    try:
        session = adapter.start({})
        live = adapter._get(session)
        adapter._prompt_finished(live, 'lost-prompt', False)
        assert live.activity == 'unknown'
        adapter._native_prompt_started(live, 'busy-prompt')
        live.process.request = lambda *args, **kwargs: {'accepted': True}
        adapter.deliver(session, {'attempt_id': 'unresolved', 'body': 'first'})
        with pytest.raises(OrchestrationError) as limited:
            adapter.deliver(session, {'attempt_id': 'new', 'body': 'second'})
        assert limited.value.code == 'session_capacity'
        assert limited.value.details['outcome'] == 'not_started'
        assert 'unresolved' in live.grok['interjections']
    finally:
        adapter.close()



def test_material_configuration_identity_includes_protocol_contract():
    from xorchestrate.adapters.sessions import ACPSessionDriver
    options = {'command': [sys.executable, str(PEER), 'codex'], 'timeout': 5}
    codex = CodexSessionDriver(options)
    acp = ACPSessionDriver(options)
    operational = CodexSessionDriver({**options, 'timeout': 9})
    try:
        assert codex.configuration_id != acp.configuration_id
        assert codex.configuration_id == operational.configuration_id
    finally:
        codex.close()
        acp.close()
        operational.close()
