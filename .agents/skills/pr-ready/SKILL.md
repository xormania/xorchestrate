---
name: pr-ready
description: Prepare and publish a pull request to this repository's standard, from coverage map and red/green proof to the pre-draft review, draft-only publication and xormania-only attribution.
---

# pr-ready

Read and follow [the PR quality standard](../../../docs/pr-quality.md) and the
attribution rule in [AGENTS.md](../../../AGENTS.md). They are the source of truth;
this skill repeats what must survive a summary.

- Follow the standard's review and delivery steps in order: plan, implement and
  verify, a pre-draft adversarial review, fix warranted findings, open a draft,
  hand it to xor.
- Attribution: commits are authored as given in AGENTS.md. No co-author
  trailers, session lines or links, or "Generated with" text anywhere: commits,
  pull request descriptions, comments, code or documentation.
- Before publishing, read every commit message and the pull request description
  for local paths, usernames, host names and session identifiers.
- Draft pull requests only; xor merges. Never force-push or rewrite published
  history; update with ordinary commits or a merge commit.
- Read CI with `gh pr checks`. A skipped check has not passed.
- Add user-visible changes to the Unreleased entry of `CHANGELOG.md`.
