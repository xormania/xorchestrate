"""Product configuration binding; generic test-kit policy never imports product code."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path

from .evidence import write_json


def local_capture(directory, *, project=None):
    """One private corpus per runtime; reuse the product's shared collector."""
    directory = Path(directory).resolve()
    identity = hashlib.sha256(str(directory).encode()).hexdigest()
    project = hashlib.sha256(str(Path(project or directory).resolve()).encode()).hexdigest()
    return {"adapter": "local", "options": {"runtime_root": str(directory / "telemetry"),
            "project_id": "project-" + project, "dataset_id": "proof-" + identity,
            "collector_id": "collector-" + identity, "producer_id": "xorchestrate-managed-proof"}}


def _storage_selection(options, name, default, shorthand):
    if name not in options:
        return default, {target: options[key] for key, target in shorthand.items() if key in options}, True
    value = options[name]
    if (not isinstance(value, dict) or not isinstance(value.get("adapter"), str) or not value["adapter"]
            or not isinstance(value.get("options", {}), dict) or type(value.get("enabled", True)) is not bool):
        raise ValueError(f"Malformed telemetry {name} selection")
    return value["adapter"], value.get("options", {}), value.get("enabled", True)


def capture_storage(capture):
    """Describe selected local capture storage, mirroring the product's selection rules.

    Returns None for disabled or non-local capture. Otherwise ``corpus`` and
    ``artifacts`` name the selected adapters (``artifacts`` is None when disabled),
    and ``database``/``artifact_root`` give the relative paths of the default
    ``sqlite``/``filesystem`` adapters, or None for another adapter. Malformed
    selections raise ValueError rather than being guessed.
    """
    if not isinstance(capture, dict):
        raise ValueError("Telemetry configuration must be an object")
    if capture.get("enabled", True) is False or capture.get("adapter") != "local":
        return None
    options = capture.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("Telemetry options must be an object")
    corpus, corpus_options, _ = _storage_selection(options, "corpus", "sqlite", {"database": "database"})
    artifacts, artifact_options, enabled = _storage_selection(
        options, "artifacts", "filesystem", {"artifact_root": "root"})
    database = corpus_options.get("database", "events.sqlite3") if corpus == "sqlite" else None
    artifact_root = artifact_options.get("root", "artifacts") if enabled and artifacts == "filesystem" else None
    if any(value is not None and (not isinstance(value, str) or not value) for value in (database, artifact_root)):
        raise ValueError("Telemetry storage paths must be nonempty text")
    return {"corpus": corpus, "artifacts": artifacts if enabled else None,
            "database": database, "artifact_root": artifact_root}


def isolated_config(config, base, directory, *, capture=False, project=None):
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
                options["cwd"] = str(Path(project["path"]).resolve() if project is not None
                                     else (base / options.get("cwd", ".")).resolve())
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
    selected = result.get("telemetry")
    if capture and selected is None:
        result["telemetry"] = local_capture(directory, project=base)
    elif (capture and isinstance(selected, dict) and selected.get("enabled", True)
          and selected.get("adapter") == "local"):
        options = selected.setdefault("options", {})
        if not isinstance(options, dict) or not isinstance(options.get("runtime_root", ""), str):
            raise ValueError("Telemetry options must be an object with a text runtime_root")
        if "runtime_root" not in options:
            fresh = local_capture(directory, project=base)["options"]
            selected["options"] = {**fresh, **options, **{key: fresh[key]
                                   for key in ("runtime_root", "dataset_id", "collector_id")}}
        else:
            # An explicit shared/external corpus remains an operator choice.
            # Collection separately checks that any source belongs to this run.
            options["runtime_root"] = str((base / options["runtime_root"]).expanduser().resolve())
    if capture:
        selected = result.get("telemetry", {})
        descriptor = {"schema_version": 1, "status": "external", "reason": "external_capture_policy_not_collected",
                      "content_policy": "protected-local", "source_completion": "unknown"}
        if selected.get("enabled", True) is False:
            descriptor.update(status="disabled", reason="capture_explicitly_disabled")
        elif selected.get("adapter") == "local":
            options = selected["options"]
            storage = Path(options["runtime_root"])
            selection = capture_storage(selected)
            managed = Path(os.environ.get("XORCHESTRATE_PROOF_DIRECTORY", directory)).resolve()
            owned = storage == directory / "telemetry" or (
                "XORCHESTRATE_PROOF_DIRECTORY" in os.environ and storage.is_relative_to(managed))
            if selection["database"] is None or (selection["artifacts"] is not None
                                                  and selection["artifact_root"] is None):
                # Collection preserves SQLite/file corpora (with or without
                # artifacts); another selected storage adapter remains an
                # explicit, uncollected gap.
                descriptor["reason"] = "selected_capture_storage_not_collected"
            else:
                artifacts = selection["artifact_root"]
                descriptor.update(status="configured" if owned else "external", runtime_root=str(storage),
                    database=str(storage / selection["database"]),
                    artifacts=None if artifacts is None else str(storage / artifacts),
                    **{key: options.get(key) for key in ("project_id", "dataset_id", "collector_id")})
                if owned:
                    descriptor.pop("reason", None)
                else:
                    descriptor["reason"] = "external_capture_root_not_run_owned"
        write_json(directory / "telemetry-runtime.json", descriptor)
    return result
