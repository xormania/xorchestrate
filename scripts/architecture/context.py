"""Select complete, measured Python units for bounded architecture diagnosis.

Only the deterministic inventory is eligible. AST/import/call relationships are
selection evidence, not a claim that a call executes or a test covers behavior.
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import tokenize
from copy import deepcopy
from io import BytesIO
from pathlib import Path


def changed_lines(root: Path, base_ref: str) -> list[dict]:
    """Return changed new-side line ranges against a commit, including worktree edits."""
    if not isinstance(base_ref, str) or not base_ref or base_ref.startswith("-"):
        raise ValueError("base_ref must be a Git reference.")
    command = ["git", "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--no-renames"]

    def diff(arguments):
        result = subprocess.run([*command, *arguments], cwd=root, capture_output=True,
                                text=True, timeout=10, check=False)
        if result.returncode:
            raise ValueError("The diagnostic base reference is unavailable.")
        return result.stdout

    # Patch headers quote unusual names and append a tab to names with spaces.
    # NUL-delimited names preserve the actual paths. Per-path patches then need
    # only hunk coordinates; their display headers are never interpreted.
    paths = diff(["--name-only", "-z", "--diff-filter=ACMRTUXB", base_ref, "--"]).split("\0")
    ranges = []
    for path in filter(None, paths):
        for line in diff(["--unified=0", base_ref, "--", path]).splitlines():
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                start, count = int(match[1]), int(match[2] or 1)
                # Deletions identify the surviving boundary, not deleted source.
                ranges.append({"path": path, "start_line": max(1, start),
                               "end_line": max(1, start + count - 1)})
    return ranges


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


def _call_names(unit):
    names = set()
    for node in ast.walk(unit["_node"]):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _imports(tree, module, is_package):
    """Bind imported local names to module/symbol without importing inspected code."""
    import importlib.util

    bindings = {}
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]] = (alias.name, None)
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
                  source_paths=(), changed_ranges=(), base_ref=None, max_candidates=1) -> dict:
    """Build a source-bound packet; budgets omit whole units instead of slicing them."""
    for name, value, maximum in (("max_source_bytes", max_source_bytes, 1000000),
                                 ("max_files", max_files, 1000), ("max_candidates", max_candidates, 100)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be a positive integer no greater than {maximum}.")
    if not isinstance(source_paths, (list, tuple)) or any(not isinstance(path, str) or not path for path in source_paths):
        raise ValueError("source_paths must select measured paths.")
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
        all_units.extend(_units(item, text, tree))
        module = item.get("module") or path.removesuffix(".py").replace("/", ".")
        bindings[path] = _imports(tree, module, Path(path).name == "__init__.py")

    ranges = [*changed_ranges, *(changed_lines(root, base_ref) if base_ref is not None else [])]
    excluded, candidate_units = [], []
    targeted = base_ref is not None or bool(changed_ranges)
    if targeted:
        for span in ranges:
            if span["path"] not in by_path:
                excluded.append({**span, "reason": "outside_measured_inventory"})
                continue
            matches = [unit for unit in all_units if unit["path"] == span["path"] and _overlaps(unit, span)]
            # Prefer a method only when it contains the whole changed span;
            # a simultaneous class-header change must retain the complete class.
            containers = [unit for unit in matches if unit["start_line"] <= span["start_line"]
                          and unit["end_line"] >= span["end_line"]]
            matches = containers or matches
            leaves = [unit for unit in matches if not any(
                other is not unit and other["start_line"] >= unit["start_line"]
                and other["end_line"] <= unit["end_line"] for other in matches)]
            candidate_units.extend(leaves)
            if not leaves:
                excluded.append({**span, "reason": "no_source_unit"})
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
    selected, candidates, used, paths, oversize = [], [], set(), set(), []
    remaining = max_source_bytes

    def include(unit, relation):
        nonlocal remaining
        if unit["unit_id"] in used:
            return True
        size = len(unit["source"].encode("utf-8"))
        reason = "file_limit" if unit["path"] not in paths and len(paths) >= max_files else (
            "source_budget" if size > remaining else None)
        if reason:
            excluded.append({"path": unit["path"], "unit_id": unit["unit_id"],
                             "start_line": unit["start_line"], "end_line": unit["end_line"],
                             "reason": reason, "relation": relation})
            if size > max_source_bytes:
                oversize.append(unit["unit_id"])
            return False
        used.add(unit["unit_id"])
        paths.add(unit["path"])
        remaining -= size
        selected.append(_public(unit, relation))
        return True

    for unit in candidate_units:
        if len(candidates) >= max_candidates:
            if targeted:
                excluded.append({"path": unit["path"], "unit_id": unit["unit_id"],
                                 "start_line": unit["start_line"], "end_line": unit["end_line"],
                                 "reason": "candidate_limit"})
            continue
        if include(unit, "candidate"):
            role = unit.get("role")
            declaration = report.get("profile", {}).get("roles", {}).get(role, {})
            candidates.append({key: unit[key] for key in (
                "unit_id", "path", "module", "role", "symbol", "start_line", "end_line", "sha256")})
            candidates[-1].update(candidate_id=unit["unit_id"], contract={
                "profile_id": report.get("profile", {}).get("id"), "role": role,
                "declaration": deepcopy(declaration)})

    # Changed candidates get the source budget first. Then supply actual imports,
    # imported contracts, callers and test callers; unrelated file prefixes cannot
    # crowd a changed function out. These relationships are static, not coverage.
    related = []
    for candidate in candidates:
        path, module, symbol = candidate["path"], candidate["module"], candidate["symbol"].split(".")[-1]
        own = next(unit for unit in all_units if unit["unit_id"] == candidate["unit_id"])
        referenced = {node.id for node in ast.walk(own["_node"]) if isinstance(node, ast.Name)}
        imported = bindings[path]
        targets = {value for name, value in imported.items() if name in referenced}
        edges = {edge["target"] for edge in report.get("relationships", []) if edge.get("source") == module}
        for unit in all_units:
            if unit["path"] == path and unit["kind"] in {"Import", "ImportFrom"}:
                related.append((0, unit, "imports"))
            elif any(unit["module"] == target and (name is None or unit["symbol"] == name)
                     for target, name in targets):
                related.append((1, unit, "imported_contract"))
            elif unit["module"] in edges and unit["kind"] == "ClassDef":
                related.append((2, unit, "imported_definition"))
            local = unit["path"] == path
            aliases = {alias for alias, (target, name) in bindings[unit["path"]].items()
                       if target == module and name == symbol}
            imported_module = any(target == module and name is None or target + "." + str(name) == module
                                  for target, name in bindings[unit["path"]].values())
            if not (local or aliases or imported_module):
                continue
            calls = _call_names(unit)
            if (local and symbol in calls) or calls & aliases or imported_module and symbol in calls:
                is_test = unit["symbol"].split(".")[-1].startswith("test_")
                related.append((3 if is_test else 2, unit, "test_caller" if is_test else "caller"))
    for _, unit, relation in sorted(related, key=lambda item: (item[0], item[1]["path"], item[1]["start_line"])):
        include(unit, relation)
    return {
        "subject": deepcopy(report.get("subject", {})), "profile": deepcopy(report.get("profile", {})),
        "deterministic": {key: deepcopy(report.get(key)) for key in ("status", "summary", "findings", "coverage")},
        "sources": selected, "candidates": candidates,
        "source_scope": {"selected": sorted(paths), "omitted": sorted(set(by_path) - paths),
                         "excluded": excluded, "oversize": list(dict.fromkeys(oversize)), "truncated": [],
                         "max_source_bytes": max_source_bytes, "source_bytes": max_source_bytes - remaining,
                         "selection": "changed_ranges" if targeted else "inventory",
                         "changed_ranges": ranges, "base_ref": base_ref, "max_candidates": max_candidates},
    }
