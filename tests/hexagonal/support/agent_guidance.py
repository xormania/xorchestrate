"""A read-only view of a repository tree for the agent guidance contract, and planted trees that prove it bites."""

import os
import shutil
from pathlib import Path

from tests.hexagonal.contracts import agent_guidance as contract

AUTHOR = "127287135+xormania@users.noreply.github.com"


class RepositoryView:
    """Repository-relative reads; nothing outside ``root`` is reported as part of it."""

    def __init__(self, root):
        self.root = Path(root)
        self.real = Path(os.path.realpath(self.root))

    def _path(self, relative):
        return self.root / relative

    def _inside(self, path):
        real = Path(os.path.realpath(path))
        try:
            return real.relative_to(self.real).as_posix()
        except ValueError:
            return None

    def exists(self, relative):
        path = self._path(relative)
        return path.exists() and self._inside(path) is not None

    def is_dir(self, relative):
        return self._path(relative).is_dir()

    def listdir(self, relative):
        path = self._path(relative)
        if not path.is_dir():
            return None
        return sorted(entry.name for entry in path.iterdir())

    def text(self, relative):
        path = self._path(relative)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def link(self, relative):
        path = self._path(relative)
        present = path.is_symlink() or path.exists()
        symlink = path.is_symlink()
        return {"present": present, "symlink": symlink,
                "target": os.readlink(path) if symlink else None,
                "resolved": self._inside(path) if present and path.exists() else None}

    def files_under(self, relative):
        """Regular files below ``relative``, following no symlinks, as repository-relative paths."""
        base = self._path(relative)
        if not base.is_dir():
            return []
        found = []
        for directory, folders, files in os.walk(base):
            folders[:] = sorted(folder for folder in folders if not (Path(directory) / folder).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if path.is_file() and not path.is_symlink():
                    found.append(path.relative_to(self.root).as_posix())
        return found


# Planted trees --------------------------------------------------------------------------------------

SKILL = """---
name: {name}
description: {description}
---

# {name}

Follow [the agent workflow](../../../docs/agent-workflow.md) and the rules in [AGENTS.md](../../../AGENTS.md).
Keep this folder's `SKILL.md` short; the testing guide is `docs/guide.md`. Never keep notes in /tmp.
"""

WORKFLOW = f"""# Agent workflow

Skills live in `.agents/skills`; each has a `SKILL.md`. Read [the guide](guide.md),
[its first section](guide.md#first) and [the rules](../AGENTS.md#rules). See also
[upstream](https://example.org/docs) and [above](#agent-workflow).
Python writes `__pycache__/`, which is not tracked. Run `python -m pytest -q` and
`tests/**` globs; placeholders like `.agents/skills/<name>/SKILL.md` are not paths.
Commits are authored by `xor <{AUTHOR}>`.
"""

RULES = f"""# Working here

Commits are authored by `xor <{AUTHOR}>`.

## Agent workflow

Shared skills live in `.agents/skills`, mirrored for each harness. Start with
[the agent workflow](docs/agent-workflow.md) and `docs/guide.md`.

## Other rules

Nothing here is checked as part of the section.
"""

IMPORTER = "@AGENTS.md\n\nSee [AGENTS.md](AGENTS.md) for the rules.\n"

PROJECT = """# list of languages for which language servers are started; choose from:
#   python, typescript, rust, ...
# The first language is the default language.
language_servers:
- python

# workspace folders handed to the language servers
ls_workspace_folders: ["."]

# the encoding used by text files in the project
# For a list of possible encodings, see https://docs.python.org/3.11/library/codecs.html#standard-encodings
encoding: "utf-8"

# whether to use the project's gitignore file to ignore files
ignore_all_files_in_gitignore: true

# list of additional paths to ignore
# same syntax as gitignore, so you can use * and **
ignored_paths: []

# whether the project is in read-only mode
read_only: false

# how agents reach Serena: tools exposes the symbol tools directly
agent_interface: tools

# tools and apis to exclude or include; see the Serena documentation
excluded_tools: []
excluded_apis: []
included_apis: []

# initial prompt for the project. It will always be given to the LLM upon activating the project
# (contrary to the memories, which are loaded on demand).
initial_prompt: |
  Read AGENTS.md first.
  # Not a comment inside a block scalar,
  - nor a list item, nor a key: value pair.

  Paths like `docs/guide.md` are prose here.

project_name: "fixture"
"""

GITIGNORE = "__pycache__/\n.serena/cache/\n.serena/memories/\n"


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def healthy_tree(root):
    """A small repository satisfying every behaviour: two skills, each mirrored, and every pointer kind."""
    root = Path(root)
    for name, description in (("alpha", "Plan work before changing code."), ("beta", "Prove tests red first.")):
        _write(root, f"{contract.SKILLS}/{name}/SKILL.md", SKILL.format(name=name, description=description))
        mirror = root / contract.MIRROR / name
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.symlink_to(f"../../{contract.SKILLS}/{name}")
    _write(root, contract.WORKFLOW, WORKFLOW)
    _write(root, "docs/guide.md", "# Guide\n\n## First\n")
    _write(root, contract.RULES, RULES)
    _write(root, contract.IMPORTER, IMPORTER)
    _write(root, contract.SERENA, PROJECT)
    _write(root, ".gitignore", GITIGNORE)
    return root


def _replace(root, relative, old, new):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, f"planted fault anchor missing in {relative}: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _append(root, relative, text):
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _relink(root, entry, target):
    path = root / entry
    path.unlink()
    path.symlink_to(target)


def _copy_instead(root):
    path = root / contract.MIRROR / "beta"
    path.unlink()
    shutil.copytree(root / contract.SKILLS / "beta", path)


def _escape(root):
    outside = root.parent / "outside" / "beta"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text(SKILL.format(name="beta", description="Elsewhere."), encoding="utf-8")
    _relink(root, f"{contract.MIRROR}/beta", "../../../outside/beta")


BETA = f"{contract.SKILLS}/beta/SKILL.md"

PLANTS = {
    "missing-skill-file": lambda root: (root / BETA).unlink(),
    "name-mismatch": lambda root: _replace(root, BETA, "name: beta", "name: gamma"),
    "empty-description": lambda root: _replace(root, BETA, "description: Prove tests red first.", "description:"),
    "multi-line-description": lambda root: _replace(
        root, BETA, "description: Prove tests red first.", "description: >\n  Prove tests\n  red first."),
    "missing-mirror": lambda root: (root / contract.MIRROR / "beta").unlink(),
    "copy-instead-of-symlink": _copy_instead,
    "absolute-symlink": lambda root: _relink(root, f"{contract.MIRROR}/beta", root / contract.SKILLS / "beta"),
    "symlink-escapes-repository": _escape,
    "symlink-to-another-skill": lambda root: _relink(root, f"{contract.MIRROR}/beta", f"../../{contract.SKILLS}/alpha"),
    "orphan-mirror-entry": lambda root: (root / contract.MIRROR / "gamma").symlink_to(f"../../{contract.SKILLS}/gamma"),
    "dead-link-pointer": lambda root: _append(root, BETA, "\nSee [the missing guide](../../../docs/missing.md).\n"),
    "dead-backtick-pointer": lambda root: _append(root, contract.WORKFLOW, "\nThe runner is `scripts/missing.py`.\n"),
    "renamed-document": lambda root: (root / "docs/guide.md").rename(root / "docs/handbook.md"),
    "workflow-guidance-missing": lambda root: (root / contract.WORKFLOW).unlink(),
    "home-path": lambda root: _append(root, BETA, "\nNotes are in /home/someone/notes.md.\n"),
    "temp-path": lambda root: _append(root, contract.WORKFLOW, "\nEvidence goes to /tmp/run-1/evidence.json.\n"),
    "windows-mount-path": lambda root: _append(root, BETA, "\nThe checkout is /mnt/c/Users/someone/project.\n"),
    "session-identifier": lambda root: _append(
        root, BETA, "\nThis came from 9a50996e-2f97-42d3-9bba-ce28047e1daa.\n"),
    "session-line": lambda root: _append(root, contract.WORKFLOW, "\nClaude-Session: https://example.org/code/session_01ABC\n"),
    "foreign-email": lambda root: _append(root, BETA, "\nAsk someone@example.com.\n"),
    "broken-project-yml": lambda root: _replace(root, contract.SERENA, "ignored_paths: []", "ignored_paths: [one, two"),
    "wrong-language": lambda root: _replace(root, contract.SERENA, "\n- python\n", "\n- typescript\n"),
    "agent-interface-missing": lambda root: _replace(root, contract.SERENA, "agent_interface: tools\n", ""),
    "agent-interface-not-tools": lambda root: _replace(
        root, contract.SERENA, "agent_interface: tools", "agent_interface: repl"),
    "gitignore-not-honoured": lambda root: _replace(
        root, contract.SERENA, "ignore_all_files_in_gitignore: true", "ignore_all_files_in_gitignore: false"),
    "cache-not-ignored": lambda root: _replace(root, ".gitignore", ".serena/cache/\n", ""),
    "memories-not-ignored": lambda root: _replace(root, ".gitignore", ".serena/memories/\n", ""),
    "link-but-no-import": lambda root: _replace(root, contract.IMPORTER, "@AGENTS.md\n", ""),
    "section-missing": lambda root: _replace(root, contract.RULES, "## Agent workflow", "## Agent notes"),
    "section-without-skills": lambda root: _replace(
        root, contract.RULES, "Shared skills live in `.agents/skills`, mirrored", "Shared skills are mirrored"),
    "section-points-at-missing-document": lambda root: _replace(
        root, contract.RULES, "and `docs/guide.md`.", "and `docs/vanished.md`."),
}


def planted_tree(base, fault=None):
    """A healthy tree under ``base/repo``, with one planted fault when ``fault`` is named."""
    root = healthy_tree(Path(base) / "repo")
    if fault is not None:
        PLANTS[fault](root)
    return RepositoryView(root)
