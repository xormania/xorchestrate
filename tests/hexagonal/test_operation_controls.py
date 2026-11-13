"""Semantic process gates require their own operation's activation receipt."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from tests.hexagonal.support.native import ControlledCallbacks
from tests.hexagonal.support.operation_controls import ProcessOperation
from tests.hexagonal.support.operation_observation import ObservationError


def test_release_requires_activation_but_cleanup_always_unblocks_owned_gate(tmp_path):
    operation = ProcessOperation(tmp_path, "native-peer", "instance-1", "target")
    boundary = operation.hold("tail")
    assert boundary.activation() is None and not boundary.entered()
    with pytest.raises(ObservationError):
        boundary.release()
    assert not Path(boundary.arguments["tail_gate"]).exists(), "UNACTIVATED_GATE_MUST_NOT_RELEASE"
    boundary.close()
    assert Path(boundary.arguments["tail_gate"]).exists(), "CLEANUP_RELEASES_UNACTIVATED_GATE"


@pytest.mark.parametrize("field,value", [
    ("source", "other-peer"), ("incarnation", "old-instance"), ("operation", "competing"),
    ("stage", "start"), ("token", "old-control"),
])
def test_stale_or_foreign_activation_cannot_release_gate(tmp_path, field, value):
    operation = ProcessOperation(tmp_path, "native-peer", "instance-1", "target")
    boundary = operation.hold("tail")
    # The peer receives the exact challenge in operation.arguments and returns
    # it with the stage it reached and its actual native operation identity.
    expected = dict(operation.arguments["operation_observation"])
    expected.update(stage="tail", native_operation="actual-native-turn")
    Path(boundary.arguments["tail_entered"]).write_text(json.dumps({**expected, field: value}))
    with pytest.raises(ObservationError):
        boundary.activation()
    with pytest.raises(ObservationError):
        boundary.release()
    assert not Path(boundary.arguments["tail_gate"]).exists(), "FOREIGN_ACTIVATION_MUST_NOT_RELEASE"
    Path(boundary.arguments["tail_entered"]).write_text(json.dumps(expected))
    assert boundary.activation() == expected
    boundary.release()
    assert Path(boundary.arguments["tail_gate"]).exists(), "CURRENT_ACTIVATION_RELEASES_OWN_GATE"


def test_activation_must_name_actual_native_operation(tmp_path):
    operation = ProcessOperation(tmp_path, "native-peer", "instance-1", "target")
    boundary = operation.hold("tail")
    receipt = {**operation.arguments["operation_observation"], "stage": "tail"}
    Path(boundary.arguments["tail_entered"]).write_text(json.dumps(receipt))
    with pytest.raises(ObservationError):
        boundary.release()


def test_separate_controls_cannot_share_a_release_path_or_challenge(tmp_path):
    first = ProcessOperation(tmp_path, "native-peer", "instance-1", "same-label")
    second = ProcessOperation(tmp_path, "native-peer", "instance-2", "same-label")
    left, right = first.hold("tail"), second.hold("tail")
    assert left.arguments["tail_gate"] != right.arguments["tail_gate"]
    assert left.arguments["tail_entered"] != right.arguments["tail_entered"]
    assert first.arguments["operation_observation"]["token"] != second.arguments["operation_observation"]["token"], \
        "GATE_CHALLENGES_DISTINGUISH_SOURCE_INCARNATIONS"


@pytest.mark.parametrize("overlap", [False, True], ids=["repeated", "overlapping"])
def test_one_prepared_operation_has_one_callback_activation(overlap):
    callbacks = ControlledCallbacks(labels=())
    callbacks.prepare("target", False)
    barrier = Barrier(2) if overlap else None

    def invoke_once():
        if barrier is not None:
            barrier.wait(timeout=2)
        try:
            callbacks.invoke("hold", {"nonce": "target"}, None)
        except ValueError:
            return "refused"
        return "invoked"

    if overlap:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke_once) for _ in range(2)]
            results = [future.result(timeout=2) for future in futures]
    else:
        results = [invoke_once(), invoke_once()]
    assert sorted(results) == ["invoked", "refused"], "CALLBACK_OPERATION_CANNOT_INVOKE_TWICE"
    assert callbacks.finished == ["target"]
    assert callbacks.activation("target")["operation"] == "target"
    callbacks.prepare("different", False)
    callbacks.invoke("hold", {"nonce": "different"}, None)
    assert callbacks.finished == ["target", "different"], "DISTINCT_CALLBACK_OPERATIONS_REMAIN_INDEPENDENT"
