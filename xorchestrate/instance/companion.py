"""The user's private companion repository: overlays, approvals, policies and launchers.

```
<companion>/overlays/<project>.json   the user's own settings for a project (no approval needed)
<companion>/approvals/<project>.json  approved hashes of the project's tracked configuration
<companion>/policies/*.json           the user's fragments, merged above every project's config.json
<companion>/launchers/                executables copied under the home directory at render time
```

Approvals live here (D13) because only the tracked configuration can be
changed by agents or other people's commits; the companion belongs to the user.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..model import OrchestrationError
from .home import read_document, write_document

LAYOUT = ("overlays", "approvals", "policies", "launchers")
SCHEMA = 1
README = """# Xorchestrate companion

This private repository holds your own xorchestrate settings for every project:

- `overlays/<project>.json`: your overrides of a project's configuration. They
  win over the project's tracked configuration, guardrails included.
- `approvals/<project>.json`: the hashes of tracked project configurations you
  approved with `xorchestrate approve`.
- `policies/*.json`: fragments merged above every project's `config.json`.
- `launchers/`: executables your configurations reference as `${launchers}`.

Keep it under version control and private.
"""
GITATTRIBUTES = "telemetry/**/*.sqlite3 filter=lfs diff=lfs merge=lfs -text\n"


def is_companion(path):
    path = Path(path)
    return path.is_dir() and all((path / name).is_dir() for name in ("overlays", "approvals"))


def init_companion(path):
    """Create the minimal companion layout; existing files are never overwritten."""
    path = Path(path).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise OrchestrationError("The companion path is not a directory.", "invalid_arguments", {"path": str(path)})
    created = []
    for name in LAYOUT:
        directory = path / name
        if not directory.exists():
            directory.mkdir(parents=True)
            created.append(str(directory))
    for name, text in (("README.md", README), (".gitattributes", GITATTRIBUTES)):
        target = path / name
        if not target.exists():
            target.write_text(text, encoding="utf-8")
            created.append(str(target))
    return path, created


@dataclass(frozen=True)
class Companion:
    root: Path

    @classmethod
    def open(cls, path):
        path = Path(path)
        if not is_companion(path):
            raise OrchestrationError("The registered companion is missing or incomplete; run xorchestrate setup.",
                                     "companion_required", {"path": str(path)})
        return cls(path)

    def overlay_path(self, project):
        return self.root / "overlays" / f"{project}.json"

    def approvals_path(self, project):
        return self.root / "approvals" / f"{project}.json"

    @property
    def policies(self):
        return self.root / "policies"

    @property
    def launchers(self):
        return self.root / "launchers"

    def overlay(self, project):
        return read_document(self.overlay_path(project), {})

    def ensure_overlay(self, project):
        """Create an empty overlay for ``project`` if absent; return whether it was created."""
        path = self.overlay_path(project)
        if path.exists():
            return False
        write_document(path, {}, 0o644)
        return True

    def approvals(self, project):
        document = read_document(self.approvals_path(project))
        if document is None:
            return None
        if document.get("schema") != SCHEMA or not isinstance(document.get("approved"), list):
            raise OrchestrationError("Unsupported approvals file.", "invalid_configuration",
                                     {"path": str(self.approvals_path(project))})
        return document

    def approved_hashes(self, project):
        document = self.approvals(project)
        return [] if document is None else [item.get("hash") for item in document["approved"] if isinstance(item, dict)]

    def is_approved(self, project, config_hash):
        return config_hash in self.approved_hashes(project)

    def approve(self, project, config_hash, digests, worktree):
        """Record ``config_hash`` (several hashes may be approved); return whether it was new."""
        document = self.approvals(project) or {"schema": SCHEMA, "project": project, "approved": []}
        if any(isinstance(item, dict) and item.get("hash") == config_hash for item in document["approved"]):
            return False
        document["approved"].append({
            "hash": config_hash, "files": dict(digests), "branch": worktree.get("branch"),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        })
        write_document(self.approvals_path(project), document, 0o644)
        return True

    def approved_files(self, project, config_hash):
        document = self.approvals(project) or {"approved": []}
        for item in document["approved"]:
            if isinstance(item, dict) and item.get("hash") == config_hash and isinstance(item.get("files"), dict):
                return item["files"]
        return None
