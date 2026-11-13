"""Telemetry operations shared by application entrypoints and capability adapters.

The service receives explicit storage, projection and current-authority
collaborators. It neither owns an application facade nor selects infrastructure.
Driving entrypoints validate their input schemas before calling these methods.
"""

from .model import OrchestrationError
from .telemetry import TelemetryViews, dispatch_pressure, record_observation


class TelemetryService:
    """Capture and authorized telemetry operations over live collaborators.

    ``sink`` and ``resources`` are suppliers so replacing the runtime's capture
    adapter or source collections does not leave reads attached to stale objects.
    ``authority`` identifies the current policy and its revision; the context,
    authorization and verification callbacks enforce resource visibility.
    """

    def __init__(self, *, sink, store, context, authorize, verify, projector, authority, resources):
        self._sink = sink
        self._store = store
        self._context = context
        self._authorize = authorize
        self._verify_context = verify
        self._projector = projector
        self._authority = authority
        self._resources = resources

    def views(self):
        return TelemetryViews(self._sink(), self._store, self._context, self._authorize,
                              self._verify_context, self._projector, self._authority)

    def record(self, event_type, payload, context, **metadata):
        return record_observation(self._sink(), event_type, payload, context, **metadata)

    def _read(self, operation):
        authority = self._authority()
        result = operation()
        if self._authority() != authority:
            raise OrchestrationError("Telemetry authority changed during the read.", "conflict")
        return result

    def status(self, arguments, actor):
        return self._read(lambda: self._status(actor))

    def _status(self, actor):
        views = self.views()
        if views.sink is None:
            return {"status": "disabled", "coverage": "unknown", "event_count": 0, "sources": {}, "publication": "unknown"}
        result = views.status(actor)
        health = {}
        for kind, resources in self._resources():
            selected = {}
            for name, resource in resources.items():
                report = getattr(resource, "telemetry_health", None)
                if callable(report):
                    try:
                        selected[name] = dispatch_pressure(report())
                    except Exception:
                        selected[name] = {"status": "unavailable", "coverage": "unknown"}
            if selected:
                health[kind] = selected
        result["source_dispatch"] = health
        return result

    def events(self, arguments, actor):
        return self._read(lambda: self.views().events(arguments, actor))

    def payload(self, arguments, actor):
        return self._read(lambda: self.views().payload(arguments, actor))

    def usage(self, arguments, actor):
        return self._read(lambda: self.views().usage(arguments, actor))

    def compare(self, arguments, actor):
        return self.views().comparison(arguments, actor)

    def snapshot(self, arguments, actor):
        views = self.views()
        sink = views._configured()
        authorize = views.bundle_authorizer(actor, "telemetry_snapshot", sink.snapshot_source())
        return sink.snapshot(arguments["destination"], authorize=authorize, receipt=True)

    def verify(self, arguments, actor):
        views = self.views()
        sink = views._configured()
        authorize = views.bundle_authorizer(actor, "telemetry_verify", sink.bundle_source(arguments["destination"]))
        return sink.verify(arguments["destination"], authorize=authorize, receipt=True)

    def restore(self, arguments, actor):
        views = self.views()
        sink = views._configured()
        authorize = views.bundle_authorizer(actor, "telemetry_restore", sink.bundle_source(arguments["bundle"]))
        return sink.restore(arguments["bundle"], arguments["destination"], authorize=authorize, receipt=True)
