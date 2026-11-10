"""Assemble a runtime from an explicit, trusted local configuration snapshot.

Only this module selects infrastructure. Installed ``module:factory`` extensions
receive their options as a dict; incoming tool arguments never choose factories.
"""
import importlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

from . import __version__
from .application import Application
from .model import OrchestrationError, canonical_digest, unique_keys
from .ports import ApplicationToolSessionDriver, SessionDriver
from .worktrees import bound_worktree, session_worktree

BUILTINS = {
    "telemetry": {"local": "xorchestrate.adapters.telemetry:LocalTelemetry"},
    # Storage selected inside the local telemetry adapter's ``corpus``/``artifacts`` options.
    "telemetry_corpus": {"sqlite": "xorchestrate.adapters.telemetry_collector.sqlite:corpus"},
    "telemetry_artifacts": {"filesystem": "xorchestrate.adapters.telemetry_collector.artifacts:filesystem"},
    "state_engine": {"declared": "xorchestrate.adapters.state:ConfiguredStateEngine"},
    "store": {"memory": "xorchestrate.adapters.storage:MemoryStore", "sqlite": "xorchestrate.adapters.storage:SQLiteStore"},
    "providers": {"mcp": "xorchestrate.adapters.capabilities:StdioMCPProvider", "cli": "xorchestrate.adapters.capabilities:CLIProvider",
        "pr": "xorchestrate.adapters.pr:PullRequestProvider"},
    "sessions": {
        "jsonrpc": "xorchestrate.adapters.process_sessions:JSONRPCSessionDriver",
        "codex": "xorchestrate.adapters.codex:CodexSessionDriver",
        "acp": "xorchestrate.adapters.acp:ACPSessionDriver",
        "grok": "xorchestrate.adapters.grok:GrokSessionDriver",
        "claude": "xorchestrate.adapters.claude:ClaudeSessionDriver",
        "bridge": "xorchestrate.adapters.bridge:BridgeSessionDriver",
    },
    "acceptance": {"required-checks": "xorchestrate.adapters.acceptance:RequiredChecksAcceptance"},
    "session_selection": {"latest": "xorchestrate.adapters.delivery:LatestSessionSelection"},
    "delivery_scheduler": {"manual": "xorchestrate.adapters.delivery:DeliverySchedule"},
    "retry_policy": {"definite-failure": "xorchestrate.adapters.delivery:DefiniteFailureRetry"},
    "receipt_policy": {"complete-exchange": "xorchestrate.adapters.delivery:CompleteExchangeReceipt"},
    "guardrail": {"rules": "xorchestrate.adapters.policies:RuleGuardrail"},
    "routing": {"configurable": "xorchestrate.adapters.policies:ConfigurableRouting"},
    "evaluators": {"tool": "xorchestrate.adapters.evaluation:ToolEvaluator", "typesafe": "xorchestrate.adapters.evaluation:TypeSafeEvaluator"},
    # Operator ports, selected from the user's home by the operator layer, never by project configuration.
    "companion": {"git-directory": "xorchestrate.adapters.companion_git:GitDirectoryCompanion"},
    "version_control": {"git": "xorchestrate.adapters.git_cli:GitCLI"},
    "repository_hosting": {"github": "xorchestrate.adapters.github:GitHubHosting"},
}


# Adapter kinds that are selected inside another adapter's options, not as sections.
NESTED = frozenset({"telemetry_corpus", "telemetry_artifacts"})
# Adapter kinds the operator layer selects from the user's home (settings.json,
# instance.json); a project configuration cannot select them.
OPERATOR = frozenset({"companion", "version_control", "repository_hosting"})


