"""The single declared table of developer settings, in display order.

Every consumer reads its settings from here; the example file is rendered from it.
"""

from __future__ import annotations

from scripts.devconfig.model import Setting

SERENA_COMMAND = (
    "uvx --python 3.12 --from serena-agent=={version} serena start-mcp-server --context {context}"
    " --project {project} --agent-interface tools --transport streamable-http --host 127.0.0.1"
    " --port {port} --open-web-dashboard false"
)

SETTINGS: tuple[Setting, ...] = (
    Setting("XORCHESTRATE_DEV_WORKTREE_ROOT", "path", "{XDG_DATA_HOME}/xorchestrate/worktrees/{repo_name}",
            "Directory that holds this repository's work trees, one per branch.", "worktrees"),
    Setting("XORCHESTRATE_DEV_STATE_DIR", "path", "{XDG_STATE_HOME}/xorchestrate/worktrees",
            "Directory for the work-tree tool's state, logs and its own Serena home.", "worktrees"),
    Setting("XORCHESTRATE_DEV_BASE", "str", None,
            "Ref new work trees start from; unset means the target of origin/HEAD.", "worktrees"),
    Setting("XORCHESTRATE_DEV_SERENA_VERSION", "str", "2.1.0",
            "Pinned serena-agent release used for per-tree servers.", "serena"),
    Setting("XORCHESTRATE_DEV_SERENA_CONTEXT", "str", "claude-code",
            "Serena context passed to per-tree servers.", "serena"),
    Setting("XORCHESTRATE_DEV_SERENA_TIMEOUT", "float", "180",
            "Seconds to wait for a per-tree server to answer a symbol lookup.", "serena"),
    Setting("XORCHESTRATE_DEV_SERENA_ALLOW_EDITS", "int", "0",
            "1 lets per-tree servers offer Serena's editing tools; anyone reaching the port could then edit "
            "the tree as you. Default 0: symbol navigation only.", "serena"),
    Setting("XORCHESTRATE_DEV_SERENA_COMMAND", "command", SERENA_COMMAND,
            "Command that starts a per-tree server; the tool fills the braced fields.", "serena"),
    Setting("XORCHESTRATE_DEV_PROBE_TOOL", "str", None,
            "Serena tool used as the readiness probe; unset derives one from the tree.", "serena"),
    Setting("XORCHESTRATE_DEV_PROBE_ARGS", "json", None,
            "JSON object of arguments for the readiness probe tool.", "serena", json_type=dict),
    Setting("XORCHESTRATE_DEV_SCRATCH_ROOT", "path", "{XDG_CACHE_HOME}/xorchestrate/scratch",
            "Directory for marked scratch copies made by scratch new.", "maintenance"),
    Setting("XORCHESTRATE_DEV_EXTRA_ROOTS", "json", None,
            "JSON list of other directories to report on, absolute or starting with ~/.", "maintenance",
            json_type=list),
    Setting("XORCHESTRATE_DEV_RUN_ROOTS", "json", None,
            "JSON list of directories holding test-kit run directories, absolute or starting with ~/.",
            "maintenance", json_type=list),
    Setting("XORCHESTRATE_DEV_IDLE_DAYS", "float", "7",
            "Days without use after which an unmerged tree needs a look.", "maintenance"),
    Setting("XORCHESTRATE_DEV_SCRATCH_MAX_AGE_HOURS", "float", "24",
            "Hours after which a marked scratch directory may be pruned.", "maintenance"),
    Setting("XORCHESTRATE_DEV_RUN_MAX_AGE_DAYS", "float", "7",
            "Days after which a recognised run directory may be pruned.", "maintenance"),
    Setting("XORCHESTRATE_DEV_BRIEF_SIZE_GB", "float", "5",
            "Total size in GiB above which the brief report speaks up.", "maintenance"),
    Setting("XORCHESTRATE_DEV_OWNER", "str", None,
            "Owner recorded in new scratch markers when --owner is not given.", "maintenance"),
    Setting("XORCHESTRATE_DEV_TESTKIT_OUTPUT", "path", "{XDG_STATE_HOME}/xorchestrate/runs/{repo_name}",
            "Directory for test-kit runs when neither --output nor the profile sets one.", "testkit"),
    Setting("XORCHESTRATE_DEV_TESTKIT_PROFILE", "path", None,
            "Operator profile used when no --profile or --from is given; recorded in runs.", "testkit",
            evidence=True, trust="config"),
    Setting("XORCHESTRATE_DEV_TESTKIT_INTERPRETER", "path", None,
            "Interpreter for fresh runs without a profile; symbolic links are kept; recorded in runs.",
            "testkit", evidence=True, trust="executable"),
    Setting("XORCHESTRATE_DEV_PYCACHE_PREFIX", "path", None,
            "Bytecode cache scripts/proof.sh uses when PYTHONPYCACHEPREFIX is unset; outside the checkout.",
            "testkit"),
    Setting("XORCHESTRATE_DEV_NATIVE_CONFIG", "path", None,
            "Native-mode configuration when neither --config nor the profile sets one; recorded in runs.",
            "testkit", evidence=True, trust="config"),
    Setting("XORCHESTRATE_DEV_TERMINAL_BINARY", "path", None,
            "Executable for the default terminal host when no host is selected; recorded in runs.",
            "testkit", evidence=True, trust="executable"),
)
