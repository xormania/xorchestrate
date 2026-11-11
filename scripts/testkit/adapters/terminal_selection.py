"""Choose and build the terminal host a supervised run uses, from the test kit's own configuration.

The selection is a product terminal-host selection (``{"adapter", "options"?,
"fallback"?}``) taken from the profile or the command line, defaulting to the
product's default host without its fallback. The test kit adds the placement and the ownership mode
itself and builds the adapter through the product's composition, so no adapter
is named here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


def _product():
    """The product's composition, loaded on use: suites without a terminal host never import it."""
    from xorchestrate.composition import DEFAULT_TERMINAL_HOST, factory_for
    from xorchestrate.instance.services import selection, terminal_host_selection
    from xorchestrate.model import OrchestrationError

    return DEFAULT_TERMINAL_HOST, factory_for, selection, terminal_host_selection, OrchestrationError


@dataclass(frozen=True)
class SelectedHost:
    """The host in use (``None`` when none is ready) and how it was chosen, for readiness and receipts."""

    host: object | None
    adapter: str | None
    selected: str
    fallback_reason: str | None = None
    reason: str | None = None

    def report(self):
        report = {"adapter": self.adapter, "selected": self.selected, "fallback_reason": self.fallback_reason}
        if self.host is not None:
            report.update(survives_controller=self.host.survives_controller, interactive=self.host.interactive)
        if self.reason is not None:
            report["reason"] = self.reason
        return report


def parse(value):
    """A selection from configuration: a mapping, a JSON text, or ``None`` for the product's default."""
    default, _, _, terminal_host_selection, _ = _product()
    if value is None:
        # The product's default host without its fallback: a test run never moves to another host silently.
        # Another host is used only when selected explicitly.
        return {key: item for key, item in default.items() if key != "fallback"}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("terminal host selection must be JSON: {\"adapter\": NAME, ...}") from None
    if not isinstance(value, dict):
        raise ValueError("terminal host selection must be an object with an adapter")
    terminal_host_selection(value, "test kit")  # same shape rules as the product
    return value


def recorded(value):
    """A selection read back from a run's own files, which the run's harness can rewrite: built-in host names only.

    Only the adapter name (and its fallback's) is taken, never options. Anything that is not one of the
    product's built-in terminal hosts, any ``module:factory`` included, is refused before anything is imported.
    """
    from xorchestrate.composition import BUILTINS

    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    builtin = set(BUILTINS["terminal_host"])

    def name(specification):
        adapter = specification.get("adapter") if isinstance(specification, dict) else None
        if not isinstance(adapter, str) or adapter not in builtin:
            raise ValueError("The run's recorded terminal host is not a built-in host; it is not used")
        return adapter

    result = {"adapter": name(value)}
    if isinstance(value, dict) and value.get("fallback") is not None:
        result["fallback"] = {"adapter": name(value["fallback"])}
    return result


def build(value, *, name, location, socket, owned=True):
    """Build the selected host at this placement, or its fallback when the selected one is not ready.

    ``owned`` hosts end with this controller (the test kit's default, which
    keeps descendant tracking authoritative); a surviving host is only for runs
    that close later. Never raises for an unready host: the result says why.
    """
    _, factory_for, adapter_selection, terminal_host_selection, OrchestrationError = _product()
    primary, fallback = terminal_host_selection(parse(value), "test kit")
    placement = {"name": name, "location": str(location), "socket": str(socket), "owned": owned}

    def make(specification, where):
        adapter, options = adapter_selection("terminal_host", specification, where)
        try:
            return adapter, factory_for("terminal_host", adapter)({**options, **placement})
        except OrchestrationError:
            raise
        except Exception as error:
            raise OrchestrationError(f"Cannot construct terminal host {adapter}.", "configuration_error") from error

    adapter, host = make(primary, "test kit terminal host")
    reason = host.readiness()
    if reason is None:
        return SelectedHost(host, adapter, adapter)
    host.close()
    if fallback is None:
        return SelectedHost(None, None, adapter, reason=reason)
    fallback_adapter, fallback_host = make(fallback, "test kit terminal host fallback")
    fallback_problem = fallback_host.readiness()
    if fallback_problem is not None:
        fallback_host.close()
        return SelectedHost(None, None, adapter, reason=f"{reason}; fallback {fallback_adapter}: {fallback_problem}")
    return SelectedHost(fallback_host, fallback_adapter, adapter, fallback_reason=reason)


def readiness(value, *, name, location, socket):
    """A readiness item named for the port, with the adapter's own reason as its text; nothing is started."""
    selected = build(value, name=name, location=location, socket=socket)
    if selected.host is not None:
        selected.host.close()
    item = {"name": "terminal_host", "passed": selected.host is not None, **selected.report()}
    if selected.reason is not None:
        item["reason"] = selected.reason
    return item
