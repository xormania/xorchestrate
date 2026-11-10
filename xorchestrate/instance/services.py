"""Operator adapters selected from the user's home: version control, repository hosting and companions.

Each is selected like any other adapter, by name and options
(``{"adapter": "git-directory", "options": {"path": "/abs"}}``); a trusted
installed ``module:factory`` works too. Selections live only in the user's
home, never in a project's tracked ``.xorchestrate/``:

- ``settings.json``: ``version_control`` (default ``git``),
  ``repository_hosting`` (default ``github``) and the machine-default
  ``companion``;
- a project's ``instance.json``: an optional ``companion`` for that project,
  which takes precedence over the machine default.

A ``companion`` setting that is a plain path (written by earlier versions) is
read as the ``git-directory`` adapter at that path and rewritten in the new
shape.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..composition import factory_for
from ..model import OrchestrationError
from .home import Home

DEFAULTS = {"version_control": {"adapter": "git"}, "repository_hosting": {"adapter": "github"}}
DEFAULT_COMPANION_ADAPTER = "git-directory"


def selection(kind, specification, where):
    """Check an ``{"adapter", "options"}`` selection; return ``(adapter, options)``."""
    if (not isinstance(specification, dict) or not isinstance(specification.get("adapter"), str)
            or not specification["adapter"] or specification.keys() - {"adapter", "options"}
            or not isinstance(specification.get("options", {}), dict)):
        raise OrchestrationError(f"{where} {kind} must be {{\"adapter\": NAME, \"options\": {{...}}}}.",
                                 "invalid_configuration", {"kind": kind})
    return specification["adapter"], deepcopy(specification.get("options", {}))


def construct(kind, specification, where, **dependencies):
    adapter, options = selection(kind, specification, where)
    try:
        # An unimportable reference is a configuration error like a failing factory.
        factory = factory_for(kind, adapter)
        return factory(options, **dependencies)
    except OrchestrationError:
        raise
    except Exception as exc:
        raise OrchestrationError(f"Cannot construct {kind} adapter {adapter}.", "configuration_error") from exc


def companion_path_selection(path, adapter=DEFAULT_COMPANION_ADAPTER):
    """The selection of a companion at a local path (``companion init|use PATH``)."""
    return {"adapter": adapter, "options": {"path": str(Path(path).expanduser().resolve())}}


class Services:
    """The operator adapters of one home."""

    def __init__(self, home):
        self.home = home
        self._cache = {}

    def _setting(self, kind):
        return self.home.settings().get(kind, DEFAULTS[kind])

    @property
    def version_control(self):
        if "version_control" not in self._cache:
            self._cache["version_control"] = construct("version_control", self._setting("version_control"),
                                                       "settings.json")
        return self._cache["version_control"]

    @property
    def repository_hosting(self):
        if "repository_hosting" not in self._cache:
            self._cache["repository_hosting"] = construct("repository_hosting", self._setting("repository_hosting"),
                                                          "settings.json")
        return self._cache["repository_hosting"]

    def companion_for(self, specification, where="companion"):
        return construct("companion", specification, where, version_control=self.version_control)

    def machine_companion(self):
        """The machine-default companion selection, migrating a plain path setting; ``None`` before setup."""
        value = self.home.settings().get("companion")
        if value is None:
            return None
        if isinstance(value, str):
            if not Path(value).is_absolute():
                raise OrchestrationError("settings.json companion must be an absolute path.", "invalid_configuration")
            value = companion_path_selection(value)
            self.home.update_settings(companion=value)
        selection("companion", value, "settings.json")
        return value

    def companion_selection(self, instance=None):
        """``(selection, scope)``: the project's own companion if set, else the machine default."""
        if instance is not None and instance.record.get("companion") is not None:
            value = instance.record["companion"]
            selection("companion", value, "instance.json")
            return value, "project"
        value = self.machine_companion()
        return (value, "machine") if value is not None else (None, None)

    def companion(self, instance=None, *, validate=True):
        """The companion for ``instance`` (or the machine default), which must exist.

        Its adapter's location check runs every time it is opened, unless
        ``validate`` is false (``doctor`` reports the check instead).
        """
        value, scope = self.companion_selection(instance)
        if value is None:
            raise OrchestrationError("No companion is registered; run `xorchestrate setup`.", "companion_required")
        companion = self.companion_for(value, "instance.json" if scope == "project" else "settings.json")
        if not companion.exists():
            raise OrchestrationError("The selected companion is missing or incomplete; run xorchestrate setup.",
                                     "companion_required", {"location": companion.location, "scope": scope})
        if validate:
            refuse_unsafe(companion)
        return companion


def refuse_unsafe(companion):
    """Refuse a companion whose adapter reports its location unsafe."""
    reason = companion.validate_location()
    if reason is not None:
        raise OrchestrationError(f"The companion at {companion.location} is unsafe: {reason}. Approvals there could "
                                 "be written by the agents or accounts they guard against.", "companion_exposed",
                                 {"location": companion.location, "reason": reason})
    return companion


def services(environ=None):
    return Services(Home.locate(environ))
