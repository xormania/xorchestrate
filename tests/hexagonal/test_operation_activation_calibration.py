"""Observe the held boundary itself when activation verification is bypassed."""
from pathlib import Path

from tests.hexagonal.support.operation_controls import ProcessOperation
from tests.hexagonal.support.operation_observation import ObservationError


def test_unwitnessed_release_has_no_process_side_effect(tmp_path):
    boundary = ProcessOperation(tmp_path, "peer", "incarnation", "work").hold("tail")
    try:
        boundary.release()
    except ObservationError:
        pass
    assert not Path(boundary.arguments["tail_gate"]).exists(), "SCHEDULE_REQUIRES_VERIFIED_ACTIVATION"
