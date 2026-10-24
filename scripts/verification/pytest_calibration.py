"""Trusted pytest observer for assertion-specific calibration.

Loaded explicitly by the checker from a retained copy. The exception's terminal
Python frame, not a formatted traceback, establishes whether the declared test
actually executed its own assertion. A helper failing before that assertion is
an unrelated failure even if it happens to reuse the expected marker.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


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
            "exception_type": type(error).__name__, "target_assertion": False}
    if trace is not None:
        code = trace.tb_frame.f_code
        data.update(file=code.co_filename, line=trace.tb_lineno, function=code.co_name)
        try:
            syntax = ast.parse(Path(code.co_filename).read_text())
            is_assert = any(isinstance(node, ast.Assert) and
                            node.lineno <= trace.tb_lineno <= node.end_lineno
                            for node in ast.walk(syntax))
        except (OSError, SyntaxError, UnicodeError):
            is_assert = False
        data["target_assertion"] = (
            type(error) is AssertionError and code is getattr(item.obj, "__code__", None)
            and is_assert
        )
    value = json.dumps(data, sort_keys=True)
    item.user_properties.append(("calibration.attribution", value))
    report.user_properties = list(item.user_properties)
