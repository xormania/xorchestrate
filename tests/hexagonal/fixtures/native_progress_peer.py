"""Independent protocol peer: acknowledges, then calls back while work is held.

A deterministic subprocess fixture, never authenticated native evidence.
"""
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from controlled_gates import wait_gate

protocol = sys.argv[1]
write_lock = threading.Lock()
state_lock = threading.Lock()
native_id = 'native-' + uuid.uuid4().hex
callbacks = {}
responses = {}
mcp = None
counter = 0
thread_context = threading.local()
result_lock = threading.Lock()

def retain_result(value):
    if os.environ.get("PEER_RESULTS"):
        with result_lock:
            with open(os.environ["PEER_RESULTS"], "a") as output:
                output.write(json.dumps(value) + "\n")
params_start = {}
memories = {}
state_path = Path(os.environ["PEER_STATE_FILE"]) if os.environ.get("PEER_STATE_FILE") else None


def write(packet):
    with write_lock:
        if protocol != 'codex':
            packet = {'jsonrpc': '2.0', **packet}
        print(json.dumps(packet), flush=True)


def call(name, args, prompt=None, observed=True):
    call_id = getattr(thread_context, "call_id", None) or uuid.uuid4().hex
    if protocol == 'codex':
        event = threading.Event()
        rpc_id = uuid.uuid4().hex
        callbacks[rpc_id] = event
        write({'id': rpc_id, 'method': 'item/tool/call', 'params': {
            'threadId': native_id, 'callId': call_id, 'tool': name, 'arguments': args}})
        assert event.wait(10), 'native callback did not complete'
        result = responses[rpc_id].get('result')
        retain_result({'label': getattr(thread_context, 'label', None), 'call_id': call_id, 'rpc_id': rpc_id, 'result': result})
        return result
    if observed:
        write({'method': 'session/update', 'params': {'sessionId': native_id, 'promptId': prompt,
            'update': {'sessionUpdate': 'tool_call', 'toolCallId': call_id, 'title': 'xorchestrate__' + name,
                       'rawInput': args, '_meta': {'x.ai/tool': {'version': 1, 'namespace': 'mcp', 'name': 'xorchestrate__' + name}}}}})
    result = mcp.request('tools/call', {'name': name, 'arguments': args})
    retain_result({'label': getattr(thread_context, 'label', None), 'call_id': call_id, 'result': result})
    return result


class MCP:
    def __init__(self, spec):
        self.process = subprocess.Popen([spec['command'], *spec['args']], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
        self.lock = threading.Lock()
        self.waiting = {}
        self.answers = {}
        self.seq = 0
        threading.Thread(target=self.read, daemon=True).start()
        self.request('initialize', {'protocolVersion': '2025-06-18', 'clientInfo': {'name': 'peer', 'version': '1'}, 'capabilities': {}})
        self.notify('notifications/initialized', {})

    def read(self):
        for line in self.process.stdout:
            data = json.loads(line)
            ident = data.get('id')
            self.answers[ident] = data
            if ident in self.waiting:
                self.waiting[ident].set()

    def notify(self, method, params):
        with self.lock:
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params}) + '\n')
            self.process.stdin.flush()

    def request(self, method, params):
        with self.lock:
            self.seq += 1
            ident = getattr(thread_context, "mcp_request_id", None) or self.seq
            event = self.waiting[ident] = threading.Event()
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params}) + '\n')
            self.process.stdin.flush()
        assert event.wait(10), 'MCP callback deadline'
        return self.answers[ident]


def app_call(name, arguments, prompt):
    response = call(name, arguments, prompt)
    if protocol == 'codex':
        assert response.get('success'), response
        return json.loads(response['contentItems'][0]['text'])
    assert 'error' not in response, response
    value = response['result']
    assert not value.get('isError'), value
    return value.get('structuredContent', json.loads(value['content'][0]['text']))


def verification(instruction, prompt):
    kind = instruction['kind']
    if kind == 'startup':
        memories['verification'] = instruction['memory']
        if state_path:
            state_path.write_text(json.dumps({'native_id': native_id, 'memories': memories}))
        app_call('record_result', {'result': {'verification': {'run_id': instruction['run_id'],
                 'phase': 'ready', 'memory': memories['verification']}}}, prompt)
    elif kind in ('send', 'send_many'):
        messages = instruction['messages'] if kind == 'send_many' else [instruction]
        for item in messages:
            app_call('send_message', {'destination': {'actor': item['target']},
                     'body': {'verification': item['payload']}}, prompt)
    elif kind == 'hold':
        app_call('invoke', {'capability': 'verification/hold', 'arguments': {'case_id': instruction['case_id']}}, prompt)
    elif kind == 'lifecycle_probe':
        app_call('record_result', {'result': {'verification': {'run_id': instruction['run_id'],
                 'phase': 'lifecycle', 'case_id': instruction['case_id'], 'memory': memories.get('verification')}}}, prompt)
    elif kind == 'work':
        rows = app_call('invoke', {'capability': 'verification/work_next', 'arguments': {}}, prompt)['invocation']['result']
        # Independent fixture computation: sort attempts newest first, take each
        # job once, then count only the latest failed attempt.
        seen, failures, failed_tests = set(), [], 0
        for row in sorted(rows['rows'], key=lambda item: item['attempt'], reverse=True):
            if row['job'] in seen:
                continue
            seen.add(row['job'])
            if row['status'] == 'FAIL':
                failures.append(row['job'])
                failed_tests += row['failed_tests']
        app_call('invoke', {'capability': 'verification/work_submit', 'arguments': {'batch': rows['batch'],
                 'failed_jobs': sorted(failures), 'failed_tests': failed_tests}}, prompt)


