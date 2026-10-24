"""Composition root: run one behavioral contract across interchangeable stores."""
import pytest

from tests.hexagonal.contracts.assessment_inspection import (
    absent_optional_binding_contract,
    actor_projection_contract,
    current_context_race_contract,
    work_projection_contract,
)
from tests.hexagonal.support.assessment_inspection import Fixture


@pytest.fixture(params=["memory", "sqlite"])
def fixture_factory(request, tmp_path):
    fixtures = []

    def create(**options):
        fixture = Fixture(request.param, tmp_path / f"inspection-{len(fixtures)}.sqlite", **options)
        fixtures.append(fixture)
        return fixture

    yield create
    for fixture in fixtures:
        fixture.close()


@pytest.mark.parametrize("effect", ["deny", "review"])
def test_typed_actor_projection_preserves_authorized_and_opaque_values(fixture_factory, effect):
    actor_projection_contract(fixture_factory(), effect)


@pytest.mark.parametrize("work_fields", [None, ["goal"], []])
def test_work_binding_uses_owning_identity_and_preserves_configured_fields(fixture_factory, work_fields):
    work_projection_contract(fixture_factory(work_fields=work_fields))


def test_absent_optional_bindings_remain_absent(fixture_factory):
    absent_optional_binding_contract(fixture_factory())


@pytest.mark.parametrize("target,change", [("inspector", "update"), ("inspector", "delete"), ("work", "update")])
def test_nested_authorization_rechecks_current_context(fixture_factory, target, change):
    current_context_race_contract(fixture_factory(), target, change)


def test_known_bad_projection_omission_fails_the_independent_contract(fixture_factory):
    actor_projection_contract(fixture_factory())
    with pytest.raises(AssertionError, match="^IV01_TYPED_ASSESSMENT_ACTOR_SCOPE(?:\\n|$)"):
        actor_projection_contract(fixture_factory(fault="omit_assessment_projection"))


def test_unrelated_exception_is_not_detection_credit(fixture_factory):
    with pytest.raises(TimeoutError, match="^unrelated inspection failure$"):
        actor_projection_contract(fixture_factory(fault="unrelated_error"))