@dataclass(frozen=True)
class TelemetryDefaults:
    """Collector identity a caller supplies when the configuration has no telemetry section.

    With these defaults, an absent ``telemetry`` section constructs the local
    collector; ``{"enabled": false}`` still disables it, and an explicit section
    is used as written.
    """

    runtime_root: str | Path
    project_id: str
    collector_id: str
    dataset_id: str = "default"

    def specification(self):
        """Return the local telemetry section, refusing defaults an explicit section would refuse."""
        from .adapters.telemetry_collector.config import text
        from .adapters.telemetry_collector.domain import TelemetryError

        root = self.runtime_root
        if not isinstance(root, (str, Path)) or not str(root) or not Path(root).is_absolute():
            raise OrchestrationError("Telemetry defaults require an absolute runtime root.", "configuration_error")
        try:
            for name in ("project_id", "collector_id", "dataset_id"):
                text(getattr(self, name), name)
        except TelemetryError as error:
            raise OrchestrationError(str(error), "configuration_error") from error
        return {"adapter": "local", "options": {
            "runtime_root": str(root), "project_id": self.project_id,
            "dataset_id": self.dataset_id, "collector_id": self.collector_id}}


REPO_PLACEHOLDER = "${repo}"
"""Left unresolved by rendering; each call resolves it to its own worktree root."""


@dataclass(frozen=True)
class ProjectContext:
    """The set-up project a runtime is built for, supplied by the entrypoint.

    Composition looks nothing up itself: the operator layer finds the project,
    checks its approval and renders its configuration, then passes this
    context. It supplies the telemetry defaults (collection is always on for a
    project unless the configuration says ``enabled: false``), the data
    directory that relative paths resolve against, and the default worktree of
    calls that do not bring their own.
    """

    name: str
    instance_id: str
    data_dir: str | Path
    worktree: dict | None = None
    dataset_id: str = "default"

    def telemetry_defaults(self):
        return TelemetryDefaults(Path(self.data_dir) / "telemetry", self.name, self.instance_id, self.dataset_id)


def factory_for(kind, adapter):
    """Resolve a built-in alias or a trusted installed ``module:factory`` for one adapter kind."""
    target = BUILTINS[kind].get(adapter, adapter)
    if not isinstance(target, str) or ":" not in target:
        raise OrchestrationError(f"Unknown {kind} adapter: {adapter}", "configuration_error")
    module, name = target.split(":", 1)
    return getattr(importlib.import_module(module), name)


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def read_json(text):
    try:
        value = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=unique_keys("Duplicate JSON key: {key}"))
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise OrchestrationError("Expected valid JSON with unique keys and finite numbers.", "invalid_json") from exc