def verification_delivery(message, prompt):
    payload = message['body']['verification']
    if payload['kind'] == 'challenge':
        app_call('send_message', {'destination': {'actor': message['sender']}, 'body': {'verification': {
            **payload, 'kind': 'reply', 'memory': memories.get('verification')}}}, prompt)
    else:
        app_call('record_result', {'result': {'verification': {
            **{key: payload[key] for key in ('run_id', 'case_id', 'nonce', 'memory')},
            'phase': 'received', 'peer': message['sender']}}}, prompt)


def execute(packet):
    global native_id, mcp, params_start, memories
    method, params = packet['method'], packet.get('params', {})
    ident = packet.get('id')
    if method == 'initialize':
        write({'id': ident, 'result': {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True}}})
    elif method in ('thread/start', 'thread/resume', 'session/new', 'session/load'):
        params_start = params
        native_id = params.get('threadId', params.get('sessionId', native_id))
        if method in ('thread/resume', 'session/load') and state_path and state_path.exists():
            retained = json.loads(state_path.read_text())
            assert retained['native_id'] == native_id
            memories = retained['memories']
        if protocol == 'grok' and params.get('mcpServers'):
            mcp = MCP(params['mcpServers'][-1])
        result = {'thread': {'id': native_id}, 'model': 'observed-codex'} if protocol == 'codex' else {'sessionId': native_id, 'model': 'observed-grok'}
        write({'id': ident, 'result': result})
    elif method in ('turn/start', 'session/prompt', '_x.ai/interject'):
        if method == 'turn/start':
            text = params.get('toolOutput', {}).get('output') if params.get('toolOutput') else params['input'][0]['text']
        elif method == '_x.ai/interject':
            text = params['text']
        else:
            text = params['prompt'][0]['text']
        instruction = None
        if isinstance(text, str) and 'VERIFICATION_COMMAND=' in text:
            instruction = json.loads(text.split('VERIFICATION_COMMAND=', 1)[1].splitlines()[0])
        try:
            message = json.loads(text)
        except (ValueError, TypeError):
            message = {'text': text}
        body = message.get('body', message)
        replay = body.get('replay_fixture', {}) if isinstance(body, dict) else {}
        thread_context.call_id = replay.get('call_id')
        thread_context.mcp_request_id = replay.get('mcp_request_id')
        thread_context.label = replay.get('label')
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {'text': body}
        prompt = params.get('_meta', {}).get('promptId') or params.get('interjectionId') or uuid.uuid4().hex
        if method in ('turn/start', '_x.ai/interject'):
            write({'id': ident, 'result': {'accepted': True, 'turn': {'id': prompt}, 'wire': params}})
        wait_gate(body, 'start')
        if protocol == 'codex':
            write({'method': 'turn/started', 'params': {'threadId': native_id, 'turn': {'id': prompt}}})
        else:
            write({'method': 'session/update', 'params': {'sessionId': native_id, 'promptId': prompt, 'update': {
                'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'working'}}}})
        if instruction is not None:
            verification(instruction, prompt)
        elif 'verification' in body:
            verification_delivery(message, prompt)
        elif body.get('action') == 'remember':
            memories[body['key']] = body['private']
            if state_path:
                state_path.write_text(json.dumps({'native_id': native_id, 'memories': memories}))
        elif body.get('action') == 'recall':
            call('echo', {'memory': memories.get(body['key']), 'nonce': body['nonce']}, prompt)
        elif body.get('action') == 'permission':
            call_id = uuid.uuid4().hex
            event = threading.Event()
            callbacks[call_id] = event
            write({'id': call_id, 'method': 'session/request_permission' if protocol == 'grok' else 'item/commandExecution/requestApproval', 'params': {
                'threadId' if protocol == 'codex' else 'sessionId': native_id,
                'toolCall': {'title': body['tool'], 'toolCallId': call_id}, 'options': [
                    {'kind': 'allow_once', 'optionId': 'yes'}, {'kind': 'reject_once', 'optionId': 'no'}]}})
            assert event.wait(10)
        elif body.get('action') == 'tool':
            call(body.get('name', 'echo'), body.get('arguments', {}), prompt, body.get('observed', True))
        # A callback can finish before native reasoning/turn completion.
        wait_gate(body, 'tail')
        if protocol == 'codex':
            write({'method': 'turn/completed', 'params': {'threadId': native_id, 'turn': {'id': prompt, 'status': 'completed'}}})
        else:
            write({'method': '_x.ai/session/prompt_complete', 'params': {'sessionId': native_id, 'promptId': prompt, 'stopReason': 'end_turn'}})
            if method == 'session/prompt':
                write({'id': ident, 'result': {'stopReason': 'end_turn'}})
    elif method == 'authenticate':
        write({'id': ident, 'result': {}})
    elif ident is not None:
        write({'id': ident, 'error': {'code': -32601, 'message': 'Unsupported method'}})


for line in sys.stdin:
    packet = json.loads(line)
    if 'method' in packet:
        threading.Thread(target=execute, args=(packet,), daemon=True).start()
    elif packet.get('id') in callbacks:
        responses[packet['id']] = packet
        callbacks[packet['id']].set()
if mcp:
    mcp.process.terminate()
    mcp.process.wait(timeout=2)
