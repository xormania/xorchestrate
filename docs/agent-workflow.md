# Agent workflow

How agents work on this repository together, how a contributor recovers after a
crash, and how to use Serena here. [AGENTS.md](../AGENTS.md) carries the
repository's rules and takes precedence over this document; xor's current
direction takes precedence over both.

The shared skills in `.agents/skills` point at this document and at the guides
it names. Codex reads that folder (`codex debug prompt-input` lists all six
skills without a model call). Claude Code sees each skill through a relative
symlink in `.claude/skills`. Grok reads both folders, but only in a trusted
folder: untrusted, it shows none of the skills and no instruction files;
trusted (`--trust`, or `/hooks-trust` in a session), `grok inspect` lists all
six skills and both instruction files. This document and the guides stay the
source of truth, so a harness that loads no skills still gets the same rules.

## Team roles

A single contributor works through the whole change alone, following
[the PR quality standard](pr-quality.md): tests that prove detection, the
implementation, the pre-draft review and the draft. Nothing in this section
restricts that.

When work is split across several agent sessions, these roles apply. They stay
the same however many sessions there are and whatever they are called:

- **Coordinator.** Writes the behaviour specification, routes work, and makes
  design and scope decisions; in a split team it is the role that takes those
  questions to xor. Keeps the current roster of sessions; nothing in the
  repository names sessions.
- **Test writer.** Writes the tests and proves they fail for the intended
  reason before the implementation or fix exists; for a fix, red against the
  defective code ([Prove detection](pr-quality.md#prove-detection)).
- **Implementer.** Writes the implementation or fix that turns those tests
  green. The PR quality standard asks for separate implementation and test
  authors when work is delegated, so in a split team the implementer does not
  edit tests: a test that looks wrong goes to the coordinator, who takes it to
  the test writer.
- **Lookup.** Answers specific questions about where something lives or how an
  existing mechanism works, with file and line references. Read-only.

Default ownership of paths in a split team. The coordinator may change it per
task:

| Role | Owns |
|---|---|
| Test writer | `tests/` and `contracts/testing.json` |
| Implementer | `xorchestrate/`, `scripts/testkit/`, `scripts/acceptance/`, `docs/`, `contracts/hexagonal.json` and `CHANGELOG.md` |

Working rules for a split team:

- A direct message between two sessions can speed up a question, but it never
  settles a design or scope question; that goes through the coordinator.
- One committer per branch, normally the implementer. The test writer's tests
  go in unchanged as their own commit before the implementation commit, so the
  history shows red before green.
- One writer per file. A shared note is edited by its own section, never
  rewritten wholesale.

For everyone, alone or in a team:

- Commit work in progress at every checkpoint and push the branch; a pushed
  draft branch is the recovery mechanism. Keep anything you will need later
  outside temporary directories, and keep private notes out of the repository.
- Pull requests are drafts; xor merges. Never force-push or rewrite published
  history. Attribution and publication follow AGENTS.md and the PR quality
  standard.

## Crash recovery for contributors

After a machine, container or session crash, first find out what is at risk,
read-only, and report it before changing anything. Product state (sessions,
terminals, the effect journal) has its own recovery, described in
[recovering after a container or machine failure](terminal-host.md#recovering-after-a-container-or-machine-failure);
this section is about the work in the repository.

Being asked to run a recovery check authorises the read-only inventory only.
Saving work (a commit and push to the existing feature branch, never to master)
is a separate step that needs the go-ahead of whoever directs the work (the
coordinator in a split team, otherwise xor). Restoring a truncated file from a
commit is a decision you report, not one you make.

1. **Check for live work.** List the running agent and build processes and the
   other agent sessions. Leave alone any worktree where something is still
   running, and say so in the report.
2. **Write nothing while you look.** Set `GIT_OPTIONAL_LOCKS=0` so `git status`
   does not refresh the index, do not fetch, and use `git ls-remote origin` for
   what the remote holds now. Do not switch, check out, reset, stash, clean,
   commit, push, prune or repair until the inventory is complete.
3. **Inventory every worktree** that `git worktree list` shows, and every other
   clone you work in: branch, HEAD (hash, date, subject), upstream and
   ahead/behind, whether the branch still exists on the remote,
   `git status --porcelain --untracked-files=all`, `git stash list`, commits
   on no remote branch (`git log --branches --not --remotes`), and files changed
   in the hours before the crash that are not committed.
4. **Look for damage.** Leftover `*.lock` files in the git directories, a
   `git status` or `git log` that errors, zero-length or NUL-filled tracked
   files (a clean status rules these out for tracked files),
   `git fsck --connectivity-only`, and each `.xorchestrate/state.sqlite3`
   opened read-only with a `PRAGMA quick_check`.
5. **Find loose work.** Dropped stashes and amended commits appear as dangling
   commits in `git fsck`. For each one, and for any patch saved before the
   crash, check whether its change is already in master without touching the
   working tree or the real index, using a temporary index:

   ```bash
   index="$(mktemp -d)/index"
   GIT_INDEX_FILE="$index" git read-tree <master ref>
   GIT_INDEX_FILE="$index" git apply --cached --check -R <patch file>   # succeeds: already in master
   rm -r "$(dirname "$index")"
   ```

   For a dangling commit, make the patch with `git diff <commit>^ <commit>`.
   When the check fails, later commits have often rewritten the same lines:
   compare which of the lines the patch adds are present in master, and say
   whether the remainder looks superseded or lost.
6. **Check open pull requests** with `gh pr list` and `gh pr checks <number>`.
   A skipped check has not passed. Note whether any local branch holds work
   beyond a pull request's head.
7. **Product state.** With no server running, `xorchestrate doctor` reports,
   read-only, whether the last server stopped cleanly and which sessions are
   persisted as live, but only for an approved configuration with a built-in
   store adapter; otherwise it constructs nothing and says so, which is not a
   clean bill of health. Starting the server then recovers sessions under the
   project's recovery policy; `xorchestrate status` and `xorchestrate recover`
   follow [the terminal host guide](terminal-host.md).

Report in this order: what is at risk (uncommitted or unpushed, with paths),
what is damaged, what is safe; then a table per worktree; then what you could
not check. Flag anything at risk as soon as you find it rather than waiting for
the full sweep. The checks cannot see damage to an ignored file, a truncated
untracked file that is not empty, or work that existed only in a lost session's
memory.

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
