"""How this launcher was installed, and the command that upgrades it.

The source comes from the installed distribution's PEP 610 ``direct_url.json``:
absent for an index install, otherwise ``vcs_info`` (git), ``dir_info`` (a local
directory, editable or not) or ``archive_info`` (a wheel or sdist file or URL).
The installer is recognised from markers its environments carry: ``uv tool
install`` writes ``uv-receipt.toml``, pipx writes ``pipx_metadata.json``, and
``uvx`` runs from an environment inside uv's cache.
"""

import json
import shlex
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

DISTRIBUTION = "xorchestrate"
SOURCES = ("index", "git", "local", "archive")
INSTALLERS = ("uv-tool", "pipx", "uvx", "environment")
# The cache directory tag uv, like other tools, writes at its cache root.
CACHEDIR_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"
UV_EPHEMERAL_BUCKETS = ("archive-v", "builds-v")


@dataclass(frozen=True)
class Installation:
    source: str
    installer: str
    url: str | None = None
    commit: str | None = None
    editable: bool = False
    python: str | None = None

    @property
    def reported_source(self):
        """`uvx` replaces the source in reports: nothing is installed to upgrade."""
        return "uvx" if self.installer == "uvx" else self.source


def direct_url(distribution=metadata.distribution, name=DISTRIBUTION):
    """The distribution's PEP 610 record, or ``None`` when absent or unreadable."""
    try:
        text = distribution(name).read_text("direct_url.json")
        data = json.loads(text) if text else None
    except (metadata.PackageNotFoundError, OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def classify_source(record):
    """Map a PEP 610 record to ``(source, url, commit, editable)``."""
    if record is None:
        return "index", None, None, False
    url = record.get("url") if isinstance(record.get("url"), str) else None
    vcs = record.get("vcs_info")
    if isinstance(vcs, dict):
        commit = vcs.get("commit_id")
        return "git", url, commit if isinstance(commit, str) and commit else None, False
    directory = record.get("dir_info")
    if isinstance(directory, dict):
        return "local", url, None, directory.get("editable") is True
    return "archive", url, None, False


def _is_uv_cache(path):
    try:
        return (path / "CACHEDIR.TAG").read_text(encoding="utf-8").startswith(CACHEDIR_SIGNATURE)
    except (OSError, UnicodeError):
        return False


def detect_installer(prefix):
    """Recognise the tool that created the environment at ``prefix``."""
    prefix = Path(prefix)
    if (prefix / "uv-receipt.toml").is_file():
        return "uv-tool"
    if (prefix / "pipx_metadata.json").is_file():
        return "pipx"
    if prefix.parent.name.startswith(UV_EPHEMERAL_BUCKETS) and _is_uv_cache(prefix.parent.parent):
        return "uvx"
    return "environment"


def installation(distribution=metadata.distribution, prefix=None, python=None, name=DISTRIBUTION):
    source, url, commit, editable = classify_source(direct_url(distribution, name))
    return Installation(source, detect_installer(sys.prefix if prefix is None else prefix), url, commit,
                        editable, sys.executable if python is None else python)


def _requirement(info, name):
    """What to install again; placeholders mark the version the user chooses."""
    if info.source == "index":
        return name
    if info.source == "git":
        return "git+" + (info.url or "<repository>") + "@<tag>"
    if info.source == "local" and info.url and urlsplit(info.url).scheme == "file":
        return unquote(urlsplit(info.url).path)
    if info.source == "local":
        return info.url or "<directory>"
    return "<newer wheel or sdist>"


def upgrade_command(info, name=DISTRIBUTION):
    """The command that upgrades this launcher the way it was installed."""
    requirement = _requirement(info, name)
    editable = ["--editable"] if info.editable else []
    if info.installer == "uv-tool":
        argv = (["uv", "tool", "upgrade", name] if info.source == "index"
                else ["uv", "tool", "install", "--force", *editable, requirement])
    elif info.installer == "pipx":
        argv = (["pipx", "upgrade", name] if info.source == "index"
                else ["pipx", "install", "--force", *editable, requirement])
    elif info.installer == "uvx":
        argv = (["uvx", "--from", name + "@latest", name] if info.source == "index"
                else ["uvx", "--refresh", "--from", requirement, name])
    else:
        argv = ["uv", "pip", "install", "--python", info.python or "python", "--upgrade", *editable, requirement]
    return shlex.join(argv)
