import json

import pytest

from experiments.jev_failure_correlation_v2.transport import JevBackend, MODEL, RESERVATION


class Reply:
    status = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps(self.value).encode()[:limit]


class Transport:
    def __init__(self, model=MODEL, probabilities=None):
        self.model, self.probabilities = model, probabilities
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        self.last_request = json.loads(request.data)
        assert request.get_header('Authorization') == 'Bearer fake-credential-for-test'
        return Reply({'model': self.model, 'usage': {'input_tokens': 10, 'output_tokens': 4},
                      'answers': {'literal': {'type': 'choice', 'choice': 'alpha', 'confidence': .9,
                      'probabilities': self.probabilities or {'alpha': .9, 'beta': .1}}}})


def test_exact_model_gates_batch_and_retains_failed_usage(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fake-credential-for-test')
    transport = Transport(model='jev-latest')
    client = JevBackend(tmp_path / 'run', opener=transport)
    assert not client.smoke()['smoke_passed']
    request = {'model': MODEL, 'state': {}, 'questions': {}}
    assert client.assess(request, {'stage': 'P1'})['status'] == 'smoke_required'
    records = [json.loads(x) for x in (tmp_path / 'run/attempts.jsonl').read_text().splitlines()]
    assert len(records) == 1
    assert records[0]['usage']['input_tokens'] == 10
    assert records[0]['status'] == 'invalid_response'
    assert 'fake-credential-for-test' not in (tmp_path / 'run/attempts.jsonl').read_text()
    assert transport.calls == 1


def test_global_reservations_survive_process_instances(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fake-credential-for-test')
    ledger = tmp_path / 'budget.json'
    first = JevBackend(tmp_path / 'first', ledger_path=ledger, max_calls=1, opener=Transport())
    assert first.smoke()['smoke_passed']
    second = JevBackend(tmp_path / 'second', ledger_path=ledger, max_calls=1, opener=Transport())
    assert second.smoke()['status'] == 'budget_exhausted'
    assert json.loads(ledger.read_text())['reserved_usd'] == RESERVATION


def test_invalid_probabilities_cannot_clear(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fake-credential-for-test')
    client = JevBackend(tmp_path, opener=Transport(probabilities={'alpha': 2, 'beta': -1}))
    result = client.smoke()
    assert result['status'] == 'invalid_response'
    assert not result['answers']


def test_reopening_attempt_log_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fake-credential-for-test')
    JevBackend(tmp_path, opener=Transport()).smoke()
    with pytest.raises(ValueError, match='fresh'):
        JevBackend(tmp_path)


def test_credentials_in_packet_refused_before_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fake-credential-for-test')
    client = JevBackend(tmp_path, opener=Transport())
    with pytest.raises(ValueError, match='Credential'):
        client.assess({'model': MODEL, 'state': 'fake-credential-for-test'}, {})
    assert not (tmp_path / 'requests').exists()


def test_option_order_is_both_sent_and_bound(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fake-credential-for-test')
    transport = Transport()
    client = JevBackend(tmp_path, opener=transport)
    client.smoke()
    question = {'type': 'choice', 'instructions': 'Read literal.',
                'criteria': {'alpha': 'Alpha', 'beta': 'Beta'}}
    packet = {'model': MODEL, 'state': {'literal': 'alpha'}, 'questions': {'literal': question}}
    a = client.assess(packet, {'stage': 'ordering'})
    question['criteria'] = {'beta': 'Beta', 'alpha': 'Alpha'}
    b = client.assess(packet, {'stage': 'ordering'})
    assert a['request_id'] != b['request_id']
    assert list(transport.last_request['questions']['literal']['criteria']) == ['beta', 'alpha']
    wire = json.loads((tmp_path / 'requests' / (b['request_id'] + '.body.json')).read_bytes())
    assert list(wire['questions']['literal']['criteria']) == ['beta', 'alpha']
