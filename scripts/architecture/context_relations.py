"""Bounded static references between measured units; never runtime coverage."""
from __future__ import annotations

import ast
from collections import defaultdict


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return parent + "." + node.attr if parent else None
    return None


def _symbols(unit):
    node = unit["_node"]
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [item.id for target in targets for item in ast.walk(target) if isinstance(item, ast.Name)]
    return [unit["symbol"]]


def _fixture(unit):
    return any((_name(item.func if isinstance(item, ast.Call) else item) or "").split(".")[-1] == "fixture"
               for item in getattr(unit["_node"], "decorator_list", ()))


def references(units, bindings):
    """Resolve explicit local/import references, including fixture arguments.

    Attribute suffixes alone never establish a dependency. Dynamic dispatch and
    imported modules absent from measurement remain unproved.
    """
    index = {(unit["module"], symbol): unit for unit in units for symbol in _symbols(unit)}
    modules = {unit["module"] for unit in units}
    edges, absent = defaultdict(dict), defaultdict(list)
    for unit in units:
        node, module, identifier = unit["_node"], unit["module"], unit["unit_id"]
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        imported = unit.get("_bindings", bindings.get(unit["path"], {}))
        arguments = {item.arg for item in ast.walk(node) if isinstance(item, ast.arg)}
        locals_ = {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
        names = {_name(item) for item in ast.walk(node) if isinstance(item, (ast.Name, ast.Attribute))}
        for name in sorted(names - {None}):
            root, _, tail = name.partition(".")
            target, relation = None, "local_reference"
            if root in imported and root not in arguments | locals_:
                source, symbol = imported[root]
                qualified = ".".join(part for part in (symbol, tail) if part)
                target = index.get((source, qualified))
                # `import package.module` binds `package`, whereas an alias
                # binds the full module. Resolve measured module boundaries
                # before interpreting the remaining suffix as a source symbol.
                parts = qualified.split(".")
                for split in range(len(parts) - 1, 0, -1):
                    if target is None:
                        target = index.get((source + "." + ".".join(parts[:split]),
                                            ".".join(parts[split:])))
                relation = "imported_reference"
                if target is None and (tail or symbol) and source + "." + qualified not in modules:
                    absent[identifier].append({"reason": "dependency_not_measured", "module": source,
                                               "symbol": qualified})
            elif root in arguments:
                possible = index.get((module, root)) if not tail else None
                if possible and _fixture(possible):
                    target, relation = possible, "fixture_binding"
                elif root in {"self", "cls"} and "." in unit["symbol"] and tail:
                    target = index.get((module, unit["symbol"].rsplit(".", 1)[0] + "." + tail))
            elif root not in locals_:
                target = index.get((module, name))
            if target and target["unit_id"] != identifier:
                edges[identifier][target["unit_id"]] = relation
    return edges, absent


def neighborhood(candidate, units, edges, absent):
    """Follow two caller/registration hops and three dependency/binding hops."""
    by_id = {unit["unit_id"]: unit for unit in units}
    reverse = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            reverse[target].add(source)
    selected = {candidate["unit_id"]: (0, "candidate")}
    frontier = {candidate["unit_id"]}
    for depth in range(1, 3):
        following = set()
        for target in frontier:
            for source in sorted(reverse[target]):
                if source not in selected:
                    selected[source] = (depth, "caller" if depth == 1 else "binding_caller")
                    following.add(source)
        frontier = following
    frontier = set(selected)
    for depth in range(1, 4):
        following = set()
        for source in sorted(frontier):
            for target, relation in edges.get(source, {}).items():
                if target not in selected:
                    selected[target] = (depth, relation)
                    following.add(target)
        frontier = following
    missing = [item for identifier in selected for item in absent.get(identifier, ())]
    rows = [(distance, by_id[identifier], relation) for identifier, (distance, relation) in selected.items()
            if identifier != candidate["unit_id"] and identifier in by_id]
    return sorted(rows, key=lambda item: (item[0], item[1]["path"], item[1]["start_line"])), missing
