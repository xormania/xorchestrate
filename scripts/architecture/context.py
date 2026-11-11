"""Select complete, measured Python units for bounded architecture diagnosis.

Only the deterministic inventory is eligible. AST/import/call relationships are
selection evidence, not a claim that a call executes or a test covers behavior.
"""
from __future__ import annotations

import ast
import hashlib
import tokenize
from collections import Counter, defaultdict
from copy import deepcopy
from fnmatch import fnmatchcase
from io import BytesIO
from pathlib import Path

from .context_changes import base_blob, comparison
from .context_relations import neighborhood, references


def changed_lines(root: Path, base_ref: str) -> list[dict]:
    """Return changed new-side line ranges against a commit, including worktree edits."""
    _, changes = comparison(root, base_ref)
    return [span for change in changes for span in change["after"]]


def _start(node):
    return min([node.lineno, *(item.lineno for item in getattr(node, "decorator_list", ()))])


def _units(item, text, tree):
    lines, units = text.splitlines(keepends=True), []

    def add(node, symbol):
        start, end = _start(node), node.end_lineno
        unit_id = hashlib.sha256(f"{item['path']}:{item['sha256']}:{start}:{end}:{symbol}".encode()).hexdigest()[:24]
        units.append({"unit_id": unit_id, "path": item["path"], "module": item.get("module"),
                      "role": item.get("role"), "sha256": item["sha256"], "symbol": symbol,
                      "kind": type(node).__name__, "start_line": start, "end_line": end,
                      "source": "".join(lines[start - 1:end]), "truncated": False,
                      "_node": node})

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node, node.name)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add(child, node.name + "." + child.name)
        else:
            add(node, f"<module:{node.lineno}>")
    return units


def _public(unit, relation):
    return {**{key: value for key, value in unit.items() if not key.startswith("_")}, "relation": relation}


def _overlaps(unit, span):
    return unit["start_line"] <= span["end_line"] and span["start_line"] <= unit["end_line"]


def _matching(units, span):
    matches = [unit for unit in units if unit["path"] == span["path"] and _overlaps(unit, span)]
    containers = [unit for unit in matches if unit["start_line"] <= span["start_line"]
                  and unit["end_line"] >= span["end_line"]]
    matches = containers or matches
    return [unit for unit in matches if not any(
        other is not unit and other["start_line"] >= unit["start_line"]
        and other["end_line"] <= unit["end_line"] for other in matches)]


def _base_units(report, root, commit, changes, by_path):
    """Admit immutable old blobs only within the currently declared source scope.

    Old code is judged against the supplied current contract, not a reconstructed
    historical policy. This distinction is retained in each old source record.
    """
    units, excluded, missing = [], [], []
    profile = report.get("profile", {})
    for change in changes:
        path = change["path"]
        if not path.endswith(".py"):
            continue
        item = by_path.get(path)
        if item is None:
            parts = Path(path).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            roles = [name for name, role in profile.get("roles", {}).items()
                     if any(fnmatchcase(module, selector) for selector in role.get("modules", []))]
            if (not any(path.startswith(source + "/") for source in profile.get("source_roots", []))
                    or len(roles) != 1):
                excluded.append({"path": path, "reason": "base_outside_declared_scope", "side": "before"})
                continue
            item = {"path": path, "module": module, "role": roles[0]}
        try:
            raw = base_blob(root, commit, path)
            if raw is None:
                continue
            encoding, _ = tokenize.detect_encoding(BytesIO(raw).readline)
            text = raw.decode(encoding)
            tree = ast.parse(text, filename=path)
            historical = _units({**item, "sha256": hashlib.sha256(raw).hexdigest()}, text, tree)
            module = item.get("module") or path.removesuffix(".py").replace("/", ".")
            imports = _imports(tree, module, Path(path).name == "__init__.py")
            for unit in historical:
                unit.update(unit_id=hashlib.sha256(("before:" + commit + ":" + unit["unit_id"]).encode()).hexdigest()[:24],
                            side="before", git_commit=commit, contract_basis="current_profile", _bindings=imports)
            units.extend(historical)
        except (ValueError, UnicodeError, SyntaxError) as error:
            missing.append({"path": path, "reason": "base_source_unavailable", "error_type": type(error).__name__})
    return units, excluded, missing


