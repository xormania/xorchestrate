"""Product configuration binding; generic test-kit policy never imports product code."""

import os
from copy import deepcopy
from pathlib import Path


def isolated_config(config, base, directory):
    """Preserve composition's cwd semantics when relocating a private run config.

    Custom factories own their option interpretation. Only enabled built-ins
    receive resolved cwd defaults, exactly as in product composition. Caller
    configuration is never mutated and each run gets its own SQLite store.
    Import lazily so a checker may select a different candidate checkout first.
    """
    result = deepcopy(config)
    base, directory = Path(base).resolve(), Path(directory).resolve()
    cache_environment = {name: os.environ[name] for name in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE")
                         if name in os.environ}
    builtin_candidates = any(spec.get("enabled", True) and ":" not in spec.get("adapter", "")
                             for kind in ("providers", "sessions") for spec in result.get(kind, {}).values())
    if builtin_candidates:
        from xorchestrate.composition import BUILTINS
    else:
        BUILTINS = {"providers": {}, "sessions": {}}
    for kind in ("providers", "sessions"):
        for spec in result.get(kind, {}).values():
            if spec.get("enabled", True) and spec.get("adapter") in BUILTINS[kind]:
                options = spec.setdefault("options", {})
                options["cwd"] = str((base / options.get("cwd", ".")).resolve())
                if (kind == "sessions" and spec.get("adapter") == "claude"
                        and options.get("terminal", "inherit") == "inherit"
                        and os.environ.get("XORCHESTRATE_PROOF_DIRECTORY")):
                    managed = Path(os.environ["XORCHESTRATE_PROOF_DIRECTORY"]).resolve()
                    if not directory.is_relative_to(managed):
                        raise ValueError("Managed native terminal evidence must belong to its run")
                    # Explicit run-local launcher configuration, never a vendor
                    # environment override or a second native session launch.
                    options["terminal"] = {"adapter": "pty", "directory": str(directory / "native-terminals"),
                                           "purpose": "Claude native prompt"}
                if "command" in options and cache_environment:
                    # Child tools keep the validation launcher's bytecode policy
                    # without inheriting unrelated operator credentials.
                    options["env"] = {**options.get("env", {}), **cache_environment}
    result["store"] = {"adapter": "sqlite", "options": {"path": str(directory / "state.sqlite3")}}
    return result
