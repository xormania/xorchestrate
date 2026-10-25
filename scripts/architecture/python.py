"""Python source extraction adapter, adapted from verification boundary checks.

No inspected application module is imported or executed. Package initializer
edges and transitive paths preserve the existing checker's useful semantics.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import tokenize
from io import BytesIO
from pathlib import Path

from .core import evaluate, validate_profile

DYNAMIC_OPERATIONS = {
    "__import__", "__builtins__", "eval", "exec", "compile", "import_module",
    "exec_module", "load_module", "find_spec", "importorskip",
}
DYNAMIC_MODULES = {"builtins", "importlib", "pytest"}


def _qualified_name(node, aliases):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    return ".".join([aliases.get(node.id, node.id), *parts]) if isinstance(node, ast.Name) else None


def _extract(name, tree, names, packages):
    package = name if name in packages else name.rpartition(".")[0]
    edges, dynamic, calls = set(), set(), set()
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            target = importlib.util.resolve_name(target, package) if node.level else target
            for alias in node.names:
                aliases[alias.asname or alias.name] = target + "." + alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.update((alias.name, node.lineno, "import") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            target = importlib.util.resolve_name(target, package) if node.level else target
            edges.add((target, node.lineno, "import"))
            edges.update((target + "." + alias.name, node.lineno, "import") for alias in node.names
                         if target + "." + alias.name in names)
            for alias in node.names:
                if alias.name in DYNAMIC_OPERATIONS and target.split(".")[0] in DYNAMIC_MODULES:
                    dynamic.add((target + "." + alias.name, node.lineno))
        operation = _qualified_name(node, aliases)
        if (isinstance(node, ast.Name) and node.id in DYNAMIC_OPERATIONS
                or operation and operation.split(".")[0] in DYNAMIC_MODULES
                and operation.rsplit(".", 1)[-1] in DYNAMIC_OPERATIONS):
            dynamic.add((operation, node.lineno))
        if isinstance(node, ast.Call):
            called = _qualified_name(node.func, aliases)
            if called:
                calls.add((called, node.lineno))
    for target, line, _ in (*edges, (name, 0, "initializer")):
        bits = target.split(".")
        edges.update((".".join(bits[:i]), line, "initializer") for i in range(1, len(bits))
                     if ".".join(bits[:i]) in packages)
    return ([{"target": target, "line": line, "kind": kind} for target, line, kind in sorted(edges)],
            [{"name": operation, "line": line} for operation, line in sorted(dynamic)],
            [{"name": operation, "line": line} for operation, line in sorted(calls)])


def check(root: Path, profile: dict) -> dict:
    """Read the declared scope and evaluate it against a generic role profile."""
    try:
        validate_profile(profile)
    except (ValueError, TypeError):
        return evaluate(profile, {})
    root = Path(root).resolve()
    modules, trees, packages, errors, seen = {}, {}, set(), [], set()

    def error(path, message, line=None):
        item = {"rule_id": "source.readable", "kind": "extraction", "path": path, "message": message}
        if line is not None:
            item["line"] = line
        errors.append(item)

    for source_root in profile["source_roots"]:
        directory = root / source_root
        if not directory.is_dir():
            error(source_root, "Declared source root is missing or is not a directory.")
            continue
        paths = set(directory.rglob("*.py"))
        if not paths:
            error(source_root, "Declared source root contains no Python modules.")
        # A nested package also executes initializers above the selected root.
        # Include their source and require an explicit role for those modules.
        for ancestor in directory.parents:
            if ancestor == root:
                break
            initializer = ancestor / "__init__.py"
            if initializer.is_file():
                paths.add(initializer)
        for path in sorted(paths):
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            if not path.resolve().is_relative_to(root):
                error(relative, "Source path resolves outside the selected repository.")
                continue
            parts = path.relative_to(root).with_suffix("").parts
            is_package = parts[-1] == "__init__"
            name = ".".join(parts[:-1] if is_package else parts)
            if name in modules:
                error(relative, f"Ambiguous Python module {name!r}: both a file and package exist.")
                continue
            try:
                raw = path.read_bytes()
                encoding, _ = tokenize.detect_encoding(BytesIO(raw).readline)
                source = raw.decode(encoding)
                modules[name] = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "imports": [], "dynamic": [], "calls": []}
                trees[name] = ast.parse(source, filename=relative)
                if is_package:
                    packages.add(name)
            except (OSError, UnicodeError, SyntaxError) as exc:
                error(relative, f"Cannot parse source: {exc}", getattr(exc, "lineno", None))
    for name, tree in sorted(trees.items()):
        try:
            modules[name]["imports"], modules[name]["dynamic"], modules[name]["calls"] = _extract(name, tree, modules, packages)
        except (ValueError, ImportError) as exc:
            error(modules[name]["path"], f"Cannot resolve source imports: {exc}")
    return evaluate(profile, modules, stdlib_modules=sys.stdlib_module_names, extraction_findings=errors)
