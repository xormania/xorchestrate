"""Active application delivery, policy substitution and recovery contracts."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.hexagonal.support.application import application
from tests.hexagonal.support.delivery import Native, dispatch
from xorchestrate.adapters.delivery import DefiniteFailureRetry, DeliverySchedule
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import Conflict, OrchestrationError


@pytest.fixture
def setup(store):
    driver = Native()
    app = application(store, driver=driver)
    app.call("add_actor", {"actor_id": "bob"})
    session = app.call("start_session", {"driver": "agent", "specification": {}, "actor_id": "bob", "idempotency_key": "start"})['session']
    message = app.call("send_message", {"destination": {"actor": "bob"}, "body": {"text": "challenge"}})['message']
    yield app, driver, session, message


def test_routing_queues_durable_attempt_and_submission_is_not_receipt(setup):
    app, driver, session, message = setup
    initial = app.call('get_delivery', {'message_id': message['id']})
    assert initial['deliveries'][0]['status'] == 'queued'
    assert initial['attempts'] == []
    result = dispatch(app, message)
    assert result['attempt']['session_id'] == session['id']
    assert result['attempt']['incarnation'] == session['backend']['incarnation']
    assert result['attempt']['status'] == 'submitted'
    assert app.call('dispatch_deliveries', {'message_id': message['id']}) == {'deliveries': []}
    assert len(driver.calls) == 1
    assert app.call('get_delivery', {'message_id': message['id']})['receipts'] == []
    assert message['routing_snapshot']['semantics'] == 'resolved_at_enqueue'


def test_lost_submission_stays_unknown_across_reconstructed_application(setup):
    app, driver, session, message = setup
    driver.failure = TimeoutError('lost response')
    result = dispatch(app, message)
    assert result['delivery']['status'] == 'unknown', 'DELIVERY_UNKNOWN_OUTCOME_PRESERVED'
    restarted = application(app.store, driver=driver)
    assert restarted.call('dispatch_deliveries', {}) == {'deliveries': []}
    with pytest.raises(OrchestrationError, match='unknown outcomes'):
        restarted.call('retry_delivery', {'delivery_id': result['delivery']['id'], 'expected_version': result['delivery']['version']})
    assert len(driver.calls) == 1
    assert restarted.call('poll_session', {'session_id': session['id'], 'idempotency_key': 'observe'})['output']


def test_explicit_resolution_preserves_uncertainty_and_retry_policy(setup):
    app, driver, session, message = setup
    driver.failure = TimeoutError()
    delivery = dispatch(app, message)['delivery']
    resolved = app.call('resolve_delivery', {'delivery_id': delivery['id'], 'expected_version': delivery['version'],
        'outcome': 'failed', 'reason': 'Operator independently established no effect'})['delivery']
    app.retry_policy = DefiniteFailureRetry({'max_attempts': 1})
    with pytest.raises(OrchestrationError, match='retry policy'):
        app.call('retry_delivery', {'delivery_id': delivery['id'], 'expected_version': resolved['version']})
    app.retry_policy = DefiniteFailureRetry({'max_attempts': 2})
    app.call('retry_delivery', {'delivery_id': delivery['id'], 'expected_version': resolved['version']})
    driver.failure = None
    result = dispatch(app, message)
    assert result['attempt']['sequence'] == 2
    attempts = app.call('get_delivery', {'message_id': message['id']})['attempts']
    assert attempts[0]['original_status'] == 'unknown'
    assert attempts[0]['disposition'] == 'failed'


def test_concurrent_dispatch_cannot_duplicate_native_effect(setup):
    app, driver, session, message = setup
    driver.release = threading.Event()
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(dispatch, app, message)
        assert driver.entered.wait(2)
        assert app.call('dispatch_deliveries', {}) == {'deliveries': []}
        driver.release.set()
        assert pending.result()['attempt']['status'] == 'submitted'
    assert len(driver.calls) == 1


def test_late_submission_after_detach_does_not_reactivate_or_verify(setup):
    app, driver, session, message = setup
    driver.release = threading.Event()
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(dispatch, app, message)
        assert driver.entered.wait(2)
        app.call('stop_session', {'session_id': session['id'], 'idempotency_key': 'stop'})
        driver.release.set()
        result = pending.result()
    assert result['stale'] is True
    assert result['attempt']['status'] == 'pending'
    assert result['attempt']['late_result'] == {'submitted': True}
    assert app.call('list_sessions', {})['sessions'][0]['status'] == 'stopped'


def test_policy_substitution_scheduling_selection_and_authorization(setup):
    app, driver, session, message = setup
    app.delivery_scheduler = DeliverySchedule({'paused': True})
    assert dispatch(app, message)['deferred'] == 'schedule'
    app.delivery_scheduler = DeliverySchedule()
    class NoSession:
        def select(self, message, recipient, sessions):
            return None
    app.session_selection = NoSession()
    assert dispatch(app, message)['deferred'] == 'no_active_session'
    app.session_selection = None
    app.guardrail = RuleGuardrail({'rules': [{'actions': ['deliver_message'], 'effect': 'deny'}]})
    assert dispatch(app, message)['error']['code'] == 'denied'
    assert driver.calls == []


def test_observation_provenance_duplicate_conflict_and_stale_incarnation(setup):
    app, driver, session, message = setup
    context = {'session_id': session['id'], 'actor': 'bob'}
    event = {'native_id': 'conversation', 'incarnation': '1', 'kind': 'application_tool', 'phase': 'requested',
        'call_id': 'native1', 'action': 'record_result', 'arguments': {'result': 'hello'}}
    first = app.observe_native('agent', context, event)
    assert app.observe_native('agent', context, event) == first
    with pytest.raises(Conflict, match='conflicting evidence'):
        app.observe_native('agent', context, {**event, 'arguments': {'result': 'forged'}})
    stale = app.observe_native('agent', context, {**event, 'incarnation': 'old'})
    assert stale['stale'] is True
    assert first['origin'] == 'adapter'
    with pytest.raises(OrchestrationError, match='Unknown action'):
        app.call('observe_native', {'observation': event})


def test_observation_and_control_available_without_resolving_unknown_effect():
    class Uncertain(Native):
        def send(self, session, message):
            raise TimeoutError()
    app = application(driver=Uncertain())
    session = app.call('start_session', {'driver': 'agent', 'specification': {}, 'idempotency_key': 'start'})['session']
    with pytest.raises(OrchestrationError) as failure:
        app.call('send_session', {'session_id': session['id'], 'message': {}, 'idempotency_key': 'uncertain'})
    operation_id = failure.value.details['operation_id']
    app.call('poll_session', {'session_id': session['id'], 'idempotency_key': 'poll'})
    assert app.call('stop_session', {'session_id': session['id'], 'idempotency_key': 'stop'})['session']['status'] == 'stopped'
    assert app.call('get_operation', {'operation_id': operation_id})['operation']['status'] == 'unknown'


def test_explicit_fallback_records_requested_and_resolved_driver():
    app = application(driver=Native())
    with pytest.raises(OrchestrationError, match='Unknown session driver'):
        app.call('start_session', {'driver': 'missing', 'specification': {}, 'idempotency_key': 'no-fallback'})
    app.session_fallbacks = {'missing': 'agent'}
    session = app.call('start_session', {'driver': 'missing', 'specification': {}, 'idempotency_key': 'fallback'})['session']
    assert session['driver'] == 'agent'
    assert session['selection'] == {'requested': 'missing', 'resolved': 'agent', 'fallback_reason': 'requested_driver_unavailable'}


def test_reconnect_preserves_unknown_source_effect_while_enabling_observation():
    class Recoverable(Native):
        def send(self, session, message):
            raise TimeoutError('lost result')
        def reconnect(self, source, specification):
            return self.start(specification)
    app = application(driver=Recoverable())
    first = app.call('start_session', {'driver': 'agent', 'specification': {}, 'idempotency_key': 'first'})['session']
    with pytest.raises(OrchestrationError) as failed:
        app.call('send_session', {'session_id': first['id'], 'message': {}, 'idempotency_key': 'lost'})
    operation = app.call('get_operation', {'operation_id': failed.value.details['operation_id']})['operation']
    replacement = app.call('reconnect_session', {'driver': 'agent', 'specification': {},
        'resume_session_id': first['id'], 'idempotency_key': 'reconnect'})['session']
    assert replacement['unresolved_effects'] == [operation['id']]
    assert app.call('poll_session', {'session_id': replacement['id'], 'idempotency_key': 'inspect'})['output']
    with pytest.raises(Conflict, match='unresolved source effects'):
        app.call('send_session', {'session_id': replacement['id'], 'message': {}, 'idempotency_key': 'new'})
    app.call('resolve_operation', {'operation_id': operation['id'], 'expected_version': operation['version'],
        'outcome': 'completed', 'result': {'accepted': True}, 'reason': 'Observed original submission separately'})
    assert app.call('list_sessions', {})['sessions'][0]['status'] == 'superseded'
    assert app.call('get_operation', {'operation_id': operation['id']})['operation']['external_result_origin'] == 'caller'


def test_latest_session_policy_is_independent_of_store_key_order():
    from xorchestrate.adapters.delivery import LatestSessionSelection
    old = {'id': 'z-old', 'driver': 'native', 'created_at': '2026-10-23T01:00:00+00:00'}
    new = {'id': 'a-new', 'driver': 'native', 'created_at': '2026-10-23T02:00:00+00:00'}
    policy = LatestSessionSelection()
    assert policy.select({}, 'bob', [old, new]) == 'a-new'
    assert policy.select({}, 'bob', [new, old]) == 'a-new'