def load_config(path=None):
    if path is None:
        return {}, Path.cwd()
    source = Path(path).expanduser().resolve()
    try:
        config = read_json(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OrchestrationError("Cannot read the selected configuration.", "configuration_error") from exc
    if not isinstance(config, dict):
        raise OrchestrationError("Configuration must be a JSON object.", "configuration_error")
    return config, source.parent


class Runtime:
    def __init__(self, application, resources, warnings=()):
        self.application = application
        self._resources = resources
        # Non-fatal configuration findings, such as a missing format version.
        self.warnings = tuple(warnings)

    def close(self):
        errors = []
        while self._resources:
            resource = self._resources.pop()
            try:
                if self.application is not None and resource is self.application.store:
                    self.application.runtime_closed()
                close = getattr(resource, "close", None)
                if close:
                    close()
            except Exception as exc:
                errors.append(type(exc).__name__)
        if errors:
            raise OrchestrationError("One or more adapters failed to close.", "cleanup_failed", {"errors": errors})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise


SUPPORTED_CONFIG_VERSIONS = frozenset({1})
"""Configuration format versions this release reads. Absent ``version`` means 1."""

RELEASE = f"v{__version__}"
# Nested storage kinds are selected inside telemetry options, never as sections.
_SECTIONS = (frozenset(BUILTINS) - NESTED - OPERATOR) | {"assessment", "session_fallbacks", "version"}
_ADAPTER_FIELDS = frozenset({"adapter", "options", "enabled"})
# Singleton adapters in construction order, with their defaults.
_SINGLETONS = {
    "state_engine": {"adapter": "declared"},
    "store": {"adapter": "sqlite"},
    "guardrail": {"adapter": "rules"},
    "routing": {"adapter": "configurable"},
    "acceptance": {"adapter": "required-checks"},
    "session_selection": {"adapter": "latest"},
    "delivery_scheduler": {"adapter": "manual"},
    "retry_policy": {"adapter": "definite-failure"},
    "receipt_policy": {"adapter": "complete-exchange"},
}
_COLLECTIONS = ("providers", "sessions", "evaluators")


@dataclass(frozen=True)
class AdapterSelection:
    """One enabled adapter: its factory target and the options it will receive."""

    kind: str
    adapter: str
    target: str
    options: dict


@dataclass(frozen=True)
class ResolvedConfig:
    """A structurally valid configuration with paths resolved and nothing constructed."""

    version: int
    base_dir: Path
    config: dict
    adapters: dict
    collections: dict
    telemetry: AdapterSelection | None
    telemetry_unavailable: bool
    provider_configurations: dict
    evaluator_configurations: dict
    warnings: tuple = ()
    telemetry_cause: dict | None = None
    """Bounded cause when the telemetry section is structurally invalid."""


def config_version(config, supported=SUPPORTED_CONFIG_VERSIONS):
    """Return the configuration format version and any warning about it.

    Configuration is never rewritten here; an unreadable version is refused.
    """
    supported = sorted(supported)
    warnings = ()
    if "version" not in config:
        version = 1
        warnings = ({
            "code": "configuration_version_missing",
            "message": "Configuration has no format version; it is read as version 1. "
                       "Add \"version\": 1 because a later release will require it.",
            "assumed_version": version,
        },)
    else:
        version = config["version"]
        if type(version) is not int or version < 1:
            raise OrchestrationError("Configuration version must be a positive integer.", "configuration_error")
    details = {"version": version, "supported": supported, "release": RELEASE}
    if version in supported:
        return version, warnings
    if version < supported[0]:
        raise OrchestrationError(
            f"Configuration format version {version} is older than this release reads; migrate the "
            f"configuration to version {supported[0]}. Configuration is never rewritten on load.",
            "configuration_migration_required", {**details, "target": supported[0]},
        )
    raise OrchestrationError(
        f"Configuration format version {version} is not supported by release {RELEASE}; "
        "use a release that reads it.",
        "configuration_version_unsupported", details,
    )


def _resolve_path(base_dir, value, kind, adapter, strict=True):
    if not isinstance(value, str):
        raise OrchestrationError(f"{kind} adapter {adapter} paths must be strings.", "configuration_error")
    if value.startswith(REPO_PLACEHOLDER):
        # Resolved per call to the caller's worktree root (see adapters/process.py).
        return value
    try:
        return str((base_dir / value).resolve()) if strict else str(base_dir / value)
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise OrchestrationError(f"Invalid {kind} adapter {adapter} path.", "configuration_error") from exc


def _select(kind, specification, base_dir):
    if not isinstance(specification, dict) or not isinstance(specification.get("adapter"), str):
        raise OrchestrationError(f"{kind} requires an adapter and options object.", "configuration_error")
    if specification.keys() - _ADAPTER_FIELDS:
        raise OrchestrationError(f"Unknown {kind} adapter configuration fields.", "configuration_error")
    if "enabled" in specification and type(specification["enabled"]) is not bool:
        raise OrchestrationError(f"{kind} enabled must be a boolean.", "configuration_error")
    options = specification.get("options", {})
    if not isinstance(options, dict):
        raise OrchestrationError(f"{kind} options must be an object.", "configuration_error")
    options = deepcopy(options)
    adapter = specification["adapter"]
    target = BUILTINS[kind].get(adapter, adapter)
    if ":" not in target:
        raise OrchestrationError(f"Unknown {kind} adapter: {adapter}", "configuration_error")
    if kind in {"providers", "sessions"} and adapter in BUILTINS[kind] and adapter != "pr":
        options["cwd"] = _resolve_path(base_dir, options.get("cwd", "."), kind, adapter)
    if kind == "store" and adapter == "sqlite":
        path = options.get("path", ".xorchestrate/state.sqlite3")
        options["path"] = path if path == ":memory:" else _resolve_path(base_dir, path, kind, adapter)
    if kind == "telemetry" and adapter == "local" and isinstance(options.get("runtime_root"), str):
        options["runtime_root"] = _resolve_path(base_dir, options["runtime_root"], kind, adapter, strict=False)
    return AdapterSelection(kind, adapter, target, options)


def validate_config(config=None, base_dir=None, *, telemetry_defaults=None):
    """Check a configuration's structure and resolve its paths.

    This is the structural half of ``build``: format version, section names,
    adapter shapes, option containers and path resolution. It imports no
    factory module, constructs no adapter, launches no process and writes
    nothing, so operator tooling can check a configuration safely. Adapter
    option contents remain each factory's responsibility at construction, and
    application-owned settings (``assessment``, ``session_fallbacks``) are
    validated by the application. The local telemetry adapter's nested
    ``corpus``/``artifacts`` selections are its option contents.

    ``telemetry_defaults`` supplies the local collector when the configuration
    has no telemetry section (absent or ``null``); invalid defaults are refused.
    """
    if telemetry_defaults is not None and not isinstance(telemetry_defaults, TelemetryDefaults):
        raise OrchestrationError("Telemetry defaults must be a TelemetryDefaults value.", "configuration_error")
    try:
        config = json.loads(json.dumps({} if config is None else config, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("Configuration must contain JSON values.", "configuration_error") from exc
    if not isinstance(config, dict):
        raise OrchestrationError("Configuration must be an object.", "configuration_error")
    version, warnings = config_version(config)
    unknown = set(config) - _SECTIONS
    if unknown:
        raise OrchestrationError("Unknown configuration sections.", "configuration_error", {"sections": sorted(unknown)})
    base_dir = Path(base_dir or Path.cwd()).resolve()

    telemetry, telemetry_unavailable, telemetry_cause = None, False, None
    # An absent section and an explicit ``"telemetry": null`` are the same: no
    # section. Only ``{"enabled": false}`` disables collection outright.
    specification = config.get("telemetry")
    if specification is None and telemetry_defaults is not None:
        specification = telemetry_defaults.specification()
    if specification is not None and not (isinstance(specification, dict) and specification.get("enabled") is False):
        try:
            telemetry = _select("telemetry", specification, base_dir)
        except OrchestrationError as error:
            # Telemetry failure must not prevent unrelated work.
            # Structural messages name sections and adapters, never option values.
            telemetry_unavailable, telemetry_cause = True, {"code": error.code, "detail": str(error)[:200]}
            warnings += ({"code": "telemetry_configuration_invalid",
                          "message": "Telemetry configuration is invalid; telemetry will be unavailable."},)

    adapters, collections = {}, {}
    provider_configurations, evaluator_configurations = {}, {}

    def collection(kind):
        specifications = config.get(kind, {})
        if not isinstance(specifications, dict):
            raise OrchestrationError(f"{kind} must be a map of names to adapters.", "configuration_error")
        result = {}
        for name, specification in specifications.items():
            if not isinstance(name, str) or not name or "/" in name:
                raise OrchestrationError(f"Invalid {kind} name.", "configuration_error")
            if isinstance(specification, dict) and specification.get("enabled") is False:
                continue
            result[name] = selection = _select(kind, specification, base_dir)
            if kind in {"providers", "evaluators"}:
                material = deepcopy(specification)
                material_options = material.setdefault("options", {})
                material_options["cwd"] = _resolve_path(base_dir, material_options.get("cwd", "."), kind, selection.adapter)
                if kind == "evaluators" and specification.get("adapter") == "tool":
                    provider = material["options"].get("provider")
                    material["measurement_provider_configuration"] = provider_configurations.get(provider)
                identities = provider_configurations if kind == "providers" else evaluator_configurations
                identities[name] = canonical_digest(material)
        collections[kind] = result

    # Sections are checked in construction order, so among structural errors
    # the first one reported is stable. Every structural error is reported
    # before any factory runs, so a factory's own option error is reported
    # only when the whole configuration is structurally valid.
    for kind in ("state_engine", "store"):
        adapters[kind] = _select(kind, config.get(kind, _SINGLETONS[kind]), base_dir)
    collection("providers")
    collection("sessions")
    for kind in ("guardrail", "routing"):
        adapters[kind] = _select(kind, config.get(kind, _SINGLETONS[kind]), base_dir)
    collection("evaluators")
    for kind in ("acceptance", "session_selection", "delivery_scheduler", "retry_policy", "receipt_policy"):
        adapters[kind] = _select(kind, config.get(kind, _SINGLETONS[kind]), base_dir)
    return ResolvedConfig(
        version=version, base_dir=base_dir, config=config, adapters=adapters, collections=collections,
        telemetry=telemetry, telemetry_unavailable=telemetry_unavailable,
        provider_configurations=provider_configurations, evaluator_configurations=evaluator_configurations,
        warnings=warnings, telemetry_cause=telemetry_cause,
    )


def build(config=None, base_dir=None, *, project=None, telemetry_defaults=None):
    """Assemble a runtime.

    ``project`` is the explicit ``ProjectContext`` of a set-up project: it
    supplies the telemetry defaults, the base directory (its data directory)
    when none is given, and the default worktree. ``telemetry_defaults``
    enables collection without a telemetry section for callers without a
    project; it takes precedence over the project's defaults when both are given.
    """
    if project is not None and not isinstance(project, ProjectContext):
        raise OrchestrationError("Project must be a ProjectContext value.", "configuration_error")
    if project is not None:
        telemetry_defaults = telemetry_defaults or project.telemetry_defaults()
        base_dir = base_dir or project.data_dir
    resolved = validate_config(config, base_dir, telemetry_defaults=telemetry_defaults)
    config = resolved.config
    resources = []
    providers = {}

    def construct(selection):
        try:
            module, name = selection.target.split(":", 1)
            factory = getattr(importlib.import_module(module), name)
            options = deepcopy(selection.options)
            if selection.kind == "telemetry" and selection.adapter == "local":
                resource = factory(options, resolve=lambda component, adapter: factory_for(
                    "telemetry_" + component, adapter))
            elif selection.kind == "evaluators" and selection.adapter == "tool":
                resource = factory(options, providers)
            elif selection.kind == "providers" and selection.adapter == "pr":
                resource = factory(options, store=store, state_engine=state_engine)
            else:
                resource = factory(options)
        except OrchestrationError:
            raise
        except Exception as exc:
            raise OrchestrationError(f"Cannot construct {selection.kind} adapter {selection.adapter}.",
                                     "configuration_error") from exc
        resources.append(resource)
        return resource

    def collection(kind):
        return {name: construct(selection) for name, selection in resolved.collections[kind].items()}

    try:
        telemetry = None
        if resolved.telemetry is not None:
            try:
                telemetry = construct(resolved.telemetry)
            except Exception as error:
                # Telemetry failure must not prevent unrelated work. Keep a safe
                # explicit state with a bounded cause; source exception text may
                # contain credentials and is not retained.
                from .adapters.telemetry import UnavailableTelemetry, construction_cause
                telemetry = UnavailableTelemetry(cause=construction_cause(error))
                resources.append(telemetry)
        elif resolved.telemetry_unavailable:
            from .adapters.telemetry import UnavailableTelemetry
            telemetry = UnavailableTelemetry(cause=resolved.telemetry_cause)
            resources.append(telemetry)
        adapters = resolved.adapters
        state_engine = construct(adapters["state_engine"])
        store = construct(adapters["store"])
        providers.update(collection("providers"))
        sessions = collection("sessions")
        guardrail = construct(adapters["guardrail"])
        routing = construct(adapters["routing"])
        evaluators = collection("evaluators")
        application = Application(
            store=store, providers=providers, sessions=sessions, guardrail=guardrail,
            routing=routing, evaluators=evaluators,
            clock=lambda: datetime.now(timezone.utc).isoformat(), identifiers=lambda: uuid4().hex,
            assessment=config.get("assessment"),
            acceptance=construct(adapters["acceptance"]),
            session_selection=construct(adapters["session_selection"]),
            delivery_scheduler=construct(adapters["delivery_scheduler"]),
            retry_policy=construct(adapters["retry_policy"]),
            receipt_policy=construct(adapters["receipt_policy"]),
            session_fallbacks=config.get("session_fallbacks"),
            provider_configurations=resolved.provider_configurations,
            evaluator_configurations=resolved.evaluator_configurations,
            state_engine=state_engine,
            telemetry=telemetry,
            worktree=project.worktree if project is not None else None,
        )
        def capture(event):
            if isinstance(event, dict):
                return application.record_observation(event.get("event_type", "process.observation"),
                    event.get("payload", {}), event.get("context", {}),
                    **{key: event[key] for key in ("event_id", "basis", "completeness", "reason") if key in event})
            return None
        for resource in (*providers.values(), *sessions.values(), *evaluators.values()):
            bind_telemetry = getattr(resource, "bind_telemetry", None)
            if callable(bind_telemetry) and telemetry is not None:
                bind_telemetry(capture)
        for name, driver in sessions.items():
            bind_state_engine = getattr(driver, "bind_state_engine", None)
            if callable(bind_state_engine):
                bind_state_engine(state_engine)
            bind_observer = getattr(driver, "bind_observer", None)
            if callable(bind_observer):
                bind_observer(lambda context, observation, name=name: application.observe_native(name, context, observation))
            specification = config["sessions"][name]
            if specification.get("options", {}).get("application_tools", False):
                _wire_application_tools(name, driver, application)
        return Runtime(application, resources, resolved.warnings)
    except BaseException:
        # Construction is transactional with respect to owned process/connection lifetimes.
        try:
            Runtime(None, resources).close()
        except Exception:
            pass
        raise


def _wire_application_tools(name: str, driver: SessionDriver, application: Application) -> None:
    if not callable(getattr(driver, "bind_application_tools", None)):
        raise OrchestrationError("This session adapter cannot expose application tools.", "configuration_error")
    tools = cast(ApplicationToolSessionDriver, driver)
    capabilities = {capability.name: capability for capability in application.describe()}

    def handle(action, arguments, context, idempotency_key=None, *, replay_only=False):
        if not isinstance(action, str) or action not in capabilities:
            raise OrchestrationError("Unknown application tool.", "unknown_action")
        if not isinstance(arguments, dict) or not isinstance(context, dict):
            raise OrchestrationError("Tool arguments and trusted context must be objects.")
        arguments = deepcopy(arguments)
        schema = capabilities[action].input_schema
        properties = schema.get("properties", {})
        if "work_id" in properties and context.get("work_id") and "work_id" not in arguments:
            arguments["work_id"] = context["work_id"]
        if "idempotency_key" in properties:
            required = "idempotency_key" in schema.get("required", [])
            if idempotency_key is None and not required:
                arguments.pop("idempotency_key", None)
            elif not isinstance(idempotency_key, str) or not idempotency_key:
                raise OrchestrationError("This application tool requires a stable invocation key.")
            else:
                arguments["idempotency_key"] = "session:" + json.dumps([name, idempotency_key], separators=(",", ":"))
        # Native tool calls come from a session's own process: they carry the
        # worktree that created the session, whichever thread delivers them.
        with bound_worktree(_session_worktree(application, context.get("session_id"))):
            return dispatch(action, arguments, context, replay_only)

    def dispatch(action, arguments, context, replay_only):
        # The driver supplies the owned session's principal separately from tool arguments.
        observed = all(context.get(field) for field in ("session_id", "native_id", "incarnation", "call_id"))
        if observed:
            for attempt in range(8):
                try:
                    return application.invoke_native(name, action, arguments, context, replay_only=replay_only)
                except OrchestrationError as error:
                    if not replay_only or error.code != "conflict" or not error.details.get("retry_safe") or attempt == 7:
                        raise
        if replay_only:
            raise OrchestrationError("Replay requires its original native binding.", "native_evidence_missing")
        return application.call(action, arguments, actor=context.get("actor"))

    tools.bind_application_tools(deepcopy(list(capabilities.values())), handle)
    replay = getattr(tools, "bind_application_replay", None)
    if callable(replay):
        replay(lambda action, arguments, context, key=None:
               handle(action, arguments, context, key, replay_only=True))


def _session_worktree(application, session_id):
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        with application.store.transaction() as tx:
            session = tx.get("session", session_id)
    except Exception:
        return None
    return session_worktree(session.data) if session is not None else None


def describe(application):
    return [asdict(capability) for capability in application.describe()]
