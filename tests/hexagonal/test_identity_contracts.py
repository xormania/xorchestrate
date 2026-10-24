"""Composition root: apply one identity contract to interchangeable decoders."""
import pytest

from tests.hexagonal.contracts.identity import (
    EXCHANGE_SCENARIOS,
    LIFECYCLE_SCENARIOS,
    assert_identity_scenario,
    assert_native_claim_provenance,
)
from tests.hexagonal.support.identity_evidence import (
    CLAUDE,
    CODEX,
    CUSTOM,
    GROK,
    OTHER_CUSTOM,
    identity_factory,
    receipt_factory,
)

FACTORIES = (
    pytest.param(identity_factory(CODEX, GROK), id="codex"),
    pytest.param(identity_factory(CLAUDE, CODEX), id="claude"),
    pytest.param(identity_factory(GROK, CLAUDE), id="grok"),
    pytest.param(identity_factory(CUSTOM, OTHER_CUSTOM, custom=True), id="custom-peer"),
)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("scenario", EXCHANGE_SCENARIOS, ids=lambda item: item.name)
def test_exchange_identity_contract(factory, scenario):
    assert_identity_scenario(factory, scenario)


@pytest.mark.parametrize("factory", (
    pytest.param(receipt_factory(CODEX, GROK), id="codex"),
    pytest.param(receipt_factory(CLAUDE, CODEX), id="claude"),
    pytest.param(receipt_factory(GROK, CLAUDE), id="grok"),
))
@pytest.mark.parametrize("scenario", EXCHANGE_SCENARIOS, ids=lambda item: item.name)
def test_product_receipt_identity_contract(factory, scenario):
    assert_identity_scenario(factory, scenario)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("operation", ("resume", "reconnect", "attach", "replace"))
@pytest.mark.parametrize("scenario", LIFECYCLE_SCENARIOS, ids=lambda item: item.name)
def test_lifecycle_identity_contract(factory, operation, scenario):
    assert_identity_scenario(factory, scenario, operation)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("value,absent", (("another-actor", False), (None, False), (None, True)),
                         ids=("conflicting-actor", "explicit-null", "genuinely-absent"))
def test_native_identity_normalization_retains_provenance(factory, value, absent):
    assert_native_claim_provenance(factory, value, absent=absent)
