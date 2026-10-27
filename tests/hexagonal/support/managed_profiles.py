"""Filesystem and process subjects for managed setup/profile contracts."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def profile_file(directory, values=None):
    directory.mkdir(parents=True, exist_ok=True)
    profile = directory / "proof profile.json"
    profile.write_text(json.dumps({"schema_version": 1, "interpreter": sys.executable, **(values or {})}))
    return profile


def shell_subject(directory):
    """Use real bootstrap code with a minimal independent observable CLI."""
    scripts = directory / "scripts"
    scripts.mkdir(parents=True)
    shutil.copytree(ROOT / "scripts/testkit", scripts / "testkit", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "scripts/proof.sh", scripts / "proof.sh")
    (scripts / "testkit/__main__.py").write_text(
        "import json,os,sys\n"
        "print(json.dumps({'argv':sys.argv[1:],'cache':sys.pycache_prefix,'cwd':os.getcwd()}))\n")
    return scripts / "proof.sh"


def run_shell(script, arguments, *, cache, **environment):
    env = {**os.environ, "PROOF_PYTHON": sys.executable, "PYTHONPYCACHEPREFIX": str(cache), **environment}
    return subprocess.run([str(script), *arguments], env=env, capture_output=True, text=True, timeout=30)
