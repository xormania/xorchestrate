"""Value types of the developer-settings layer."""

from __future__ import annotations

from dataclasses import dataclass

KINDS = frozenset({"str", "path", "int", "float", "command", "json"})
SOURCE_KINDS = ("flag", "env", "repo_file", "user_file", "default")
"""Where a value can come from, highest precedence first."""


@dataclass(frozen=True)
class Setting:
    """One declared setting.

    ``default`` is template text or None. Path and str defaults may use the XDG base
    directories and ``{repo_name}``; command defaults keep their tool placeholders.
    ``evidence`` marks a setting that can change what a run proves, so runs record it.
    ``json_type`` optionally restricts a json value to one Python type.
    """

    key: str
    kind: str
    default: str | None
    description: str
    section: str
    evidence: bool = False
    json_type: type | None = None


@dataclass(frozen=True)
class Source:
    kind: str
    path: str | None = None
    line: int | None = None

    def describe(self) -> str:
        if self.path is None:
            return self.kind
        return f"{self.kind} {self.path}:{self.line}"


class DevConfigError(Exception):
    """A refused settings file or value, or a lookup of an undeclared setting."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
