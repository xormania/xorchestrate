"""Read registered fault inventories from a directory of JSON files.

An edge adapter for the calibration service: it validates the registry and
inventory files against ``schema.json`` in the same directory and builds
inventory values. It never reads the source tree a case mutates.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from jsonschema import Draft202012Validator

from .fault_calibration import FaultCase, Inventory, Mutation

REGISTRY = "registry.json"
SCHEMA = "schema.json"
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


def _validate(directory: Path, value, definition: str, code: str, name: str) -> None:
    schema = _read(directory / SCHEMA, code)
    validator = Draft202012Validator({**schema, "$ref": "#/$defs/" + definition})
    problem = next(iter(sorted(validator.iter_errors(value), key=lambda error: list(error.path))), None)
    if problem is not None:
        location = "/".join(str(part) for part in problem.path)
        raise InventoryError(code, name + (" at " + location if location else ""))


def _registered(directory: Path) -> dict:
    directory = Path(directory)
    value = _read(directory / REGISTRY, "invalid_registry")
    _validate(directory, value, "registry", "invalid_registry", REGISTRY)
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
    path, _, test = selector.partition("::")
    return path.removesuffix(".py").replace("/", ".") + "::" + test


def load_inventory(directory: Path, name: str) -> Inventory:
    directory = Path(directory)
    entries = _registered(directory)
    if name not in entries:
        raise InventoryError("unknown_inventory", name)
    value = _read(directory / entries[name]["file"], "invalid_inventory")
    _validate(directory, value, "inventory", "invalid_inventory", name)
    names = [case["name"] for case in value["cases"]]
    if len(names) != len(set(names)):
        raise InventoryError("invalid_inventory", name + " repeats a case name")
    cases = []
    for case in value["cases"]:
        selected = {_module_form(selector) for selector in case["selectors"]}
        if case["target"] not in selected or not set(case["unaffected"]) <= selected:
            raise InventoryError("target_not_selected", name + "/" + case["name"])
        cases.append(FaultCase(name=case["name"], mutation=Mutation(**case["mutation"]),
                               selectors=tuple(case["selectors"]), target=case["target"],
                               diagnostic=case["diagnostic"], unaffected=tuple(case["unaffected"]),
                               expect=case.get("expect", "detected")))
    return Inventory(name=name, cases=tuple(cases))


def select(registry: Mapping[str, Sequence[str]], *, names: Sequence[str] = (), stage: str | None = None
           ) -> tuple[str, ...]:
    """The named inventories in the order given, else those registered at ``stage`` (default ``ci``)."""
    if names:
        unknown = [name for name in names if name not in registry]
        if unknown:
            raise InventoryError("unknown_inventory", ", ".join(unknown))
        return tuple(names)
    wanted = stage or DEFAULT_STAGE
    return tuple(name for name, stages in registry.items() if wanted in stages)
