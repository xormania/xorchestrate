"""Python source extraction adapter, adapted from verification boundary checks.

No inspected application module is imported or executed. Package initializer
edges and transitive paths preserve the existing checker's useful semantics.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import threading
import tokenize
from collections import OrderedDict
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


def _observe(name, is_package, tree):
    """Return the facts one module's source determines, independent of its neighbours.

    The result depends only on the module name, whether it is a package
    initializer and the parsed source, so it can be reused for identical bytes
    at the same relative path. Membership-dependent edges are kept as
    candidates and resolved by ``_link`` against the examined module set.
    """
    package = name if is_package else name.rpartition(".")[0]
    edges, candidates, dynamic, calls, literals = set(), set(), set(), set(), set()
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)
                  and isinstance(node.body[0].value.value, str)}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            literals.add((node.value, node.lineno))
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
            candidates.update((target + "." + alias.name, node.lineno, "import") for alias in node.names)
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
    return (frozenset(edges), frozenset(candidates), tuple(sorted(dynamic)),
            tuple(sorted(calls)), tuple(sorted(literals)))


def _link(name, observation, names, packages):
    """Complete one module's observation against the examined module set."""
    base, candidates, dynamic, calls, literals = observation
    edges = set(base)
    edges.update(edge for edge in candidates if edge[0] in names)
    for target, line, _ in (*edges, (name, 0, "initializer")):
        bits = target.split(".")
        edges.update((".".join(bits[:i]), line, "initializer") for i in range(1, len(bits))
                     if ".".join(bits[:i]) in packages)
    return ([{"target": target, "line": line, "kind": kind} for target, line, kind in sorted(edges)],
            [{"name": operation, "line": line} for operation, line in dynamic],
            [{"name": operation, "line": line} for operation, line in calls],
            [{"value": value, "line": line} for value, line in literals])


class ObservationCache:
    """Bounded, process-local memo of per-module observations.

    Keys are (relative path, content sha256): the relative path fixes the module
    name and package status, the digest fixes the source. Values are immutable,
    so reports built from them cannot alter later results. Unreadable or
    unresolvable sources are never stored and are re-examined every time.
    """

    def __init__(self, limit=4096):
        self.limit = limit
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key, value):
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.limit:
                self._entries.popitem(last=False)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __len__(self):
        return len(self._entries)


OBSERVATIONS = ObservationCache()


def check(root: Path, profile: dict) -> dict:
    """Read the declared scope and evaluate it against a generic role profile."""
    try:
        validate_profile(profile)
    except (ValueError, TypeError):
        return evaluate(profile, {})
    root = Path(root).resolve()
    modules, sources, packages, errors, seen = {}, {}, set(), [], set()

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
                sha256 = hashlib.sha256(raw).hexdigest()
                observed = OBSERVATIONS.get((relative, sha256))
                if observed is None:
                    encoding, _ = tokenize.detect_encoding(BytesIO(raw).readline)
                    text = raw.decode(encoding)
                modules[name] = {"path": relative, "sha256": sha256,
                                 "imports": [], "dynamic": [], "calls": [], "literals": []}
                # A parsed tree, or the cached observation of these exact bytes.
                sources[name] = observed if observed is not None else ast.parse(text, filename=relative)
                if is_package:
                    packages.add(name)
            except (OSError, UnicodeError, SyntaxError) as exc:
                error(relative, f"Cannot parse source: {exc}", getattr(exc, "lineno", None))
    for name, source in sorted(sources.items()):
        record = modules[name]
        try:
            if isinstance(source, ast.AST):
                observed = _observe(name, name in packages, source)
                OBSERVATIONS.put((record["path"], record["sha256"]), observed)
            else:
                observed = source
            (record["imports"], record["dynamic"], record["calls"],
             record["literals"]) = _link(name, observed, modules, packages)
        except (ValueError, ImportError) as exc:
            error(record["path"], f"Cannot resolve source imports: {exc}")
    return evaluate(profile, modules, stdlib_modules=sys.stdlib_module_names, extraction_findings=errors)
