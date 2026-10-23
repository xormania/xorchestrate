"""Dependency direction checks for the independently usable application."""

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "xorchestrate"


def imports(path):
    """Resolve local imports without importing modules or starting adapters."""
    relative = path.relative_to(ROOT).with_suffix("")
    module = ".".join(relative.parts)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source = "." * node.level + (node.module or "")
            resolved = importlib.util.resolve_name(source, package) if node.level else source
            yield resolved
            if node.module is None:
                yield from (resolved + "." + alias.name for alias in node.names)


def source_for(module):
    path = ROOT.joinpath(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


class DependencyBoundaryTests(unittest.TestCase):
    def test_application_and_its_helpers_do_not_depend_on_infrastructure(self):
        # Traverse dependencies, so moving an infrastructure import to a helper
        # cannot silently reverse the application boundary.
        forbidden_modules = {
            "devtools", "sqlite3", "subprocess", "socket", "ssl", "http",
            "urllib", "pathlib", "os", "multiprocessing", "importlib",
        }
        forbidden_local = {
            "xorchestrate.adapters", "xorchestrate.composition", "xorchestrate.cli",
            "xorchestrate.mcp", "xorchestrate.__main__",
        }
        pending = ["xorchestrate.application", "xorchestrate.model", "xorchestrate.ports"]
        visited = set()
        violations = []
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            path = source_for(name)
            self.assertIsNotNone(path, f"Missing application module: {name}")
            for dependency in imports(path):
                if dependency.split(".")[0] in forbidden_modules or any(
                    dependency == prefix or dependency.startswith(prefix + ".")
                    for prefix in forbidden_local
                ):
                    violations.append(f"{name} imports {dependency}")
                elif dependency.startswith("xorchestrate") and source_for(dependency):
                    pending.append(dependency)
            tree = ast.parse(path.read_text())
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
                for node in ast.walk(tree)
            ):
                violations.append(f"{name} imports dynamically")
        self.assertEqual([], violations, "\n".join(violations))

    def test_active_package_does_not_import_the_historical_runtime(self):
        violations = []
        for path in PACKAGE.rglob("*.py"):
            for dependency in imports(path):
                if dependency == "devtools" or dependency.startswith("devtools."):
                    violations.append(f"{path.relative_to(ROOT)} imports {dependency}")
        self.assertEqual([], violations, "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
