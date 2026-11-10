"""``xorchestrate migrate-config``: rewrite a configuration to the format version this release reads.

Configuration is never rewritten on load; this command is the explicit step.
It reads one configuration file (by default the project's tracked
``.xorchestrate/config.json``), applies the release's ordered rewrites
``N -> N + 1`` (``composition.CONFIG_MIGRATIONS``) and prints the steps and a
unified diff. Nothing is written without ``--write``. With ``--write`` the file
is replaced atomically and the original is kept under
``<home>/backups/migrate-config/<UTC time>/``.

A tracked configuration that changes needs approval again: the command never
approves. Review the diff, commit it and run ``xorchestrate approve``. A
configuration without ``version`` gains an explicit one; a newer version is
refused (``configuration_version_unsupported``).
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

from ..composition import CONFIG_MIGRATIONS, RELEASE, read_json
from ..model import OrchestrationError
from .home import Home, private_directory, write_document
from .project import CONFIG_FILE, TRACKED_DIRECTORY, configuration_hash, digests_of, read_tracked_files
from .services import Services


def _text(document):
    """The serialization ``write_document`` uses, so the diff shows exactly what is written."""
    return json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _target(file, path, services):
    if file is not None:
        return Path(file).expanduser().resolve(), None
    from .instance import locate_project
    tracked = locate_project(path, services.version_control)
    return tracked.directory / CONFIG_FILE, tracked


def migrate_config(file=None, *, write=False, path=None, environ=None):
    home = Home.locate(environ)
    services = Services(home)
    target, tracked = _target(file, path, services)
    try:
        original = target.read_bytes()
    except FileNotFoundError as exc:
        raise OrchestrationError("The configuration file does not exist.", "invalid_arguments",
                                 {"path": str(target)}) from exc
    try:
        document = read_json(original.decode("utf-8"))
    except UnicodeError as exc:
        raise OrchestrationError("The configuration is not UTF-8 JSON.", "invalid_json", {"path": str(target)}) from exc
    migrated, version, applied = CONFIG_MIGRATIONS.migrate(document)
    changed = migrated != document
    before, after = original.decode("utf-8"), _text(migrated)
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=f"{target.name} (version {document.get('version', 'absent')})",
                                        tofile=f"{target.name} (version {migrated['version']})")) if changed else ""
    report = {"path": str(target), "release": RELEASE, "from": document.get("version"), "read_as": version,
              "to": migrated["version"], "steps": applied, "changed": changed, "diff": diff, "written": False}
    tracked_file = tracked is not None or TRACKED_DIRECTORY in target.parts
    if not changed:
        report["next"] = "The configuration is already at the current format version."
        return report
    if not write:
        report["next"] = "Run again with --write to rewrite the file."
        return report
    backup = private_directory(home.ensure().root / "backups" / "migrate-config"
                               / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    shutil.copyfile(target, backup / target.name)
    mode = stat.S_IMODE(os.stat(target).st_mode)
    write_document(target, migrated, mode)
    report.update(written=True, original=str(backup / target.name))
    if tracked_file:
        report["approval"] = {"state": "required"}
        if tracked is not None:
            report["approval"]["config_hash"] = configuration_hash(digests_of(read_tracked_files(tracked.directory)))
        report["next"] = ("The tracked configuration changed: review the diff, commit it, then run "
                          "`xorchestrate approve`; xorchestrate never approves for you.")
    return report
