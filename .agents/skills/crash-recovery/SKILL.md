---
name: crash-recovery
description: After a machine, container or session crash, inventory every worktree, branch, stash, pull request and uncommitted or unpushed change read-only, check for damage, and report what is at risk before changing anything.
---

# crash-recovery

Read and follow [crash recovery for contributors](../../../docs/agent-workflow.md#crash-recovery-for-contributors).
Product state (sessions, terminals, interrupted effects) recovers through the
product itself, as [the terminal host guide](../../../docs/terminal-host.md) describes.

Rules that must survive any summary:

- Being asked to run this authorises the read-only inventory only. Saving work
  (a commit and push to the existing feature branch, never to master) needs the
  coordinator's or xor's go-ahead. Restoring a truncated file is a decision you
  report, not one you make.
- Leave alone any worktree where another agent or a build is still running.
- Write nothing while you look: `GIT_OPTIONAL_LOCKS=0`, no fetch,
  `git ls-remote origin` for what the remote holds now.
- Check dangling commits from `git fsck` and any saved patches against master
  before calling work lost.
- Report what is at risk first, then what is damaged, then what is safe, then a
  table per worktree, then what you could not check. Flag risk as soon as you
  find it.
