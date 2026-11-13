"""Shared agent skills, their harness mirrors, their pointers, the rules import and the Serena project hold.

The repository tests judge this checkout. The planted-tree tests prove each check passes a healthy tree
and catches its own faults, and only its own.
"""

from pathlib import Path

import pytest

from tests.hexagonal.contracts import agent_guidance as contract
from tests.hexagonal.support.agent_guidance import PLANTS, RepositoryView, planted_tree

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("behaviour", sorted(contract.BEHAVIOURS))
def test_the_repository_satisfies_the_agent_guidance_behaviour(behaviour):
    contract.require(RepositoryView(ROOT, shared_skills=contract.SHARED_SKILLS,
                                    kept_sections=contract.KEPT_SECTIONS), behaviour)


def test_a_healthy_tree_passes_every_check(tmp_path):
    view = planted_tree(tmp_path)
    for behaviour in sorted(contract.BEHAVIOURS):
        contract.require(view, behaviour)


@pytest.mark.parametrize("fault", sorted(contract.FAULTS))
def test_each_planted_fault_is_caught_by_its_own_check_alone(tmp_path, fault):
    expected, named, *also = contract.FAULTS[fault]
    also = set(also[0]) if also else set()
    view = planted_tree(tmp_path, fault)
    found = {behaviour: contract.check(view, behaviour) for behaviour in sorted(contract.BEHAVIOURS)}
    assert found[expected], f"{fault} must be caught by {expected}"
    assert any(named in finding for finding in found[expected]), \
        f"{expected} must name {named!r} for {fault}: {found[expected]}"
    assert all(found[behaviour] for behaviour in also), f"{fault} also breaks {sorted(also)}: {found}"
    others = {behaviour: findings for behaviour, findings in found.items()
              if behaviour != expected and behaviour not in also and findings}
    assert not others, f"{fault} must be attributed to {expected} only: {others}"


def test_every_declared_fault_has_a_planted_tree():
    assert set(PLANTS) == set(contract.FAULTS)
