"""Pytest collection adapter for the shared behavioral test catalog."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from ..catalog import LAYERS, select_families, validate_catalog
from ..sharding import TIMINGS, parse_shard, shard_plan, validate_timings
from .evidence import canonical, digest, write_json


def pytest_addoption(parser):
    group = parser.getgroup("testkit", "Shared behavioral test framework")
    group.addoption("--test-family", action="append", default=[], metavar="NAME",
                    help="Select a declared behavioral family (repeatable)")
    group.addoption("--test-layer", action="append", default=[], choices=LAYERS,
                    help="Select a contract, adapter, composition, calibration or framework layer")
    group.addoption("--test-inventory", type=Path, metavar="PATH",
                    help="Retain catalog ownership, selected case inventory and outcomes as JSON")
    group.addoption("--test-shard", metavar="K/N",
                    help="Run one-based shard K of N over the selected catalog modules")
    group.addoption("--test-timings", type=Path, metavar="PATH",
                    help="Per-module durations balancing --test-shard (default: " + TIMINGS + ")")


def pytest_configure(config):
    config.addinivalue_line("markers", "test_family(name): behavioral guarantee declared in contracts/testing.json")
    config.addinivalue_line("markers", "test_layer(name): contract, adapter, composition, calibration or framework")
    try:
        plugin = CatalogCollection(config)
    except (OSError, ValueError, TypeError) as error:
        raise pytest.UsageError(str(error)) from error
    config.pluginmanager.register(plugin, "testkit-catalog-collection")


def load_catalog(root):
    """Discover inputs once for both pytest collection and entrypoint preflight."""
    root = Path(root).resolve()
    catalog = json.loads((root / "contracts/testing.json").read_text())
    if not isinstance(catalog, dict) or not isinstance(catalog.get("roots"), list):
        raise ValueError("Test catalog requires declared roots")
    discovered = set()
    for relative in catalog["roots"]:
        directory = root / relative
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            raise ValueError("Test root is missing or outside the checkout: " + relative)
        for path in directory.rglob("*.py"):
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                if not path.resolve().is_relative_to(root):
                    raise ValueError("Test module resolves outside the checkout: " + str(path))
                discovered.add(path.relative_to(root).as_posix())
    return catalog, validate_catalog(catalog, sorted(discovered))


def load_timings(root, path=None):
    """Return the default weight and recorded per-module seconds used for sharding."""
    source = Path(path) if path is not None else Path(root) / TIMINGS
    return validate_timings(json.loads(source.read_text()))


def shard_selection(root, selected, shard, timings=None):
    """Restrict an existing family/layer selection to one balanced shard."""
    parsed = parse_shard(shard)
    if parsed is None:
        return selected, None
    default, weights = load_timings(root, timings)
    plan = shard_plan(selected, parsed, weights=weights, default=default)
    chosen = {path: entry for path, entry in selected.items() if path in set(plan["modules"])}
    return chosen, {"index": parsed[0], "count": parsed[1], "default_seconds": default,
                    "candidate_modules": len(selected), **plan}


class CatalogCollection:
    def __init__(self, config):
        self.config = config
        self.root = Path(config.rootpath).resolve()
        self.catalog, self.assignments = load_catalog(self.root)
        self.selected_modules, self.shard = shard_selection(
            self.root, select_families(self.assignments, config.getoption("test_family"), config.getoption("test_layer")),
            config.getoption("test_shard"), config.getoption("test_timings"))
        self.inventory = {}
        self.selected = set()
        self.collected_modules = set()
        self.outcomes = {}

    def _path(self, value):
        path = Path(value).resolve()
        return path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)

    def pytest_collectreport(self, report):
        if report.passed and report.nodeid in self.assignments:
            self.collected_modules.add(report.nodeid)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, session, config, items):
        deselected = []
        selected = []
        for item in items:
            path = self._path(item.path)
            if path not in self.assignments:
                raise pytest.UsageError("Collected test has no declared family: " + item.nodeid)
            if item.nodeid in self.inventory:
                raise pytest.UsageError("Duplicate collected test id: " + item.nodeid)
            # Explicit node selectors can collect an item without emitting a
            # separate module collect report. The item still proves discovery.
            self.collected_modules.add(path)
            ownership = self.assignments[path]
            self.inventory[item.nodeid] = {"path": path, **ownership}
            item.add_marker(pytest.mark.test_family(ownership["family"]))
            item.add_marker(pytest.mark.test_layer(ownership["layer"]))
            (selected if path in self.selected_modules else deselected).append(item)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
        items[:] = selected
        yield
        self.selected = {item.nodeid for item in items}

    def pytest_collection_finish(self, session):
        if session.testsfailed:
            return
        requested = set()
        for argument in self.config.args:
            path = Path(str(argument).split("::", 1)[0])
            path = path.resolve() if path.is_absolute() else (self.config.invocation_params.dir / path).resolve()
            for relative in self.selected_modules:
                target = self.root / relative
                if target == path or path in target.parents:
                    requested.add(relative)
        missing = sorted(requested - self.collected_modules)
        with_cases = {entry["path"] for entry in self.inventory.values()}
        empty = sorted((requested & self.collected_modules) - with_cases)
        if missing or empty:
            raise pytest.UsageError(f"Declared test modules were not collected: missing={missing}; empty={empty}")
        if not self.selected:
            raise pytest.UsageError("Test selection collected no cases")

    def pytest_runtest_logreport(self, report):
        if report.nodeid not in self.selected:
            return
        if report.failed:
            self.outcomes[report.nodeid] = "failed"
        elif self.outcomes.get(report.nodeid) != "failed":
            if report.skipped:
                self.outcomes[report.nodeid] = "skipped"
            elif report.when == "call":
                self.outcomes[report.nodeid] = "passed"

    def pytest_sessionfinish(self, session, exitstatus):
        output = self.config.getoption("test_inventory")
        if output is None:
            return
        cases = [{"id": name, **entry, "selected": name in self.selected,
                  "outcome": self.outcomes.get(name, "not_run" if name in self.selected else "deselected")}
                 for name, entry in sorted(self.inventory.items())]
        status = "collected" if self.config.option.collectonly and exitstatus == 0 else "passed" if exitstatus == 0 else "failed"
        families = {}
        for name, family in self.catalog["families"].items():
            members = [case for case in cases if case["family"] == name]
            families[name] = {"guarantee": family["guarantee"], "modules": family["modules"],
                              "collected": len(members), "selected": sum(case["selected"] for case in members),
                              "outcomes": dict(Counter(case["outcome"] for case in members))}
        write_json(output, {"schema_version": 1, "catalog_id": self.catalog["id"],
                            "catalog_sha256": digest(canonical(self.catalog)), "status": status,
                            "exit_status": int(exitstatus), "declared_modules": len(self.assignments),
                            "collected_modules": sorted(self.collected_modules), "collected": len(cases),
                            "selected": len(self.selected), "shard": self.shard,
                            "families": families, "cases": cases})
