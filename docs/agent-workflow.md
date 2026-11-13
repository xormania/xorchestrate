# Agent workflow

How developer tools read optional local settings, and how to use Serena here.
[AGENTS.md](../AGENTS.md) carries the
repository's rules and takes precedence over this document; xor's current
direction takes precedence over both.

The shared skills in `.agents/skills` point at this document and at the guides
it names. Codex reads that folder (`codex debug prompt-input` lists all four
skills without a model call). Claude Code sees each skill through a relative
symlink in `.claude/skills`. Grok reads both folders, but only in a trusted
folder: untrusted, it shows none of the skills and no instruction files;
trusted (`--trust`, or `/hooks-trust` in a session), `grok inspect` lists all
four skills and both instruction files. This document and the guides stay the
source of truth, so a harness that loads no skills still gets the same rules.

## Developer settings

Developer tools in `scripts/` that opt in read optional local settings through
`scripts.devconfig`. Nothing requires them: every setting has a neutral default
derived from your home and XDG directories and the checkout's name. The product
never reads them, and checks run in CI do not depend on them.

Each setting is a `XORCHESTRATE_DEV_*` name, the same in the environment and in
the files. A value is taken from the first of these that sets it:

1. a command-line flag of the tool, where it offers one;
2. the environment;
3. `.devtools.env` in the main checkout (git-ignored; linked work trees read the
   main checkout's file, never one of their own);
4. `devtools.env` in the `xorchestrate` folder under your XDG config directory;
5. the declared default.

The files hold plain `KEY=VALUE` lines, `#` comments and blank lines, in UTF-8.
Values are literal text: nothing is expanded or executed. Spaces and tabs around
the name and the value are trimmed and one matching pair of surrounding quotes
is removed. A path may start with `~` or `~/` (your home from `HOME`), and a
relative path resolves against the file's folder, or the current directory for
the environment and flags. An empty value counts as not set, so the next source
applies. Any other line is refused with its file and line number, and so is a
`.devtools.env` that git tracks. An unknown name is ignored and reported by
`show`. Keep secrets out of these files.

```bash
python3 -m scripts.devconfig show              # every setting, its value and where it came from
python3 -m scripts.devconfig example --check   # is this tree's .devtools.env.example current?
python3 -m scripts.devconfig example --write   # regenerate it in this tree
```

All settings are declared once, with kind, default and description, in
`scripts/devconfig/keys.py`. The committed `.devtools.env.example` is generated
from that table, and a test fails when the two differ. Add a setting to the
table only for a location, an external tool's launcher or pin, or a
machine-dependent number for local runs; a setting that could change what a
check proves is either not offered or is marked so that runs record it.

## Serena

[Serena](https://github.com/oraios/serena) is an MCP server that gives an agent
symbol-level navigation and editing through a language server. It is optional:
use it when it is connected and a task needs symbols (tracing a port to its
adapters, finding every caller before a change, a reference-aware rename);
otherwise use the harness's own read, search and edit tools.

This repository's rules and the harness's own tools take precedence over the
instructions manual Serena injects. Its wording that "forbids" the harness's
Read and Edit tools does not apply here.

### Project configuration

`.serena/project.yml` is the file Serena 2.1.0 generates for this repository,
with these settings changed:

- `language_servers` selects `python` (pyright).
- `agent_interface: tools` exposes Serena's tools (`find_symbol`,
  `get_symbols_overview`, `find_referencing_symbols`, `rename_symbol` and the
  others) directly, instead of Serena 2's default REPL interface, so every
  harness can discover them by name.
- `ignore_all_files_in_gitignore` is on, and the archived documents, the
  evidence directories and the test fixtures are not indexed.
- `initial_prompt` points at AGENTS.md and this document, and repeats that
  Serena is optional.

`.serena/.gitignore` is tracked exactly as Serena writes it on first start, and
the root `.gitignore` also keeps Serena's per-machine cache, memories and local
overrides (`project.local.yml`) out of the repository, so connecting Serena
leaves the working tree clean.

### Connecting Serena

Registration is per machine and nothing in the repository launches Serena:
there is no committed MCP configuration, because a committed launch entry would
also start in cloud sessions where Serena has not been shown to work. Register
the pinned release once with each harness you use:

| Harness | Register once | Scope |
|---|---|---|
| Claude Code | `claude mcp add --scope user serena -- uvx --python 3.12 --from serena-agent==2.1.0 serena start-mcp-server --context claude-code --project-from-cwd --open-web-dashboard false` | All projects for your user. Without `--scope user`, Claude Code registers for the current project path only. |
| Codex | `codex mcp add serena -- uvx --python 3.12 --from serena-agent==2.1.0 serena start-mcp-server --context codex --project-from-cwd --open-web-dashboard false` | Global (your Codex configuration). |
| Grok | No separate registration observed: Grok picked up Claude Code's registration. | Serena's dedicated `grok` context exists but is untested. |

Each command was checked by launching the server it registers against Serena
2.1.0, not through the harness's own client. `--project-from-cwd` finds the
repository from the directory the server starts in, so one registration serves
every worktree as long as the harness starts the server there.

Codex waits 10 seconds for an MCP server to start by default, and
`codex mcp add` cannot change that. The first start downloads Serena and
possibly a Python before pyright starts, so either add
`startup_timeout_sec = 60` under `[mcp_servers.serena]` in your Codex
configuration, or warm the cache once by running the registered command
yourself. The actual first-start time has not been measured.

Pin the same release everywhere: a server of a different version can rewrite
`.serena/project.yml` from its own template.

At its first start the command downloads serena-agent and its dependencies from
PyPI and, if missing, a uv-managed Python 3.12. Serena then starts pyright
itself through uvx (pyright 1.1.403 under a uv-managed Python 3.13). Logs and
the project registration go to Serena's home directory (`SERENA_HOME`, by
default in your home), never into the repository.

