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
    Setting("XORCHESTRATE_DEV_SERENA_COMMAND", "command", SERENA_COMMAND,
            "Command that starts a per-tree server; the tool fills the braced fields.", "serena"),
    Setting("XORCHESTRATE_DEV_PROBE_TOOL", "str", None,
            "Serena tool used as the readiness probe; unset derives one from the tree.", "serena"),
    Setting("XORCHESTRATE_DEV_PROBE_ARGS", "json", None,
            "JSON object of arguments for the readiness probe tool.", "serena", json_type=dict),
    Setting("XORCHESTRATE_DEV_TESTKIT_OUTPUT", "path", "{XDG_STATE_HOME}/xorchestrate/runs/{repo_name}",
            "Directory for test-kit runs when neither --output nor the profile sets one.", "testkit"),
    Setting("XORCHESTRATE_DEV_TESTKIT_PROFILE", "path", None,
            "Operator profile used when no --profile or --from is given.", "testkit"),
    Setting("XORCHESTRATE_DEV_TESTKIT_INTERPRETER", "path", None,
            "Interpreter for fresh runs without a profile; symbolic links are kept.", "testkit"),
    Setting("XORCHESTRATE_DEV_PYCACHE_PREFIX", "path", None,
            "Bytecode cache scripts/proof.sh uses when PYTHONPYCACHEPREFIX is unset; outside the checkout.",
            "testkit"),
    Setting("XORCHESTRATE_DEV_NATIVE_CONFIG", "path", None,
            "Native-mode configuration when neither --config nor the profile sets one; recorded in runs.",
            "testkit", evidence=True),
    Setting("XORCHESTRATE_DEV_TERMINAL_BINARY", "path", None,
            "Executable for the default terminal host when no host is selected; recorded in runs.",
            "testkit", evidence=True),
)
