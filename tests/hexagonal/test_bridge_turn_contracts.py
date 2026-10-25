"""Turn ownership contract applied to interchangeable bridge transports."""
import pytest

from tests.hexagonal.contracts.completion import preserve_turn_ownership
from tests.hexagonal.support.bridge_turns import bridge_turn_fixture


@pytest.mark.parametrize("transport", ["fifo", "unix"])
@pytest.mark.parametrize("turn_field", ["turn_id", "prompt_id"])
def test_asynchronous_native_call_preserves_turn_ownership(transport, turn_field, tmp_path):
    with bridge_turn_fixture(tmp_path, transport, turn_field) as fixture:
        preserve_turn_ownership(fixture)