### Known limits and failure modes

- Pyright runs under Python 3.13, so it assumes 3.13 semantics; the
  repository's baseline is 3.12. Navigation is unaffected.
- Imports of third-party packages (for example jsonschema or xcontracts) do not
  resolve, because the project environment lives outside the checkout (see
  [the testing framework](testing-framework.md)). Symbols and references inside
  the repository resolve normally. Do not add an environment inside the
  checkout to change this.
- After a branch switch, pull or rebase, restart the server: its symbol cache
  goes stale and can report symbols that no longer exist.
- If `.serena/project.yml` changes unexpectedly, a Serena of another version
  rewrote it. Do not commit the change; restore the file from the branch and
  report it.
- If the server starts outside a repository, `--project-from-cwd` activates no
  project and only Serena's REPL tools appear. Register with
  `--project <checkout>` instead for that harness.
- Unverified: whether pyright starts on a machine without Node on its path, and
  whether each harness starts MCP servers in the session's directory.

## Work trees and per-tree Serena (optional tool)

`python3 -m scripts.worktrees` is an optional convenience for contributors who
work in several git work trees at once. Nothing in the repository requires it.
It reads its settings through the [developer settings](#developer-settings);
`--root` and `--state-dir` override the work-tree root and the tool's state
directory for one command, and `--repo` names the repository (by default the
one containing the current directory; inside a linked tree it means the main
checkout).

```bash
python3 -m scripts.worktrees new feat/example          # tree at <root>/feat/example, from the remote's default branch
python3 -m scripts.worktrees remove feat/example       # only when clean, unlocked, unused and pushed or merged
python3 -m scripts.worktrees serena up feat/example    # one Serena server for that tree
python3 -m scripts.worktrees serena call feat/example find_symbol '{"name_path_pattern": "TerminalHost"}'
python3 -m scripts.worktrees serena check feat/example # before trusting its answers
python3 -m scripts.worktrees serena down feat/example
```

`new` starts the branch from the remote's default branch (or
`XORCHESTRATE_DEV_BASE`, or `--base`) with no upstream, after a fetch. It
refuses, changing nothing, when the directory or branch exists or the base
cannot be resolved. `remove` refuses a tree that has uncommitted or untracked
changes, is locked, has a stash entry made on its branch, has a process working
in it, or holds commits that are neither pushed nor merged. It never forces;
it deletes the branch only when merged into the base, and only if the branch
still points where it did.

