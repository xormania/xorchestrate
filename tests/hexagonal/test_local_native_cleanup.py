"""Rejected run-owned copies disappear without touching valid local evidence."""

import pytest

from tests.hexagonal.contracts.local_native_cleanup import collection_cleanup_contract
from tests.hexagonal.support.local_native_cleanup import CleanupCollection


@pytest.mark.parametrize("case", ["dataset_id", "collector_id", "project_id", "restored-identity", "healthy"])
def test_local_collection_removes_only_newly_rejected_byte_copies(tmp_path, case):
    subject = CleanupCollection(tmp_path)
    try:
        collection_cleanup_contract(subject, case)
    finally:
        subject.close()
