#!/usr/bin/env bash
# board.sh — the Agentscient board, measured at the moment of asking.
# Read-only. Lists, per repository: open PRs, live worktrees; then the
# dispatch log tail. Nothing here is cached or remembered — rerun it
# rather than trusting an old copy. Sibling package repositories join
# the list through AGENTSCIENT_SIBLINGS (space-separated names under
# $AGENTSCIENT_ROOT) as they are founded.
set -uo pipefail
ROOT="${AGENTSCIENT_ROOT:-$HOME/projects/uscient}"
ORG="${AGENTSCIENT_ORG:-uscient}"
read -r -a SIBLINGS <<<"${AGENTSCIENT_SIBLINGS:-}"
REPOS=(agentscient "${SIBLINGS[@]}")
echo "== $ORG board — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
for r in "${REPOS[@]}"; do
    d="$ROOT/$r"
    [[ -d $d/.git ]] || { echo "-- $r: NOT CLONED"; continue; }
    def=$(git -C "$d" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|origin/||')
    echo "-- $r (default: ${def:-unknown})"
    gh pr list -R "$ORG/$r" --state open --json number,title,isDraft,headRefName \
        --jq '.[] | "   PR #\(.number)\(if .isDraft then " [draft]" else "" end) \(.headRefName): \(.title)"' 2>/dev/null \
        || echo "   PRs: UNREACHABLE (gh failed — not zero, unknown)"
    git -C "$d" worktree list | sed 's/^/   wt /'
done
LOG="$ROOT/agentscient/ops/dispatches/log.tsv"
echo "== dispatches (newest last)"
if [[ -f $LOG ]]; then tail -5 "$LOG" | sed 's/^/   /'; else echo "   none recorded"; fi
