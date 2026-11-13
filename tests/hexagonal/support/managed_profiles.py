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


def dev_shell_subject(directory):
    """``shell_subject`` plus the developer-settings layer, in a git checkout so the layer can find its repository."""
    script = shell_subject(directory)
    shutil.copytree(ROOT / "scripts/devconfig", directory / "scripts/devconfig",
                    ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "-q", str(directory)], check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    return script


def run_shell(script, arguments, *, cache, home=None, **environment):
    """Run ``script``. ``cache=None`` leaves PYTHONPYCACHEPREFIX unset. ``home`` isolates the run from the user's
    developer settings: HOME points there and no XDG_ or XORCHESTRATE_ variable is inherited."""
    env = {**os.environ, "PROOF_PYTHON": sys.executable}
    env.pop("PYTHONPYCACHEPREFIX", None)
    if home is not None:
        env = {name: value for name, value in env.items() if not name.startswith(("XDG_", "XORCHESTRATE_"))}
        env.update(HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    if cache is not None:
        env["PYTHONPYCACHEPREFIX"] = str(cache)
    env.update(environment)
    return subprocess.run([str(script), *arguments], env=env, capture_output=True, text=True, timeout=30)
