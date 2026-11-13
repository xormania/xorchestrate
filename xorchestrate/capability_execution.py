"""Trusted capability execution choices, separate from discovery metadata.

These policies come from local composition or installed provider code. Neither
tool arguments nor remote descriptors may select retention or execution behavior.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from .model import OrchestrationError, public_error_code
from .outcomes import effect_outcome

DEFAULT_EXECUTION = {"mode": "journaled", "observation": "full"}
_OBSERVATION = ContextVar("capability_observation", default="full")


def execution_policies(value, *, qualified=True):
    """Validate and detach exact capability-address policy selections."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise OrchestrationError("Capability execution must be an object.", "configuration_error")
    result = {}
    for address, supplied in value.items():
        valid_address = isinstance(address, str) and bool(address)
        if valid_address and qualified:
            provider, separator, name = address.partition("/")
            valid_address = bool(provider and separator and name)
        if not valid_address or not isinstance(supplied, dict) or supplied.keys() - DEFAULT_EXECUTION.keys():
            raise OrchestrationError("Invalid capability execution selection.", "configuration_error")
        policy = {**DEFAULT_EXECUTION, **supplied}
        if policy["mode"] not in ("journaled", "query", "receipt") or policy["observation"] not in ("full", "none"):
            raise OrchestrationError("Invalid capability execution policy.", "configuration_error")
        result[address] = deepcopy(policy)
    return result


@contextmanager
def observation_scope(policy):
    # An inner full-capture call cannot undo an outer private call's choice.
    token = _OBSERVATION.set("none" if policy["observation"] == "none" else _OBSERVATION.get())
    try:
        yield
    finally:
        _OBSERVATION.reset(token)


def observation_suppressed():
    return _OBSERVATION.get() == "none"


def private_error(error):
    """Keep outcome knowledge without retaining arbitrary provider diagnostics."""
    return OrchestrationError("Capability execution did not return a publishable result.",
        public_error_code(error), {"outcome": effect_outcome(error)})


def request_binding(arguments):
    """The non-payload scope needed to authorize retained execution receipts."""
    return {key: deepcopy(arguments[key]) for key in ("capability", "work_id", "operation_id") if key in arguments}


def effect_receipt(operation_id, status="completed"):
    return {"operation_id": operation_id, "status": status, "result_retained": False}
