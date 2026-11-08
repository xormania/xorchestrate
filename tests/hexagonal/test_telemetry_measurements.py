"""Tiny actual-work checks for representative telemetry measurement populations."""

import pytest

from tests.hexagonal.contracts.telemetry_measurements import (
    completed_measurements_contract,
    invalid_work_contract,
    report_mutation_contract,
)
from tests.hexagonal.support.telemetry_measurements import Measurement


@pytest.fixture
def measurement(tmp_path):
    subject = Measurement(tmp_path / "trial")
    try:
        yield subject
    finally:
        subject.close()


def test_public_measurements_complete_declared_work_and_exact_evidence(measurement):
    completed_measurements_contract(measurement, measurement.run())


@pytest.mark.parametrize("fault", ["empty-query", "corrupt-artifact", "failed-snapshot"])
def test_invalid_public_work_cannot_produce_a_valid_benchmark_success(measurement, fault):
    invalid_work_contract(measurement, fault)


@pytest.mark.parametrize("mutation", ["query", "digest", "population"])
def test_independent_oracle_rejects_false_success_reports(measurement, mutation):
    report_mutation_contract(measurement, measurement.run(), mutation)