def _role_order(units, priorities):
    if not priorities:
        return units
    groups = defaultdict(list)
    for unit in units:
        groups[unit.get("role")].append(unit)
    order = [role for role in priorities if role in groups]
    order.extend(role for role in groups if role not in order)
    result = []
    for index in range(max(map(len, groups.values()), default=0)):
        result.extend(groups[role][index] for role in order if index < len(groups[role]))
    return result


def _imports(tree, module, is_package):
    """Bind imported local names to module/symbol without importing inspected code."""
    import importlib.util

    bindings = {}
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                bindings[local] = (alias.name if alias.asname else local, None)
        elif isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            if node.level:
                try:
                    target = importlib.util.resolve_name(target, package)
                except ImportError:
                    raise ValueError("Architecture relative import cannot be resolved in its measured package.") from None
            for alias in node.names:
                bindings[alias.asname or alias.name] = (target, alias.name)
    return bindings


def build_context(report: dict, root: Path, *, max_source_bytes=24000, max_files=8,
                  source_paths=(), changed_ranges=(), base_ref=None, max_candidates=1,
                  role_priorities=()) -> dict:
    """Build a source-bound packet; budgets omit whole units instead of slicing them."""
    for name, value, maximum in (("max_source_bytes", max_source_bytes, 1000000),
                                 ("max_files", max_files, 1000), ("max_candidates", max_candidates, 100)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be a positive integer no greater than {maximum}.")
    if not isinstance(source_paths, (list, tuple)) or any(not isinstance(path, str) or not path for path in source_paths):
        raise ValueError("source_paths must select measured paths.")
    roles = report.get("profile", {}).get("roles", {})
    if (not isinstance(role_priorities, (list, tuple))
            or any(not isinstance(role, str) or role not in roles for role in role_priorities)
            or len(set(role_priorities)) != len(role_priorities)):
        raise ValueError("role_priorities must select distinct declared roles.")
    if not isinstance(changed_ranges, (list, tuple)) or any(
        not isinstance(span, dict) or set(span) != {"path", "start_line", "end_line"}
        or not isinstance(span["path"], str) or not span["path"]
        or type(span["start_line"]) is not int or type(span["end_line"]) is not int
        or not 1 <= span["start_line"] <= span["end_line"] for span in changed_ranges
    ):
        raise ValueError("changed_ranges must identify positive measured source spans.")
    inventory = report.get("files", [])
    if not isinstance(inventory, list) or any(not isinstance(item, dict) for item in inventory):
        raise ValueError("Architecture report has an invalid source inventory.")
    by_path = {item.get("path"): item for item in inventory}
    if len(by_path) != len(inventory) or any(not isinstance(path, str) for path in by_path):
        raise ValueError("Architecture source paths must be distinct strings.")
    if any(path not in by_path for path in source_paths):
        raise ValueError("A requested diagnostic source is outside the measured inventory.")
    root, all_units, bindings = Path(root).resolve(), [], {}
    for path, item in sorted(by_path.items()):
        source = (root / path).resolve()
        if not source.is_relative_to(root):
            raise ValueError("Architecture source path escapes the repository.")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
            raise ValueError("Architecture source changed after deterministic measurement.")
        encoding, _ = tokenize.detect_encoding(BytesIO(raw).readline)
        text = raw.decode(encoding)
        tree = ast.parse(text, filename=path)
        all_units.extend({**unit, "side": "after"} for unit in _units(item, text, tree))
        module = item.get("module") or path.removesuffix(".py").replace("/", ".")
        bindings[path] = _imports(tree, module, Path(path).name == "__init__.py")

    commit, changes = comparison(root, base_ref) if base_ref is not None else (None, [])
    ranges = [*changed_ranges, *(span for change in changes for span in change["after"])]
    old_units, excluded, missing_inputs = _base_units(report, root, commit, changes, by_path)
    candidate_units = []
    targeted = base_ref is not None or bool(changed_ranges)
    if targeted:
        for span in ranges:
            if span["path"] not in by_path:
                excluded.append({**span, "reason": "outside_measured_inventory"})
                continue
            leaves = _matching(all_units, span)
            candidate_units.extend(leaves)
            if not leaves:
                excluded.append({**span, "reason": "no_source_unit"})
        current_symbols = {(unit["path"], unit["symbol"], unit["kind"]): unit for unit in all_units}
        for change in changes:
            for span in change["before"]:
                # A pure statement deletion has no nonempty new-side span.
                # Select its surviving behavior unit, or the deleted old unit,
                # so removed guards remain reviewable as complete pairs.
                for unit in _matching(old_units, span):
                    candidate_units.append(current_symbols.get(
                        (unit["path"], unit["symbol"], unit["kind"]), unit))
    else:
        finding_paths = [finding.get("path") for finding in report.get("findings", [])]
        priorities = list(dict.fromkeys([*source_paths, *finding_paths]))
        roles = report.get("profile", {}).get("roles", {})

        def rank(unit):
            role = roles.get(unit.get("role"), {})
            return (priorities.index(unit["path"]) if unit["path"] in priorities else len(priorities),
                    bool(role.get("allow_dynamic", False)),
                    unit["kind"] not in {"FunctionDef", "AsyncFunctionDef", "ClassDef"},
                    unit["path"], unit["start_line"])

        candidate_units = sorted(all_units, key=rank)
    if targeted:
        # Added files include docstrings/imports in the same diff as behavior.
        # Spend candidate slots on complete behavior and declarations first;
        # imports still enter the related-context packet independently.
        substantive = {"FunctionDef": 0, "AsyncFunctionDef": 0, "ClassDef": 1,
                       "Assign": 2, "AnnAssign": 2}
        candidate_units.sort(key=lambda unit: substantive.get(unit["kind"], 3))
    candidate_units = list({unit["unit_id"]: unit for unit in candidate_units}.values())
    candidate_units = _role_order(candidate_units, role_priorities)
    selected, before_selected, candidates, used, paths, oversize = [], [], [], {}, set(), []
    remaining = max_source_bytes

    def include(unit, relation, candidate_id):
        nonlocal remaining
        if unit["unit_id"] in used:
            record = used[unit["unit_id"]]
            if candidate_id not in record["support_for"]:
                record["support_for"].append(candidate_id)
            return True
        size = len(unit["source"].encode("utf-8"))
        reason = "file_limit" if unit["path"] not in paths and len(paths) >= max_files else (
            "source_budget" if size > remaining else None)
        if reason:
            excluded.append({"path": unit["path"], "unit_id": unit["unit_id"],
                             "start_line": unit["start_line"], "end_line": unit["end_line"],
                             "reason": reason, "relation": relation, "candidate_id": candidate_id})
            missing_inputs.append({"candidate_id": candidate_id, "path": unit["path"],
                                   "symbol": unit["symbol"], "reason": reason, "relation": relation})
            if size > max_source_bytes:
                oversize.append(unit["unit_id"])
            return False
        paths.add(unit["path"])
        remaining -= size
        record = {**_public(unit, relation), "support_for": [candidate_id]}
        used[unit["unit_id"]] = record
        (before_selected if unit.get("side") == "before" else selected).append(record)
        return True

    old_by_symbol = {(unit["path"], unit["symbol"], unit["kind"]): unit for unit in old_units}
    for unit in candidate_units:
        if len(candidates) >= max_candidates:
            if targeted:
                excluded.append({"path": unit["path"], "unit_id": unit["unit_id"],
                                 "start_line": unit["start_line"], "end_line": unit["end_line"],
                                 "reason": "candidate_limit"})
            continue
        before = unit if unit.get("side") == "before" else old_by_symbol.get((unit["path"], unit["symbol"], unit["kind"]))
        pair = [unit] if before is None or before is unit else [unit, before]
        size = sum(len(item["source"].encode("utf-8")) for item in pair if item["unit_id"] not in used)
        reason = "file_limit" if unit["path"] not in paths and len(paths) >= max_files else (
            "source_budget" if size > remaining else None)
        if reason:
            excluded.append({"path": unit["path"], "unit_id": unit["unit_id"], "reason": reason,
                             "relation": "candidate_pair", "candidate_id": unit["unit_id"]})
            missing_inputs.append({"candidate_id": unit["unit_id"], "path": unit["path"],
                                   "reason": "candidate_pair_omitted"})
            if size > max_source_bytes:
                oversize.append(unit["unit_id"])
            continue
        if include(unit, "candidate", unit["unit_id"]):
            if before is not None and before is not unit:
                include(before, "candidate_before", unit["unit_id"])
            role = unit.get("role")
            declaration = report.get("profile", {}).get("roles", {}).get(role, {})
            candidates.append({key: unit[key] for key in (
                "unit_id", "path", "module", "role", "symbol", "start_line", "end_line", "sha256", "side")})
            candidates[-1].update(candidate_id=unit["unit_id"], contract={
                "profile_id": report.get("profile", {}).get("id"), "role": role,
                "declaration": deepcopy(declaration)})
            unavailable = any(item.get("path") == unit["path"] and item["reason"] == "base_source_unavailable"
                              for item in missing_inputs)
            kind = ("deleted" if unit.get("side") == "before" else "modified" if before else
                    "uncompared" if unavailable or not commit else "added")
            candidates[-1]["change"] = {"kind": kind, "base_commit": commit,
                                         "before_unit_id": before["unit_id"] if before else None,
                                         "after_unit_id": unit["unit_id"] if unit.get("side") != "before" else None}
            for missing in list(missing_inputs):
                if missing.get("path") == unit["path"] and "candidate_id" not in missing:
                    missing_inputs.append({**missing, "candidate_id": unit["unit_id"]})

    # All candidate pairs get first use of the budget. Static references then
    # connect declarations, callers/registrations and fixture bindings. Their
    # presence never establishes that a test executed or covered a behavior.
    graph_units = [*all_units, *(unit for unit in old_units if any(
        candidate["candidate_id"] == unit["unit_id"] for candidate in candidates))]
    edges, absent = references(graph_units, bindings)
    related = []
    for candidate in candidates:
        rows, missing = neighborhood(candidate, graph_units, edges, absent)
        missing_inputs.extend({**item, "candidate_id": candidate["candidate_id"]} for item in missing)
        related.extend((distance, unit, relation, candidate["candidate_id"]) for distance, unit, relation in rows)
        relevant_paths = {candidate["path"], *(unit["path"] for _, unit, _ in rows)}
        related.extend((0, unit, "imports", candidate["candidate_id"]) for unit in all_units
                       if unit["path"] in relevant_paths and unit["kind"] in {"Import", "ImportFrom"})
        if candidate["change"]["before_unit_id"]:
            related.extend((0, unit, "before_imports", candidate["candidate_id"]) for unit in old_units
                           if unit["path"] == candidate["path"] and unit["kind"] in {"Import", "ImportFrom"})
    for _, unit, relation, candidate_id in sorted(related, key=lambda item: (item[0], item[1]["path"], item[1]["start_line"])):
        include(unit, relation, candidate_id)
    return {
        "subject": deepcopy(report.get("subject", {})), "profile": deepcopy(report.get("profile", {})),
        "deterministic": {key: deepcopy(report.get(key)) for key in ("status", "summary", "findings", "coverage")},
        "sources": selected, "before_sources": before_selected, "candidates": candidates,
        "missing_inputs": missing_inputs,
        "source_scope": {"selected": sorted(paths), "omitted": sorted(set(by_path) - paths),
                         "excluded": excluded, "oversize": list(dict.fromkeys(oversize)), "truncated": [],
                         "max_source_bytes": max_source_bytes, "source_bytes": max_source_bytes - remaining,
                         "selection": "changed_ranges" if targeted else "inventory",
                         "changed_ranges": ranges, "base_ref": base_ref, "base_commit": commit,
                         "max_candidates": max_candidates, "role_priorities": list(role_priorities),
                         "eligible_by_role": dict(Counter(unit.get("role") or "<unclassified>" for unit in candidate_units)),
                         "selected_by_role": dict(Counter(unit.get("role") or "<unclassified>" for unit in candidates)),
                         "before_source_bytes": sum(len(unit["source"].encode("utf-8")) for unit in before_selected)},
    }
