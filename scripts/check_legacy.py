"""Exercise recovered components in their original repository layout, offline.

Some historical tests archive HEAD. A temporary Git repository avoids archiving
the surrounding Xorchestrate tree or changing the maintainer's checkout.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = [
    ("ops/testkit/tests", "test*.py"),
    ("ops/conductor/tests", "test_coordination.py"),
    ("ops/conductor/tests", "test_coordination_mcp.py"),
    ("ops/conductor/tests", "test_courier.py"),
    ("ops/conductor/tests", "test_session_adapters.py"),
    ("ops/conductor/tests", "test_machine.py"),
]


def main():
    with tempfile.TemporaryDirectory(prefix="xorchestrate-legacy-") as temporary:
        root = Path(temporary) / "source"
        shutil.copytree(ROOT / "legacy/agentscient", root, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".ruff_cache"))
        # No ambient Git configuration, hooks, remotes, tokens, or signing setup.
        environment = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL", "TMPDIR")
                       if name in os.environ}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for command in (["init", "--quiet", "--initial-branch=dev"],
                        ["config", "user.name", "Extraction tests"],
                        ["config", "user.email", "tests@example.invalid"],
                        ["add", "--all", "--force"],
                        ["commit", "--quiet", "-m", "Isolated historical test fixture"]):
            subprocess.run(["git", *command], cwd=root, env=environment, check=True)
        # Reuse the active suite census without changing the historical modules.
        runner = root / "extraction_test_runner.py"
        shutil.copyfile(ROOT / "devtools/tests/run.py", runner)
        for directory, pattern in SUITES:
            print(f"Checking {directory}/{pattern}", flush=True)
            subprocess.run([sys.executable, str(runner), "--strict", "-s", directory, "-p", pattern],
                           cwd=root, env=environment, check=True)


if __name__ == "__main__":
    main()
