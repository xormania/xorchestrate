"""Private operator profiles and explicit environment setup.

The profile selects existing test-kit options; native configuration continues to
belong to the product configuration adapter. No vendor installation, login or
global configuration is changed here.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..model import MODES, SUITES
from .evidence import write_json

FIELDS = frozenset({"schema_version", "suite", "mode", "drivers", "families", "config", "brief",
                    "timeout", "check_timeout", "run_timeout", "shutdown_grace", "output",
                    "environment", "interpreter"})
PATH_FIELDS = ("config", "brief", "output", "environment", "interpreter")


def _external(path, root, name):
    target = Path(path).expanduser().resolve()
    if target == root or root in target.parents:
        raise ValueError(f"{name} must be outside the measured candidate: {target}")
    return target


def _strings(value, name):
    if not isinstance(value, (tuple, list)) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must contain nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicate selections")
    return list(value)


def load_profile(path, *, root, overrides=None):
    """Validate one profile and resolve paths against its containing directory."""
    root, path = Path(root).resolve(), Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as stream:
        source = json.load(stream)
    if not isinstance(source, dict):
        raise ValueError("Profile must be a JSON object")
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError("Profile overrides must be an object")
    unknown = (set(source) | set(overrides)) - FIELDS
    if unknown:
        raise ValueError("Unknown profile fields: " + ", ".join(sorted(unknown)))
    values = {"schema_version": 1, "suite": "all", "mode": "protocol-fixture", "drivers": [],
              "families": [], "config": None, "brief": str(root / "docs/implementation/owner-brief.md"),
              "timeout": 180, "check_timeout": 600, "run_timeout": None, "shutdown_grace": 10,
              "output": "runs", "environment": "environment", "interpreter": None,
              **source, **overrides}
    if type(values["schema_version"]) is not int or values["schema_version"] != 1:
        raise ValueError("Unsupported profile schema_version; expected 1")
    if values["suite"] not in SUITES or values["mode"] not in MODES:
        raise ValueError("Unknown profile suite or mode")
    values["drivers"] = _strings(values["drivers"], "drivers")
    values["families"] = _strings(values["families"], "families")
    for name in ("timeout", "check_timeout", "shutdown_grace", "run_timeout"):
        number = values[name]
        if name == "run_timeout" and number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must be a positive finite number")
    for name in PATH_FIELDS:
        value = values[name]
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a nonempty path or null")
        candidate = Path(value).expanduser()
        # A venv's Python is commonly a symlink. Preserve its invocation path:
        # resolving the final symlink would silently select the base interpreter.
        values[name] = (os.path.abspath(path.parent / candidate) if name == "interpreter"
                        else str((path.parent / candidate).resolve()))
    for name in ("output", "environment"):
        if values[name] is None:
            raise ValueError(f"{name} must be configured")
        _external(values[name], root, name)
    if values["interpreter"] is None:
        values["interpreter"] = str(Path(values["environment"]) / "bin/python")
    _external(values["interpreter"], root, "interpreter")
    if values["mode"] != "native":
        if overrides.get("config") is not None or overrides.get("drivers"):
            raise ValueError("Native config and drivers require native mode")
        # Reusable native preferences can remain dormant in a protocol profile.
        values["config"], values["drivers"] = None, []
    if values["families"] and values["suite"] not in {"tests", "checks", "all"}:
        raise ValueError("families require a suite containing tests")
    values["profile_path"] = str(path)
    values["root"] = str(root)
    return values


def _interpreter_details(interpreter):
    command = [str(interpreter), "-c", "import importlib.util,json,sys; print(json.dumps({'version':list(sys.version_info[:3]),"
               "'virtual':sys.prefix != sys.base_prefix,'executable':sys.executable,"
               "'dependencies':{n:importlib.util.find_spec(n) is not None for n in ('jsonschema','pytest','ruff')}}))"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    details = json.loads(result.stdout)
    if details.get("version", []) < [3, 12]:
        raise ValueError("Python >=3.12 is required")
    return details


def setup(profile_path, *, root, install=True):
    """Create once, preserve existing settings, and install only when requested."""
    root, profile_path = Path(root).resolve(), Path(profile_path).expanduser().resolve()
    _external(profile_path, root, "profile")
    profile_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (profile_path.parent / (profile_path.name + ".setup.lock")).open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _setup(profile_path, root=root, install=install)


def _setup(profile_path, *, root, install):
    created = False
    if not profile_path.exists():
        defaults = {"schema_version": 1, "mode": "protocol-fixture", "suite": "all",
                    "drivers": ["codex"], "families": [], "config": "native.json",
                    "brief": str(root / "docs/implementation/owner-brief.md"), "timeout": 180,
                    "check_timeout": 600, "run_timeout": None, "shutdown_grace": 10,
                    "output": "runs", "environment": "environment"}
        # Exclusive creation preserves settings even if two setup commands race.
        descriptor = os.open(profile_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(defaults, stream, indent=2)
            stream.write("\n")
        created = True
    profile = load_profile(profile_path, root=root)
    workspace = _external(profile_path.parent / "workspace", root, "workspace")
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    native_config = profile_path.parent / "native.json"
    if not native_config.exists():
        template = json.loads((root / "scripts/verification/native-config.example.json").read_text(encoding="utf-8"))
        for specification in template.get("sessions", {}).values():
            specification.setdefault("options", {})["cwd"] = str(workspace)
        write_json(native_config, template)
    environment = Path(profile["environment"])
    interpreter = Path(profile["interpreter"])
    receipts = []
    if not interpreter.exists():
        if interpreter != environment / "bin/python":
            raise ValueError("Configured interpreter is unavailable; correct the profile or create its environment")
        if environment.exists() and any(environment.iterdir()):
            raise ValueError("Environment exists without its Python; refusing to overwrite it")
        command = [sys.executable, "-m", "venv", str(environment)]
        subprocess.run(command, check=True, timeout=120)
        receipts.append(command)
    details = _interpreter_details(interpreter)
    if install:
        if not details["virtual"]:
            raise ValueError("Refusing dependency installation into a global interpreter; configure a virtual environment")
        command = [str(interpreter), "-m", "pip", "install", "-e", str(root) + "[test]",
                   "-r", str(root / ".github/requirements-ci.txt")]
        subprocess.run(command, check=True, timeout=600)
        receipts.append(command)
        details = _interpreter_details(interpreter)
    return {"status": "passed", "profile": str(profile_path), "created": created,
            "interpreter": str(interpreter), "python": details, "commands": receipts,
            "workspace": str(workspace), "native_config": str(native_config), "native_prerequisites": [
                "Install/authenticate the native clients separately; setup never changes authentication.",
                "For native mode, configure the existing native application JSON and select acceptance drivers.",
                "Native terminal checks require tmux, PTYs, Unix sockets and Linux/WSL /proc."]}


def doctor(profile_path, *, root, overrides=None):
    """Read-only environment observations; composition owns check readiness."""
    profile = load_profile(profile_path, root=root, overrides=overrides)
    observations = []
    interpreter = profile["interpreter"]
    try:
        details = _interpreter_details(interpreter)
        observations.append({"name": "interpreter", "passed": True, "path": interpreter, **details})
        required_dependencies = {"ruff"} if profile["suite"] == "static" else {"jsonschema"}
        if profile["suite"] in {"all", "checks"}:
            required_dependencies.add("ruff")
        if profile["suite"] in {"all", "checks", "tests"}:
            required_dependencies.add("pytest")
        for name, available in details["dependencies"].items():
            observations.append({"name": name, "passed": available, "required": name in required_dependencies,
                                 "observation_interpreter": interpreter})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        observations.append({"name": "interpreter", "passed": False, "path": interpreter,
                             "reason": str(error)})
    if profile["suite"] in {"acceptance", "all"}:
        observations.append({"name": "tmux", "passed": shutil.which("tmux") is not None,
                             "path": shutil.which("tmux")})
    return {"status": "passed" if all(item["passed"] for item in observations if item.get("required", True)) else "setup_error",
            "profile": profile, "readiness": observations, "authentication": "not probed"}


def bootstrap(argv=None, *, root=None):
    """Select a configured interpreter before importing the application CLI."""
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(root or Path(__file__).resolve().parents[3]).resolve()
    if sys.version_info < (3, 12):
        raise ValueError("Python >=3.12 is required; set PROOF_PYTHON to a supported interpreter")
    prefix = os.environ.get("PYTHONPYCACHEPREFIX")
    if not prefix:
        raise ValueError("An external PYTHONPYCACHEPREFIX must be set before Python starts")
    _external(prefix, root, "bytecode cache")
    profile_path, source_run = None, None
    for index, argument in enumerate(argv):
        if argument == "--profile" and index + 1 < len(argv):
            profile_path = argv[index + 1]
        elif argument.startswith("--profile="):
            profile_path = argument.split("=", 1)[1]
        elif argument == "--from" and index + 1 < len(argv):
            source_run = argv[index + 1]
        elif argument.startswith("--from="):
            source_run = argument.split("=", 1)[1]
    action = argv[0] if argv else None
    interpreter = None
    if profile_path is not None and action != "setup":
        profile = load_profile(profile_path, root=root)
        interpreter = profile["interpreter"]
        if action == "doctor" and not Path(interpreter).is_file():
            interpreter = None
    elif action in {"status", "watch", "attach", "wait", "stop", "close", "collect"}:
        source_run = next((argument for argument in argv[1:] if not argument.startswith("-")), None)
    if interpreter is None and source_run is not None:
        saved = json.loads((Path(source_run).expanduser().resolve() / "plan.json").read_text(encoding="utf-8"))
        interpreter = saved.get("interpreter") if isinstance(saved, dict) else None
        if not isinstance(interpreter, str) or not interpreter:
            raise ValueError("Saved run plan does not identify its configured interpreter")
        _external(interpreter, root, "saved interpreter")
    if interpreter is not None:
        if not Path(interpreter).is_file():
            raise ValueError("Profile environment is unavailable; run scripts/proof.sh setup --profile PATH")
        _interpreter_details(interpreter)
        os.execv(interpreter, [interpreter, "-m", "scripts.testkit", *argv])
    os.execv(sys.executable, [sys.executable, "-m", "scripts.testkit", *argv])


if __name__ == "__main__":
    try:
        bootstrap()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"proof bootstrap: {error}", file=sys.stderr)
        raise SystemExit(2) from None
