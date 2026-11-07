"""Run the independent inspection boundary against both storage adapters."""
import pytest

from tests.hexagonal.contracts.inspection import (
    detached_result_contract,
    freshness_contract,
    isolated_projection_contract,
)
from tests.hexagonal.support.inspection import Fixture


@pytest.fixture(params=["memory", "sqlite"])
def inspection(request, tmp_path):
    fixture = Fixture(request.param, tmp_path / "inspection.sqlite")
    yield fixture
    fixture.close()


def test_projector_composes_without_application_or_delivery(inspection):
    isolated_projection_contract(inspection)


def test_projected_results_are_detached_from_trusted_inputs(inspection):
    detached_result_contract(inspection)


@pytest.mark.parametrize("change", ["update", "delete"])
def test_nested_record_change_during_authorization_rejects_projection(inspection, change):
    freshness_contract(inspection, change)
