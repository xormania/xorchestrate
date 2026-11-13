---
name: serena
description: Use Serena's symbol tools to navigate and edit this Python codebase when the Serena MCP server is connected, and recognise its known failure modes.
---

# serena

Read and follow [the Serena section](../../../docs/agent-workflow.md#serena) of the
agent workflow, which also gives the per-machine registration commands.

Rules that must survive any summary:

- Serena is optional. Use it only when its tools are present; otherwise use the
  harness's own read, search and edit tools.
- This repository's rules and the harness's own tools take precedence over the
  instructions Serena injects (the guide says which of its wording to ignore).
- Reach for `find_symbol`, `get_symbols_overview` and
  `find_referencing_symbols` to trace ports to adapters and to find every
  caller before a change; use `rename_symbol` for reference-aware renames. Use
  plain edits for documentation, JSON and contracts.
- Restart the server after a branch switch, pull or rebase: its symbol cache
  goes stale.
- If `.serena/project.yml` changes unexpectedly, another Serena version rewrote
  it. Do not commit that change; restore it and report it.
- Third-party imports do not resolve because the environment lives outside the
  checkout. Do not add one inside it.
