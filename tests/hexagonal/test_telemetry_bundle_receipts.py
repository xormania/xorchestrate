"""Bound public preservation receipts without discarding complete bundle evidence."""

import pytest

from tests.hexagonal.contracts.bundle_receipts import mcp_receipt_contract, public_operation_receipt_contract
from tests.hexagonal.support.bundle_receipts import BundleReceipts


@pytest.fixture
def receipts(tmp_path):
    subject = BundleReceipts(tmp_path)
    try:
        yield subject
    finally:
        subject.close()


@pytest.mark.parametrize("operation", ["telemetry_snapshot", "telemetry_verify", "telemetry_restore"])
def test_public_bundle_receipt_is_bounded_and_references_complete_exact_manifest(receipts, operation):
    public_operation_receipt_contract(receipts, operation)


def test_actual_mcp_bundle_receipt_fits_default_frame_and_retains_complete_manifest(receipts):
    mcp_receipt_contract(receipts)
