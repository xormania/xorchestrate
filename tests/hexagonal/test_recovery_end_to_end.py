"""Recovery after a container failure, end to end, on both real terminal hosts.

The scenarios are the contract in ``tests.hexagonal.contracts.recovery_e2e``, written against the e2e world port;
``tests.hexagonal.support.e2e_world`` adapts one project on one host to it through the real command line.
"""
import importlib

import pytest

from tests.hexagonal.contracts import recovery_e2e as contract

HOSTS = ("detached-process", "multiplexer")


@pytest.fixture(params=HOSTS)
def world(request):
    support = importlib.import_module("tests.hexagonal.support.e2e_world")
    with support.world(request.param) as value:
        yield value


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_recovery_meets_the_end_to_end_contract(world, check):
    check(world)
