"""Trusted pytest observer for assertion-specific calibration.

Loaded explicitly by the checker from a retained copy. The exception's terminal
Python frame and raising instruction establish whether the declared test
executed its own assertion. A helper failure or another statement on the same
line is unrelated even if it reuses the expected marker. Missing instruction
positions cannot establish assertion execution and earn no detection credit.

A case may declare one contract function (``--calibration-contract
module:function``) that holds its check. An assertion raised there is a
contract assertion only when the declared test called it during its own call
phase; any other function, an explicit raise or an unimportable declaration
earns nothing.
"""
from __future__ import annotations

import ast
import dis
import inspect
import json
import sys
from pathlib import Path

import pytest


def _assertion_location(code, offset):
    """Locate an actual assertion raise using Python 3.11+ source positions.

    Pytest's rewritten raise spans the Assert; plain Python can locate it at
    the assertion condition instead. Both must be contained in exactly one
    Assert, with full line/column positions. A nearby explicit raise or an
    exception evaluating the condition is not an assertion failure.
    """
    instruction = next((entry for entry in dis.get_instructions(code, show_caches=True)
                        if entry.offset == offset), None)
    data = {"instruction_offset": offset, "instruction": instruction.opname if instruction else None,
            "instruction_span": None, "assertion_span": None}
    if instruction is None:
        return data
    positions = instruction.positions
    if positions is None or any(value is None for value in positions):
        return data
    start = positions.lineno, positions.col_offset
    end = positions.end_lineno, positions.end_col_offset
    data["instruction_span"] = [*start, *end]
    if instruction.opname != "RAISE_VARARGS" or instruction.arg != 1 or start >= end:
        return data
    try:
        syntax = ast.parse(Path(code.co_filename).read_text())
    except (OSError, SyntaxError, UnicodeError):
        return data
    matches = [node for node in ast.walk(syntax) if isinstance(node, ast.Assert)
               and (node.lineno, node.col_offset) <= start
               and end <= (node.end_lineno, node.end_col_offset)]
    if len(matches) == 1:
        node = matches[0]
        data["assertion_span"] = [node.lineno, node.col_offset, node.end_lineno, node.end_col_offset]
    return data


def pytest_addoption(parser):
    parser.addoption("--calibration-contract", default=None,
                     help="module:function of the contract function whose assertions count for the selected tests")


def _contract_code(config):
    """The declared contract function's own code, unwrapped from decorators; None when it cannot be resolved.

    Only a module the run already imported is consulted: a contract that was never imported cannot have
    raised, and the observer itself never imports subject code.
    """
    declared = config.getoption("--calibration-contract")
    if not declared:
        return None
    module, _, name = declared.partition(":")
    try:
        function = inspect.unwrap(getattr(sys.modules[module], name))
    except Exception:  # An unresolvable declaration earns no credit; it never breaks the run.
        return None
    return getattr(function, "__code__", None)


def _frames(trace):
    while trace is not None:
        yield trace
        trace = trace.tb_next


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or call.excinfo is None:
        return
    error = call.excinfo.value
    trace = error.__traceback__
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    data = {"phase": call.when, "test_name": item.name,
            "exception_type": type(error).__name__, "target_assertion": False, "contract_assertion": False}
    if trace is not None:
        code = trace.tb_frame.f_code
        data.update(file=code.co_filename, line=trace.tb_lineno, function=code.co_name)
        data.update(_assertion_location(code, trace.tb_lasti))
        test_code = getattr(item.obj, "__code__", None)
        data["target_assertion"] = (
            type(error) is AssertionError and code is test_code
            and data["assertion_span"] is not None
        )
        contract = _contract_code(item.config)
        data["contract_assertion"] = (
            contract is not None and type(error) is AssertionError and code is contract
            and data["assertion_span"] is not None
            and any(frame.tb_frame.f_code is test_code for frame in _frames(error.__traceback__))
        )
    value = json.dumps(data, sort_keys=True)
    item.user_properties.append(("calibration.attribution", value))
    report.user_properties = list(item.user_properties)
