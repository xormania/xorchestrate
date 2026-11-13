"""The calibrated-check standard: one plain assertion per identifier, where the case says it is.

A calibrated check is ``assert condition, "ID"`` with an UPPER_SNAKE identifier equal to its
inventory case's diagnostic, written once in the tests, inside the case's target test or the one
contract function the case declares. ``refusal(call, code)`` lets such an assertion state a
refusal guarantee without asserting anything itself.
"""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

from tests.hexagonal.support.fault_calibration import INVENTORIES, ROOT, loader

IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")


def _assert_sites():
    """Every ``assert ..., "<constant>"`` in the tests, by message: (path, enclosing top-level function)."""
    sites = {}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for top in tree.body:
            for node in ast.walk(top):
                if isinstance(node, ast.Assert) and isinstance(node.msg, ast.Constant) and isinstance(node.msg.value,
                                                                                                    str):
                    function = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
                    sites.setdefault(node.msg.value, []).append((path.relative_to(ROOT).as_posix(), function))
    return sites


def _registered_cases():
    files = loader()
    registry = files.load_registry(INVENTORIES)
    return [(name, case) for name in registry for case in files.load_inventory(INVENTORIES, name).cases]


def _expected_site(case):
    if case.contract:
        module, _, function = case.contract.partition(":")
    else:
        module, _, test = case.target.partition("::")
        function = test.split("[", 1)[0]
    return module.replace(".", "/") + ".py", function


def test_every_calibrated_identifier_is_upper_snake_and_asserted_once_where_its_case_says():
    sites = _assert_sites()
    problems = []
    for inventory, case in _registered_cases():
        label = f"{inventory}/{case.name}"
        if not IDENTIFIER.fullmatch(case.diagnostic):
            problems.append((label, "not UPPER_SNAKE", case.diagnostic))
            continue
        found = sites.get(case.diagnostic, [])
        if len(found) != 1:
            problems.append((label, f"{len(found)} assert sites", found))
        elif found[0] != _expected_site(case):
            problems.append((label, "asserted outside its target test or declared contract", found[0]))
    assert problems == [], problems


# refusal --------------------------------------------------------------------------------------------

def refusal():
    return importlib.import_module("tests.hexagonal.contracts.calibrated").refusal


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _raise(error):
    def call():
        raise error
    return call


def test_refusal_is_true_only_for_a_refusal_carrying_the_code():
    check = refusal()
    assert check(_raise(Refused("terminal_unknown")), "terminal_unknown") is True
    assert check(_raise(Refused("terminal_exists")), ("terminal_unknown", "terminal_exists")) is True
    assert check(_raise(Refused("terminal_exists")), "terminal_unknown") is False
    assert check(lambda: "accepted", "terminal_unknown") is False


@pytest.mark.parametrize("error", [TimeoutError("unrelated"), AssertionError("REFUSAL_EXPECTED"), KeyError("x")])
def test_refusal_lets_an_exception_without_a_code_through(error):
    with pytest.raises(type(error)):
        refusal()(_raise(error), "terminal_unknown")


def test_refusal_never_asserts_or_fails_a_test_itself():
    source = inspect.getsource(refusal())
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), source
    assert "pytest" not in source and "AssertionError" not in source, source


def test_refusal_lives_in_the_contracts_layer():
    module = importlib.import_module("tests.hexagonal.contracts.calibrated")
    assert Path(module.__file__).resolve() == ROOT / "tests/hexagonal/contracts/calibrated.py"
