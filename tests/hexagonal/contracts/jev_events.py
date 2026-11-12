"""Independent expectations for bounded advisory event assessments."""
from __future__ import annotations


def labels_are_not_provider_evidence(value):
    if isinstance(value, dict):
        assert not {"expected", "donor", "origin", "measurement_basis", "measurement"}.intersection(value), "EVENT_ORACLE_WITHHELD"
        for child in value.values():
            labels_are_not_provider_evidence(child)
    elif isinstance(value, list):
        for child in value:
            labels_are_not_provider_evidence(child)


def unchanged_input(before, after):
    assert after == before, "EVENT_DIAGNOSIS_DOES_NOT_MUTATE_EVIDENCE"


def no_provider_work(report, provider):
    return provider["requests"] == [] and report.get("status") != "complete"
