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

from .application import Application
from .model import OrchestrationError, canonical_digest, unique_keys
from .ports import ApplicationToolSessionDriver, SessionDriver

BUILTINS = {
    "telemetry": {"local": "xorchestrate.adapters.telemetry:LocalTelemetry"},
    # Storage selected inside the local telemetry adapter's ``corpus``/``artifacts`` options.
    "telemetry_corpus": {"sqlite": "xorchestrate.adapters.telemetry_collector.sqlite:corpus"},
    "telemetry_artifacts": {"filesystem": "xorchestrate.adapters.telemetry_collector.artifacts:filesystem"},
    "state_engine": {"declared": "xorchestrate.adapters.state:ConfiguredStateEngine"},
    "store": {"memory": "xorchestrate.adapters.storage:MemoryStore", "sqlite": "xorchestrate.adapters.storage:SQLiteStore"},
    "providers": {"mcp": "xorchestrate.adapters.capabilities:StdioMCPProvider", "cli": "xorchestrate.adapters.capabilities:CLIProvider"},
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
}


# Adapter kinds that are selected inside another adapter's options, not as sections.
NESTED = frozenset({"telemetry_corpus", "telemetry_artifacts"})


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
        values = (self.project_id, self.collector_id, self.dataset_id)
        if not isinstance(self.runtime_root, (str, Path)) or not all(isinstance(value, str) and value for value in values):
            raise OrchestrationError("Telemetry defaults require a runtime root and nonempty identities.",
                                     "configuration_error")
        return {"adapter": "local", "options": {
            "runtime_root": str(Path(self.runtime_root).expanduser()), "project_id": self.project_id,
            "dataset_id": self.dataset_id, "collector_id": self.collector_id}}


def _factory(kind, adapter):
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
    def __init__(self, application, resources):
        self.application = application
        self._resources = resources

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


def build(config=None, base_dir=None, *, telemetry_defaults=None):
    """Assemble a runtime; ``telemetry_defaults`` enables collection without a telemetry section."""
    if telemetry_defaults is not None and not isinstance(telemetry_defaults, TelemetryDefaults):
        raise OrchestrationError("Telemetry defaults must be a TelemetryDefaults value.", "configuration_error")
    try:
        config = json.loads(json.dumps({} if config is None else config, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("Configuration must contain JSON values.", "configuration_error") from exc
    if not isinstance(config, dict):
        raise OrchestrationError("Configuration must be an object.", "configuration_error")
    unknown = set(config) - (set(BUILTINS) - NESTED) - {"assessment", "session_fallbacks"}
    if unknown:
        raise OrchestrationError("Unknown configuration sections.", "configuration_error", {"sections": sorted(unknown)})
    base_dir = Path(base_dir or Path.cwd()).resolve()
    resources = []
    providers = {}
    provider_configurations = {}
    evaluator_configurations = {}

    def construct(kind, specification):
        if not isinstance(specification, dict) or not isinstance(specification.get("adapter"), str):
            raise OrchestrationError(f"{kind} requires an adapter and options object.", "configuration_error")
        if specification.keys() - {"adapter", "options", "enabled"}:
            raise OrchestrationError(f"Unknown {kind} adapter configuration fields.", "configuration_error")
        if "enabled" in specification and type(specification["enabled"]) is not bool:
            raise OrchestrationError(f"{kind} enabled must be a boolean.", "configuration_error")
        options = specification.get("options", {})
        if not isinstance(options, dict):
            raise OrchestrationError(f"{kind} options must be an object.", "configuration_error")
        options = dict(options)
        adapter = specification["adapter"]
        try:
            factory = _factory(kind, adapter)
            if kind in {"providers", "sessions"} and adapter in BUILTINS[kind]:
                options["cwd"] = str((base_dir / options.get("cwd", ".")).resolve())
            if kind == "store" and adapter == "sqlite":
                path = options.get("path", ".xorchestrate/state.sqlite3")
                options["path"] = path if path == ":memory:" else str((base_dir / path).resolve())
            if kind == "telemetry" and adapter == "local":
                if isinstance(options.get("runtime_root"), str):
                    options["runtime_root"] = str(base_dir / options["runtime_root"])
                resource = factory(options, resolve=lambda component, name: _factory("telemetry_" + component, name))
            elif kind == "evaluators" and adapter == "tool":
                resource = factory(options, providers)
            else:
                resource = factory(options)
        except OrchestrationError:
            raise
        except Exception as exc:
            raise OrchestrationError(f"Cannot construct {kind} adapter {adapter}.", "configuration_error") from exc
        resources.append(resource)
        return resource

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
            result[name] = construct(kind, specification)
            if kind in {"providers", "evaluators"}:
                material = deepcopy(specification)
                material.setdefault("options", {})["cwd"] = str((base_dir / material.get("options", {}).get("cwd", ".")).resolve())
                if kind == "evaluators" and specification.get("adapter") == "tool":
                    provider = material["options"].get("provider")
                    material["measurement_provider_configuration"] = provider_configurations.get(provider)
                identities = provider_configurations if kind == "providers" else evaluator_configurations
                identities[name] = canonical_digest(material)
        return result

    try:
        telemetry = None
        specification = config.get("telemetry")
        if specification is None and telemetry_defaults is not None:
            specification = telemetry_defaults.specification()
        if specification is not None and not (isinstance(specification, dict) and specification.get("enabled") is False):
            try:
                telemetry = construct("telemetry", specification)
            except Exception:
                # Telemetry failure must not prevent unrelated work. Keep a safe
                # explicit state; source exceptions may contain credentials.
                from .adapters.telemetry import UnavailableTelemetry
                telemetry = UnavailableTelemetry()
                resources.append(telemetry)
        state_engine = construct("state_engine", config.get("state_engine", {"adapter": "declared"}))
        store = construct("store", config.get("store", {"adapter": "sqlite"}))
        providers.update(collection("providers"))
        sessions = collection("sessions")
        guardrail = construct("guardrail", config.get("guardrail", {"adapter": "rules"}))
        routing = construct("routing", config.get("routing", {"adapter": "configurable"}))
        evaluators = collection("evaluators")
        application = Application(
            store=store, providers=providers, sessions=sessions, guardrail=guardrail,
            routing=routing, evaluators=evaluators,
            clock=lambda: datetime.now(timezone.utc).isoformat(), identifiers=lambda: uuid4().hex,
            assessment=config.get("assessment"),
            acceptance=construct("acceptance", config.get("acceptance", {"adapter": "required-checks"})),
            session_selection=construct("session_selection", config.get("session_selection", {"adapter": "latest"})),
            delivery_scheduler=construct("delivery_scheduler", config.get("delivery_scheduler", {"adapter": "manual"})),
            retry_policy=construct("retry_policy", config.get("retry_policy", {"adapter": "definite-failure"})),
            receipt_policy=construct("receipt_policy", config.get("receipt_policy", {"adapter": "complete-exchange"})),
            session_fallbacks=config.get("session_fallbacks"), provider_configurations=provider_configurations,
            evaluator_configurations=evaluator_configurations,
            state_engine=state_engine,
            telemetry=telemetry,
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
        return Runtime(application, resources)
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


def describe(application):
    return [asdict(capability) for capability in application.describe()]
