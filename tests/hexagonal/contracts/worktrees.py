"""Independent expectations for the optional work-tree tool, from its agreed behaviour.

The tool is a contributor convenience: it creates and removes git work trees
under a configurable root and runs one Serena MCP server per tree over local
HTTP. Its settings come from the shared developer-settings layer (see
``contracts.devconfig``); these expectations name no user, machine or layout.
"""

import re

TREE_REFUSALS = frozenset({"TREE_EXISTS", "BRANCH_EXISTS", "BAD_BRANCH", "BASE_MISSING", "BASE_UNRESOLVED",
                           "FETCH_FAILED", "NOT_A_TREE", "MAIN_CHECKOUT", "DIRTY", "LOCKED", "STASH", "IN_USE", "UNPUSHED"})
SERENA_CODES = frozenset({"SERENA_UNAVAILABLE", "NO_PROBE", "SERVER_FAILED", "NOT_UP"})
CHECK_CODES = frozenset({"NOT_UP", "NOT_ANSWERING", "PROJECT_MISMATCH", "MULTIPLE_SERVERS", "PROJECT_YML_CHANGED",
                         "STALE_HEAD", "VERSION_MISMATCH"})

DEFAULT_SERENA_VERSION = "2.1.0"
DEFAULT_SERENA_CONTEXT = "claude-code"
DEFAULT_TIMEOUT = 180
GRACE_SECONDS = 10
PORT_TRIES = 3
PROTOCOL_VERSION = "2025-06-18"
PROBE_TOOL = "get_symbols_overview"

TOOL_SOURCES = "scripts/worktrees/"
TOOL_ROLES = frozenset({"worktrees_foundation", "worktrees_core", "worktrees_adapters", "worktrees_composition",
                        "worktrees_entrypoints"})
TEST_ROLES = frozenset({"test_support", "test_collection"})


READONLY_TOOLS = frozenset({"find_symbol", "get_symbols_overview", "find_referencing_symbols", "find_implementations",
                            "find_declaration", "get_diagnostics_for_file", "get_diagnostics_for_symbol"})
"""The only tools a per-tree server may offer unless edits are allowed: symbol lookups and diagnostics."""
READONLY_CONTEXT = "worktrees-readonly.yml"
"""The tool-owned Serena context file, under ``<state dir>/serena-home/contexts/``."""
LOOPBACK = "127.0.0.1"


def serena_argv(version, context, project, port):
    """The documented default launch command, with the placeholders filled."""
    return ["uvx", "--python", "3.12", "--from", f"serena-agent=={version}", "serena", "start-mcp-server",
            "--context", context, "--project", str(project), "--agent-interface", "tools",
            "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(port),
            "--open-web-dashboard", "false"]


FORCING_WORDS = frozenset({"--force", "-f", "-D", "--force-with-lease", "--hard"})


def forced_git_calls(calls):
    """Recorded git argument lists that force, discard or prune work-tree metadata; the tool never does any."""
    offending = []
    for args in calls:
        words = [str(word) for word in args]
        clusters = [word for word in words if re.fullmatch(r"-[A-Za-z]{2,}", word) and set(word[1:]) & {"f", "D"}]
        if FORCING_WORDS.intersection(words) or clusters or words[:2] == ["worktree", "prune"]:
            offending.append(words)
    return offending


def codes_in(text, codes):
    """The codes from ``codes`` that appear as whole words in ``text``."""
    return {code for code in codes if re.search(rf"(?<![A-Z_]){code}(?![A-Z_])", text)}


VERDICTS = ("MAIN", "NEEDS_LOOK", "REMOVABLE", "KEEP")
REASONS = frozenset({"MISSING_DIR", "ORPHAN_DIR", "DIRTY", "UNPUSHED", "STASH", "LOCKED", "IN_USE_MERGED", "IDLE"})
TREE_KEYS = frozenset({"path", "branch", "head", "last_commit_age_s", "last_use", "size", "merged", "on_origin",
                       "clean", "stash", "locked", "in_use", "serena", "verdict", "reasons"})
SIZE_KEYS = frozenset({"total", "serena", "venv", "build"})
DIR_KEYS = frozenset({"path", "kind", "size", "age_s", "marked", "recognised", "in_use", "expired"})
TOTAL_KEYS = frozenset({"size", "removable", "needs_look", "expired"})
DIR_KINDS = frozenset({"scratch", "extra", "run"})
SCRATCH_MARK = ".scratch.json"
RUN_MARK = "plan.json"
LABEL = r"[A-Za-z0-9][A-Za-z0-9._-]*"
STAMP = r"\d{8}T\d{6}Z"
DAY = 86_400
