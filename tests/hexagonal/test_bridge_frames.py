"""Uncertain writes invalidate real bridge peers; late responses do not."""

import pytest

from tests.hexagonal.contracts import bridge_frames as contract
from tests.hexagonal.support.bridge_frames import bridge_frame_subject


@pytest.mark.parametrize("failure", ["error", "timeout", "zero"])
@pytest.mark.parametrize("already_pending", [False, True], ids=["single", "pending"])
def test_uncertain_write_closes_stream(failure, already_pending):
    with bridge_frame_subject() as subject:
        contract.uncertain_write_closes_stream(subject, failure, already_pending=already_pending)


def test_complete_short_writes_preserve_stream():
    with bridge_frame_subject() as subject:
        contract.complete_short_writes_preserve_stream(subject)


def test_rejected_frame_preserves_stream():
    with bridge_frame_subject(maximum=256) as subject:
        contract.rejected_frame_preserves_stream(subject)


def test_response_timeout_preserves_stream():
    with bridge_frame_subject(timeout=0.05) as subject:
        contract.response_timeout_preserves_stream(subject)
