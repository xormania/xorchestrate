"""Pure classification of executable test modules by current behavioral guarantee.

The catalog declares ownership, not test counts or expected verdicts. Collection
adapters supply discovered paths; undeclared, missing and multiply-owned modules
are errors instead of silently expanding or shrinking the test plan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

LAYERS = ("contract", "adapter", "composition", "calibration", "framework")


def _relative_path(value):
    return (isinstance(value, str) and bool(value) and not value.startswith("/")
            and "\\" not in value and all(part not in {"", ".", ".."} for part in value.split("/")))


def validate_catalog(catalog: Mapping, discovered_paths: Sequence[str]):
    """Return one declared family/layer/guarantee for every discovered module."""
    if not isinstance(catalog, dict) or set(catalog) != {"schema_version", "id", "roots", "families"}:
        raise ValueError("Test catalog requires schema_version, id, roots and families")
    if (type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1
            or not isinstance(catalog["id"], str) or not catalog["id"].strip()):
        raise ValueError("Test catalog requires schema_version 1 and a nonempty id")
    roots = catalog["roots"]
    if (not isinstance(roots, list) or not roots or any(not _relative_path(root) for root in roots)
            or len(roots) != len(set(roots))):
        raise ValueError("Test catalog roots must be distinct relative directories")
    if any(first != second and second.startswith(first + "/") for first in roots for second in roots):
        raise ValueError("Test catalog roots must not overlap")
    families = catalog["families"]
    if not isinstance(families, dict) or not families:
        raise ValueError("Test catalog must declare at least one behavioral family")
    assignments = {}
    for name, family in families.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(family, dict):
            raise ValueError("Test families need nonempty names and object declarations")
        if set(family) != {"guarantee", "modules"} or not isinstance(family["guarantee"], str) or not family["guarantee"].strip():
            raise ValueError(f"Test family {name!r} requires its current guarantee and modules")
        modules = family["modules"]
        if not isinstance(modules, dict) or not modules:
            raise ValueError(f"Test family {name!r} must own executable modules")
        for path, layer in modules.items():
            if (not _relative_path(path) or not path.endswith(".py")
                    or not any(path.startswith(root + "/") for root in roots)):
                raise ValueError(f"Test module {path!r} must be a relative Python path inside a declared root")
            if layer not in LAYERS:
                raise ValueError(f"Test module {path!r} has unknown layer {layer!r}")
            if path in assignments:
                raise ValueError(f"Test module {path!r} has multiple family owners")
            assignments[path] = {"family": name, "layer": layer, "guarantee": family["guarantee"]}
    discovered = tuple(discovered_paths)
    if (any(not _relative_path(path) for path in discovered)
            or len(discovered) != len(set(discovered))):
        raise ValueError("Discovered test module paths must be distinct relative paths")
    missing = sorted(assignments.keys() - set(discovered))
    unknown = sorted(set(discovered) - assignments.keys())
    if missing or unknown:
        raise ValueError(f"Test catalog does not match discovery: missing={missing}; unclassified={unknown}")
    return dict(sorted(assignments.items()))


def select_families(assignments, requested=(), layers=()):
    """Select declared modules without manufacturing a successful empty plan."""
    families = set(requested)
    wanted_layers = set(layers)
    known = {entry["family"] for entry in assignments.values()}
    if families - known:
        raise ValueError("Unknown test families: " + ", ".join(sorted(families - known)))
    if wanted_layers - set(LAYERS):
        raise ValueError("Unknown test layers: " + ", ".join(sorted(wanted_layers - set(LAYERS))))
    selected = {path: entry for path, entry in assignments.items()
                if (not families or entry["family"] in families)
                and (not wanted_layers or entry["layer"] in wanted_layers)}
    if not selected:
        raise ValueError("Test selection contains no declared modules")
    return selected
