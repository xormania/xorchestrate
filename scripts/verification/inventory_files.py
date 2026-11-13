"""Read registered fault inventories from a directory of JSON files.

An edge adapter for the calibration service: it validates the registry and
inventory files against this package's own ``inventories/schema.json`` (a
registry directory cannot supply a weaker schema) and builds inventory values. It never reads the source tree a case mutates.
"""
from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from jsonschema import Draft202012Validator

from .fault_calibration import FaultCase, Inventory, Mutation

REGISTRY = "registry.json"
SCHEMA = "schema.json"
TRUSTED_SCHEMA = Path(__file__).with_name("inventories") / SCHEMA
DEFAULT_STAGE = "ci"


class InventoryError(ValueError):
    """A registry or inventory problem, identified by a typed code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code


def _read(path: Path, code: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise InventoryError(code, path.name) from error


def _validate(value, definition: str, code: str, name: str) -> None:
    schema = _read(TRUSTED_SCHEMA, code)
    validator = Draft202012Validator({**schema, "$ref": "#/$defs/" + definition})
    problem = next(iter(sorted(validator.iter_errors(value), key=lambda error: list(error.path))), None)
    if problem is not None:
        location = "/".join(str(part) for part in problem.path)
        raise InventoryError(code, name + (" at " + location if location else ""))


def _registered(directory: Path) -> dict:
    directory = Path(directory)
    value = _read(directory / REGISTRY, "invalid_registry")
    _validate(value, "registry", "invalid_registry", REGISTRY)
    entries = value["inventories"]
    for name, entry in entries.items():
        if not (directory / entry["file"]).is_file():
            raise InventoryError("invalid_registry", name + " names a missing file")
    files = {entry["file"] for entry in entries.values()}
    if len(files) != len(entries):
        raise InventoryError("invalid_registry", "two inventories share a file")
    for path in sorted(directory.glob("*.json")):
        if path.name not in {REGISTRY, SCHEMA} and path.name not in files:
            raise InventoryError("unregistered_inventory_file", path.name)
    return entries


def load_registry(directory: Path) -> dict[str, tuple[str, ...]]:
    """Each registered inventory's name and the stages it runs at, in registry order."""
    return {name: tuple(entry["stages"]) for name, entry in _registered(directory).items()}


def _module_form(selector: str) -> str:
    """The JUnit identity of a selected test: a test class joins the module, as ``junit_cases`` keys it."""
    path, *scopes, test = selector.split("::")
    return ".".join([path.removesuffix(".py").replace("/", "."), *scopes]) + "::" + test


def load_inventory(directory: Path, name: str) -> Inventory:
    directory = Path(directory)
    entries = _registered(directory)
    if name not in entries:
        raise InventoryError("unknown_inventory", name)
    value = _read(directory / entries[name]["file"], "invalid_inventory")
    _validate(value, "inventory", "invalid_inventory", name)
    names = [case["name"] for case in value["cases"]]
    if len(names) != len(set(names)):
        raise InventoryError("invalid_inventory", name + " repeats a case name")
    cases = []
    for case in value["cases"]:
        selected = {_module_form(selector) for selector in case["selectors"]}
        if case["target"] not in selected or not set(case["unaffected"]) <= selected:
            raise InventoryError("target_not_selected", name + "/" + case["name"])
        if case["target"] in case["unaffected"]:
            raise InventoryError("invalid_inventory", name + "/" + case["name"] + " lists its target as unaffected")
        cases.append(FaultCase(name=case["name"], mutation=Mutation(**case["mutation"]),
                               selectors=tuple(case["selectors"]), target=case["target"],
                               diagnostic=case["diagnostic"], unaffected=tuple(case["unaffected"]),
                               expect=case.get("expect", "detected"), contract=case.get("contract")))
    return Inventory(name=name, cases=tuple(cases))


def select(registry: Mapping[str, Sequence[str]], *, names: Sequence[str] = (), stage: str | None = None
           ) -> tuple[str, ...]:
    """The named inventories in the order given, else those registered at ``stage`` (default ``ci``)."""
    if names:
        unknown = [name for name in names if name not in registry]
        if unknown:
            raise InventoryError("unknown_inventory", ", ".join(unknown))
        return tuple(dict.fromkeys(names))
    wanted = stage or DEFAULT_STAGE
    return tuple(name for name, stages in registry.items() if wanted in stages)


def _contract_function(root: Path, contract: str):
    """The top-level function a ``module:function`` contract names in the tree under ``root``, else None."""
    module, _, name = contract.partition(":")
    base = Path(root).joinpath(*module.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, SyntaxError):
                return None
            return next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == name), None)
    return None


def check_inventory_tree(inventory: Inventory, root: Path) -> None:
    """Refuse a case whose declared contract function is absent or does not assert its diagnostic.

    A declared contract must be a top-level function in the candidate tree holding ``assert <condition>,
    "<diagnostic>"`` with exactly that constant message. Cases without a contract are left to the
    repository's standard test, which finds each diagnostic's one assertion in its target test.
    """
    for case in inventory.cases:
        if case.contract is None:
            continue
        function = _contract_function(root, case.contract)
        label = inventory.name + "/" + case.name
        if function is None:
            raise InventoryError("contract_not_found", label + " declares " + case.contract)
        if not any(isinstance(node, ast.Assert) and isinstance(node.msg, ast.Constant)
                   and node.msg.value == case.diagnostic for node in ast.walk(function)):
            raise InventoryError("diagnostic_not_in_contract", label)
