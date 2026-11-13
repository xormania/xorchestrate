# Agent workflow

How to use Serena and other optional contributor tools on this repository.
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
