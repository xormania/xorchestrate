"""Static views of this repository's tracked sources for the dev-tool neutrality tests."""

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def tracked(*patterns):
    out = subprocess.run(["git", "ls-files", "-z", "--", *patterns], cwd=ROOT, capture_output=True, check=True).stdout
    return [path for path in out.decode().split("\0") if path]


def imports(path):
    """Absolute imports of a tracked module, plus string constants naming a ``scripts.`` module (dynamic imports)."""
    names = set()
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("scripts."):
            names.add(node.value)
    return names


def namespace_uses(text, namespace, prefix):
    """String constants, other than docstrings, using ``namespace`` anywhere but as a folder name or in ``prefix`` keys."""
    tree = ast.parse(text)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    allowed = re.compile(rf"{prefix}[A-Z0-9_]*|(?<![A-Za-z]){namespace}(?=/|$)")
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            and namespace in allowed.sub("", node.value).lower()]
