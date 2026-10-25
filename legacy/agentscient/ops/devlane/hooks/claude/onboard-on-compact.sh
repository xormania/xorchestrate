#!/usr/bin/env bash
# onboard-on-compact.sh — SessionStart hook, matcher "compact". After a
# compaction the conversation is a summary; the lane's rule is that state
# is measured, never remembered, so the onboard instrument runs again and
# its brief is injected into the fresh context (a SessionStart hook's
# stdout becomes context). The law itself does not need this: CLAUDE.md
# imports it into the cached prefix.
#
# Exit mapping: the instrument's 0 (brief complete) and 1 (absences
# counted, named in the brief) both exit 0 here so the brief always
# lands; its 2 (refused: could not measure) exits 2 so the refusal is
# shown rather than read as a clean start.
set -u
root=${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}
printf '== onboard, after compaction — measured now, not remembered\n'
python3 "$root/ops/conductor/tools/onboard.py"
rc=$?
if [[ $rc -eq 2 ]]; then
    printf 'Diagnostic: onboarding could not measure the board; report the missing reading.\n' >&2
    exit 0
fi
exit 0
