"""Exercise configured provider policies through application and native boundaries."""

import pytest

from tests.hexagonal.contracts.capability_execution import (
    default_contract,
    query_authority_contract,
    query_contract,
    query_currentness_contract,
    receipt_contract,
    receipt_failure_contract,
    receipt_settlement_contract,
)
from tests.hexagonal.support.capability_execution import Subject, configuration_error, configured_subject


def test_default_journal_ignores_provider_annotations(store):
    default_contract(Subject(store))


@pytest.mark.parametrize("native,key", [(False, None), (False, "one"), (True, "one")])
def test_query_reads_fresh_without_retaining_payloads(store, native, key):
    query_contract(Subject(store, "query"), native, key)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("denied", ["capabilities", "invoke"])
def test_query_repeat_checks_current_discovery_and_invocation_authority(store, native, denied):
    query_authority_contract(Subject(store, "query"), denied, native)


@pytest.mark.parametrize("target", ["authority", "work"])
def test_query_rechecks_authority_and_resource_versions_before_release(store, target):
    query_currentness_contract(Subject(store, "query"), target)


@pytest.mark.parametrize("native", [False, True])
def test_receipt_effect_returns_live_result_but_replays_only_completion(store, native):
    receipt_contract(Subject(store, "receipt"), native)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("outcome", ["unknown", "failed", "completed"])
def test_receipt_failure_preserves_outcome_without_copying_payloads(store, native, outcome):
    receipt_failure_contract(Subject(store, "receipt"), outcome, native)


def test_receipt_local_settlement_failure_and_reconciliation_keep_payloads_ephemeral(store):
    receipt_settlement_contract(Subject(store, "receipt"))


@pytest.mark.parametrize("mode", ["journaled", "receipt"])
def test_effect_modes_still_require_an_idempotency_key(store, mode):
    subject = Subject(store, mode)
    failure = subject.failure(lambda: subject.invoke(key=None))
    assert failure["code"] == "invalid_arguments"
    assert subject.provider.calls == 0


@pytest.mark.parametrize("defaults,policy", [(False, {"mode": "query", "observation": "none"}), (True, None)])
def test_composition_applies_explicit_and_trusted_factory_execution_policies(tmp_path, defaults, policy):
    with configured_subject(tmp_path, defaults=defaults, policy=policy) as runtime:
        result = runtime.application.call("invoke", {"capability": "reports/inspect", "arguments": {"payload": "input"}})
        assert result["result"]["value"] == 1


def test_explicit_execution_configuration_overrides_trusted_factory_defaults(tmp_path):
    with configured_subject(tmp_path, defaults=True, policy={"mode": "journaled", "observation": "full"}) as runtime:
        arguments = {"capability": "reports/inspect", "arguments": {"payload": "input"}, "idempotency_key": "once"}
        first = runtime.application.call("invoke", arguments)
        assert first["invocation"]["result"]["value"] == 1
        assert runtime.application.call("invoke", arguments) == first


@pytest.mark.parametrize("value", [
    [], {"tools": {"mode": "query"}}, {"tools/inspect": {"mode": "read"}},
    {"tools/inspect": {"mode": "query", "observation": "sometimes"}},
    {"tools/inspect": {"mode": "query", "extra": True}},
])
def test_malformed_execution_policy_is_rejected_without_constructing_providers(tmp_path, value):
    assert configuration_error(value, tmp_path) == "configuration_error"
