"""Release handoff: run a project's commands with the release its ``project.json`` pins.

The installed ``xorchestrate`` reads ``release`` from ``.xorchestrate/project.json``.
When it is not that release, it executes the same command through
``<home>/toolchains/<release>/``, building that toolchain with uv first if it
is absent:

1. ``uv python install <python>``;
2. a small ``pyproject.toml`` depending on the release's source;
3. ``uv sync`` into ``.venv`` beside it.

The toolchain is built in a temporary directory under ``toolchains/`` and
renamed into place, so a partial build is never used, and it is never modified
afterwards. The command runs as ``.venv/bin/python -m xorchestrate``, which
keeps working after the rename (console-script shebangs would not).

uv is found on ``PATH`` unless ``settings.json`` names it (``"uv"``); the
release source and interpreter are ``"toolchain_source"`` (``{release}`` is
replaced) and ``"toolchain_python"``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..composition import RELEASE
from ..model import OrchestrationError

HANDOFF_VARIABLE = "XORCHESTRATE_HANDOFF"
DEFAULT_SOURCE = "xorchestrate @ git+https://github.com/xormania/xorchestrate@{release}"
DEFAULT_PYTHON = "3.12"
UV_TIMEOUT = 1800
PYPROJECT = """[project]
name = "xorchestrate-toolchain"
version = "0"
requires-python = ">={python}"
dependencies = [{dependency}]

[tool.uv]
package = false
"""


def toolchain_python(home, release):
    return home.toolchains / release / ".venv" / "bin" / "python"


def find_uv(settings):
    configured = settings.get("uv")
    if configured is not None:
        if not isinstance(configured, str) or not Path(configured).is_absolute():
            raise OrchestrationError("settings.json uv must be an absolute path.", "invalid_configuration")
        return configured
    found = shutil.which("uv")
    if found is None:
        raise OrchestrationError("uv is required to build release toolchains; install it and retry.",
                                 "uv_unavailable")
    return found


def _quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_toolchain(home, release, *, runner=subprocess.run):
    """Build ``toolchains/<release>`` if absent; return its interpreter."""
    python = toolchain_python(home, release)
    if python.exists():
        return python
    settings = home.settings()
    uv = find_uv(settings)
    interpreter = settings.get("toolchain_python", DEFAULT_PYTHON)
    source = settings.get("toolchain_source", DEFAULT_SOURCE)
    if not isinstance(interpreter, str) or not interpreter or not isinstance(source, str) or "{release}" not in source:
        raise OrchestrationError("settings.json toolchain_python and toolchain_source ({release}) must be text.",
                                 "invalid_configuration")
    home.ensure()
    home.toolchains.mkdir(mode=0o700, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{release}.", dir=home.toolchains))
    try:
        (staging / "pyproject.toml").write_text(PYPROJECT.format(
            python=interpreter.split("-")[0] if interpreter[:1].isdigit() else "3.12",
            dependency=_quote(source.format(release=release))), encoding="utf-8")
        environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(staging / ".venv")}
        for arguments in (["python", "install", interpreter],
                          ["sync", "--project", str(staging), "--python", interpreter]):
            result = runner([uv, *arguments], cwd=staging, env=environment, capture_output=True, text=True,
                            timeout=UV_TIMEOUT, check=False)
            if result.returncode != 0:
                raise OrchestrationError(f"Building the {release} toolchain failed at `uv {arguments[0]}`.",
                                         "toolchain_failed", {"release": release, "step": " ".join(arguments[:2]),
                                                              "stderr": (result.stderr or "")[-2000:]})
        if not (staging / ".venv" / "bin" / "python").exists():
            raise OrchestrationError(f"The {release} toolchain has no interpreter after uv sync.", "toolchain_failed",
                                     {"release": release})
        try:
            os.rename(staging, home.toolchains / release)
        except OSError:
            # Another launcher finished the same release first; keep its toolchain.
            if not python.exists():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return python


def handoff(home, release, argv, *, execute=os.execve, runner=subprocess.run):
    """Execute ``argv`` with ``release``'s toolchain when this launcher is a different release.

    Returns ``None`` when this release is the pinned one. Otherwise it does not
    return (``execute`` replaces the process).
    """
    if release == RELEASE:
        return None
    if os.environ.get(HANDOFF_VARIABLE) == release:
        raise OrchestrationError(f"The {release} toolchain runs release {RELEASE}; rebuild it by removing "
                                 f"{home.toolchains / release}.", "toolchain_mismatch",
                                 {"release": release, "running": RELEASE})
    python = build_toolchain(home, release, runner=runner)
    environment = {**os.environ, HANDOFF_VARIABLE: release}
    return execute(str(python), [str(python), "-m", "xorchestrate", *argv], environment)