A session's own Serena connection serves the directory the session started in.
`serena` gives any session symbol tools for another tree without restarting:
`up` starts one Serena MCP server per tree over local HTTP, with a Serena home
in the tool's state directory, so your own Serena configuration is untouched.
A per-tree server is read-only: it offers only Serena's symbol and diagnostic
tools (no editing, file-reading or shell tools), `up` refuses a server that
offers more, and edits in a tree go through your ordinary tools. Setting
`XORCHESTRATE_DEV_SERENA_ALLOW_EDITS=1` restores Serena's full tool set.
Servers listen on 127.0.0.1 on a port the kernel picks, and Serena's HTTP
transport has no authentication: on a machine shared with other accounts, any
local account can reach a running server while it runs, so it can read the
tree's symbols, and with edits allowed it could edit the tree as you.
A server counts as up only after it answers a real symbol query (by default an
overview of the first tracked Python file that defines something; set
`XORCHESTRATE_DEV_PROBE_TOOL` and `XORCHESTRATE_DEV_PROBE_ARGS` to choose
another). `call` and `tools` use it from the shell, `ls` lists servers, `reap`
forgets dead ones and stops servers whose tree is gone, and `down` stops a
server and its language server. `check` reports a server that does not answer,
serves another directory, has a twin, runs a different Serena version, or
predates a commit or a change to `.serena/project.yml`; restart it with
`serena up` after a branch switch or pull. Without Serena's launcher, `serena`
says so and the tree commands still work; `XORCHESTRATE_DEV_SERENA_COMMAND`
replaces the launch command.

### Keeping trees and scratch space in check

Work trees, clean copies and run directories pile up. `report` shows what is
there and what can go; `prune` removes only what it can prove safe.

```bash
python3 -m scripts.worktrees report             # every tree and scratch, run or extra directory
python3 -m scripts.worktrees report --brief     # one line when something needs attention, else nothing
python3 -m scripts.worktrees prune              # what prune would do; changes nothing
python3 -m scripts.worktrees prune --apply
python3 -m scripts.worktrees scratch new copy   # a marked scratch directory for a clean copy; prints its path
python3 -m scripts.worktrees scratch rm <path>
```

`report` judges each tree: `REMOVABLE` when it holds commits of its own that
are merged into the remote's default branch (or a pull request from it was
merged there), it is clean, has no stash, is unlocked and nothing works in it;
`NEEDS_LOOK` with the reason when it is dirty, unpushed, stashed, locked,
merged but in use, idle for `XORCHESTRATE_DEV_IDLE_DAYS`, or registered without
its directory, and for any unregistered checkout under the root; `KEEP`
otherwise, including a fresh tree with no commits yet. `--offline` skips the
GitHub lookup; `--brief` is always offline, never fails, and counts only
reclaimable space, so a session-start hook can call it.

`prune --apply` fetches, re-checks each removable tree just before removing it
the same way `remove` does, drops a missing tree's registration only when it is
unlocked and its branch tip is on its origin branch (after saving a backup),
stops servers whose tree is gone, and deletes marked scratch directories older
than `XORCHESTRATE_DEV_SCRATCH_MAX_AGE_HOURS` and run directories (holding a
`plan.json`, not a git checkout) under `XORCHESTRATE_DEV_RUN_ROOTS` older than
`XORCHESTRATE_DEV_RUN_MAX_AGE_DAYS`, when nothing uses them. It never deletes
an orphan checkout, an unmarked entry or anything under
`XORCHESTRATE_DEV_EXTRA_ROOTS`, which are listed for information only. A root
that is `/`, your home directory, or overlaps a checkout or the tool's state is
refused.
