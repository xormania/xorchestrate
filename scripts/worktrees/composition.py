"""Build the tool's services from developer settings and ports; real adapters fill any port not given."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.devconfig.loader import find_main_checkout, load
from scripts.worktrees.adapters.clock import SystemClock
from scripts.worktrees.adapters.files import LocalFiles
from scripts.worktrees.adapters.git import SubprocessGit
from scripts.worktrees.adapters.launcher import PopenLauncher
from scripts.worktrees.adapters.mcp import UrllibMcp
from scripts.worktrees.adapters.net import free_port
from scripts.worktrees.adapters.procfs import ProcFs
from scripts.worktrees.adapters.state import JsonStateStore
from scripts.worktrees.core import Repository, SerenaService, TreeService
from scripts.worktrees.model import Config

ROOT_KEY = "XORCHESTRATE_DEV_WORKTREE_ROOT"
STATE_KEY = "XORCHESTRATE_DEV_STATE_DIR"
STORE_SUBDIR = "servers"


@dataclass
class Ports:
    git: Any = None
    processes: Any = None
    launcher: Any = None
    transport: Any = None
    clock: Any = None
    store: Any = None
    files: Any = None
    free_port: Any = None


@dataclass
class Services:
    config: Config
    trees: TreeService
    serena: SerenaService


def build(repo: Path, env: Mapping[str, str], flags: Mapping[str, str | None], ports: Ports | None = None) -> Services:
    ports = ports or Ports()
    git = ports.git or SubprocessGit()
    main = find_main_checkout(repo, git=lambda args, cwd: git.run(args, str(cwd)))
    settings = load(main, env=env, flags=flags, git=lambda args, cwd: git.run(args, str(cwd)))
    config = Config(
        root=str(settings.get(ROOT_KEY)), state_dir=str(settings.get(STATE_KEY)),
        base=settings.get("XORCHESTRATE_DEV_BASE"),
        serena_version=settings.get("XORCHESTRATE_DEV_SERENA_VERSION"),
        serena_context=settings.get("XORCHESTRATE_DEV_SERENA_CONTEXT"),
        serena_timeout=settings.get("XORCHESTRATE_DEV_SERENA_TIMEOUT"),
        serena_command=tuple(settings.get("XORCHESTRATE_DEV_SERENA_COMMAND")),
        probe_tool=settings.get("XORCHESTRATE_DEV_PROBE_TOOL"),
        probe_args=settings.get("XORCHESTRATE_DEV_PROBE_ARGS"))
    processes = ports.processes or ProcFs()
    store = ports.store or JsonStateStore(Path(config.state_dir) / STORE_SUBDIR)
    files = ports.files or LocalFiles()
    repository = Repository(git, str(main))
    serena = SerenaService(config, repository, processes, ports.launcher or PopenLauncher(),
                           ports.transport or UrllibMcp(), ports.clock or SystemClock(), store, files,
                           ports.free_port or free_port, env)
    return Services(config, TreeService(config, repository, processes, store, files, serena), serena)
