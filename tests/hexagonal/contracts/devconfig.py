"""Independent expectations for the shared developer-settings layer, from its agreed behaviour.

Developer tools read optional local settings from the environment, a git-ignored
file at the main checkout, and a user-level file, all in one KEY=VALUE format.
Nothing here names a user, a machine or a personal layout; expected paths are
derived only from the HOME and XDG values a test sets and the main checkout's name.
"""

PREFIX = "XORCHESTRATE_DEV_"
NAMESPACE = "xorchestrate"
"""The settings' folder under the XDG base directories; with ``PREFIX``, the only place the name may appear."""
KINDS = frozenset({"str", "path", "int", "float", "command", "json"})
SOURCES = ("flag", "env", "repo_file", "user_file", "default")
"""Precedence, highest first."""
PATH_PLACEHOLDERS = frozenset({"XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "repo_name"})
COMMAND_PLACEHOLDERS = frozenset({"version", "context", "project", "port"})
REPO_FILE = ".devtools.env"
EXAMPLE_FILE = ".devtools.env.example"

CONSUMERS = ("scripts/devconfig/", "scripts/worktrees/", "scripts/testkit/")
"""The only source trees allowed to read developer settings; a new consumer is one entry here."""
CONSUMER_ROLES = frozenset({"devconfig", "worktrees_foundation", "worktrees_core", "worktrees_adapters",
                            "worktrees_composition", "worktrees_entrypoints", "test_support", "test_collection",
                            "testkit_configuration", "testkit_composition", "testkit_entrypoints"})
"""Architecture roles allowed to depend on the ``devconfig`` role."""

SERENA_COMMAND = ("uvx --python 3.12 --from serena-agent=={version} serena start-mcp-server --context {context}"
                  " --project {project} --agent-interface tools --transport streamable-http --host 127.0.0.1"
                  " --port {port} --open-web-dashboard false")

SLICE_A = {
    "XORCHESTRATE_DEV_WORKTREE_ROOT": ("path", "{XDG_DATA_HOME}/xorchestrate/worktrees/{repo_name}", "worktrees"),
    "XORCHESTRATE_DEV_STATE_DIR": ("path", "{XDG_STATE_HOME}/xorchestrate/worktrees", "worktrees"),
    "XORCHESTRATE_DEV_BASE": ("str", None, "worktrees"),
    "XORCHESTRATE_DEV_SERENA_VERSION": ("str", "2.1.0", "serena"),
    "XORCHESTRATE_DEV_SERENA_CONTEXT": ("str", "claude-code", "serena"),
    "XORCHESTRATE_DEV_SERENA_TIMEOUT": ("float", "180", "serena"),
    "XORCHESTRATE_DEV_SERENA_COMMAND": ("command", SERENA_COMMAND, "serena"),
    "XORCHESTRATE_DEV_PROBE_TOOL": ("str", None, "serena"),
    "XORCHESTRATE_DEV_PROBE_ARGS": ("json", None, "serena"),
}
"""Key -> (kind, default template, section) for slice A's settings."""

SLICE_C = {
    "XORCHESTRATE_DEV_TESTKIT_OUTPUT": ("path", "{XDG_STATE_HOME}/xorchestrate/runs/{repo_name}", "testkit"),
    "XORCHESTRATE_DEV_TESTKIT_PROFILE": ("path", None, "testkit"),
    "XORCHESTRATE_DEV_TESTKIT_INTERPRETER": ("path", None, "testkit"),
    "XORCHESTRATE_DEV_PYCACHE_PREFIX": ("path", None, "testkit"),
    "XORCHESTRATE_DEV_NATIVE_CONFIG": ("path", None, "testkit"),
    "XORCHESTRATE_DEV_TERMINAL_BINARY": ("path", None, "testkit"),
}
"""Key -> (kind, default template, section) for the test kit's settings (slice C)."""
EVIDENCE_KEYS = frozenset({"XORCHESTRATE_DEV_NATIVE_CONFIG", "XORCHESTRATE_DEV_TERMINAL_BINARY",
                           "XORCHESTRATE_DEV_TESTKIT_INTERPRETER", "XORCHESTRATE_DEV_TESTKIT_PROFILE"})
"""Settings that can change what a run proves (they select an executable or a configuration); every run
records them whatever their source."""
TESTKIT_SECTION = "testkit"
LEGACY_OUTPUT_NAME = "xorchestrate-runs"
"""The test kit's former output folder beside the checkout; no default points there any more."""

HOME_PATHS = ("/home/", "/Users/")
"""Absolute home-directory prefixes; no tool default, example or source names any one account's home."""


def xdg(env, name):
    """An XDG base directory from ``env``: the variable when set, else the specification's default under HOME."""
    fallback = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share", "XDG_STATE_HOME": ".local/state",
                "XDG_CACHE_HOME": ".cache"}[name]
    return env.get(name) or f"{env['HOME']}/{fallback}"


def resolve_default(template, env, repo_name):
    values = {name: xdg(env, name) for name in PATH_PLACEHOLDERS - {"repo_name"}}
    return template.format(repo_name=repo_name, **values)


def user_file(env):
    return f"{xdg(env, 'XDG_CONFIG_HOME')}/{NAMESPACE}/devtools.env"
