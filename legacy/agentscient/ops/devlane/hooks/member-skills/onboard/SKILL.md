---
name: onboard
description: Onboard an interactive Agentscient session using the shared project procedure and measured brief. Use at session start, after compaction, or when the project picture is stale. Not dispatched-worker initialization.
---

# Agentscient onboarding

This file is the entry point a codex or grok member finds in its private
home. The procedure itself is the project's, shared with every harness;
this is not a second workflow.

1. Resolve the current checkout root with `git rev-parse --show-toplevel`.
   Do not switch to another checkout to find onboarding instructions.
2. Check that `.claude/skills/onboard/SKILL.md` and
   `ops/conductor/tools/onboard.py` exist in that checkout. If either is
   absent, report it as expected, found and needed, and stop onboarding
   rather than inventing a brief.
3. Read `<checkout-root>/.claude/skills/onboard/SKILL.md` completely, then
   follow it from that checkout root. Its repository-relative references
   resolve there, not beside this file. The `.claude` path is where the
   shared procedure lives; it does not require the Claude harness.

Keep the shared procedure's required readings, exit handling and usage
telemetry. Attribute telemetry to this session's own agent identity, not
to Claude or to the harness name alone.

The brief is read-only; the procedure records skill-use telemetry
separately. Reading the brief does not claim work, enroll a participant,
issue a grant or create a dispatch job. A dispatched worker does not run
this procedure; its job brief and worker initialization govern it.
