"""Ordinary capability access to the runtime's authorized telemetry service.

The provider owns neither capture nor storage. Composition supplies one service;
discovery never constructs a collector or changes its configuration.
"""

from copy import deepcopy

from ..model import Capability, OrchestrationError
from ..schema import validate
from ..telemetry_actions import TELEMETRY_OPERATIONS


class TelemetryProvider:
    def __init__(self, options=None):
        options = {} if options is None else options
        if not isinstance(options, dict) or options.keys() - {"operations"}:
            raise OrchestrationError("Telemetry provider accepts only operations.", "invalid_configuration")
        operations = options.get("operations", list(TELEMETRY_OPERATIONS))
        if (not isinstance(operations, list) or not operations
                or any(not isinstance(name, str) or name not in TELEMETRY_OPERATIONS for name in operations)
                or len(set(operations)) != len(operations)):
            raise OrchestrationError("Telemetry operations must be unique supported names.", "invalid_configuration")
        self._operations = tuple(operations)
        self._service = None

    def bind_telemetry_service(self, service):
        """Receive only the authorized application service, never the facade."""
        self._service = service

    def execution_policies(self):
        """Trusted installed-code defaults; these are not discovery annotations."""
        return {name: {"mode": "receipt" if name in {"snapshot", "restore", "report"} else "query",
                       "observation": "none"} for name in self._operations}

    def discover(self):
        return [Capability(name, TELEMETRY_OPERATIONS[name].description,
                           deepcopy(TELEMETRY_OPERATIONS[name].input_schema),
                           deepcopy(TELEMETRY_OPERATIONS[name].annotations))
                for name in self._operations]

    def invoke(self, name, arguments, context):
        if name not in self._operations:
            raise OrchestrationError("Telemetry operation is not exposed by this provider.", "not_found")
        validate(arguments, TELEMETRY_OPERATIONS[name].input_schema)
        actor = context.get("actor") if isinstance(context, dict) else None
        if not isinstance(actor, str) or not actor:
            raise OrchestrationError("Telemetry requires the authenticated caller context.", "invalid_arguments")
        if self._service is None:
            raise OrchestrationError("Telemetry service has not been bound.", "telemetry_unavailable")
        return self._service.invoke(name, deepcopy(arguments), actor)

    def close(self):
        # The runtime owns the shared collector and its lifetime.
        self._service = None
