#!/usr/bin/env bash
# link-ops.sh — make this lane reachable from every sibling package clone
# without touching tracked files: an untracked `ops` symlink in each
# clone, excluded via .git/info/exclude (per-clone, shared by all of that
# clone's worktrees, never committed). Idempotent. Siblings are named by
# AGENTSCIENT_SIBLINGS (space-separated, under $AGENTSCIENT_ROOT); none
# declared is a declaration, not a failure.
set -uo pipefail
ROOT="${AGENTSCIENT_ROOT:-$HOME/projects/uscient}"
OPS="$ROOT/agentscient/ops"
[[ -d $OPS ]] || { echo "link-ops: refusal: expected ops at $OPS; found none; clone the constitutional repository first" >&2; exit 2; }
read -r -a SIBLINGS <<<"${AGENTSCIENT_SIBLINGS:-}"
(( ${#SIBLINGS[@]} )) || { echo "link-ops: no siblings declared (AGENTSCIENT_SIBLINGS empty) — nothing linked, by declaration"; exit 0; }
for r in "${SIBLINGS[@]}"; do
    d="$ROOT/$r"
    [[ -d $d/.git ]] || { echo "$r: not cloned, skipped"; continue; }
    ln -sfn "$OPS" "$d/ops"
    excl="$d/.git/info/exclude"
    grep -qxF '/ops' "$excl" 2>/dev/null || echo '/ops' >> "$excl"
    echo "$r: ops -> $OPS (excluded locally)"
done
