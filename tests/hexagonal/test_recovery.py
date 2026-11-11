"""Recovery after a hard restart: one behavioural contract on a shared durable store and a real project server."""

import pytest

from tests.hexagonal.contracts import recovery as contract
from tests.hexagonal.support.recovery import RecoverySubject


@pytest.fixture
def subject(tmp_path):
    value = RecoverySubject(tmp_path)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_recovery_meets_the_contract(subject, check):
    check(subject)
