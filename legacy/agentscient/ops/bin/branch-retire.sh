#!/usr/bin/env bash
# branch-retire.sh — archive branches before retiring them, and prove the archive
# restores before anything is deleted.
#
# Three steps, each its own run, each refusing without the one before it:
#
#   --plan      classify every branch on the remote and write the plan (default)
#   --archive   bundle every ref, restore the bundle into a throwaway repository,
#               compare every tip, and write the index the repository keeps
#   --retire    delete the planned branches, re-reading each tip immediately
#               before, skipping any that moved, recording every outcome
#
# A branch is planned for retirement only when a reading says nothing is lost:
# its tip is inside dev, or its commits are dispatch records, or dev already has
# an equivalent patch, or dev's content already matches it, or the only files it
# adds are session records under .dev/. A branch holding code dev lacks is kept
# unless --include-code is passed. The trunk, the release branch, any branch with an open pull
# request, any branch the deliverable lock names, and any branch a worktree has
# checked out are never planned.
#
# Nothing is deleted except by --retire, and --retire refuses unless the archive
# verified. Exit: 0 clean, 1 findings kept for a human, 2 refusal.
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) \
  || { printf 'root: expected the repository root above this lever; found dirname failed; needed run the lever from its checkout\n' >&2; exit 2; }
# LANE_NAME, TRUNK_BRANCH and RELEASE_BRANCH come from the shared conf, never
# from a literal in a lever (laneconf.py literals).
# shellcheck source=../lane.conf
. "$REPO/ops/lane.conf"
REMOTE='origin'
WORKDIR='/home/scientdb/wt-archive'
LOCK="${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/deliverable.json"
TRUNK="$TRUNK_BRANCH"
RELEASE="$RELEASE_BRANCH"
mode='plan'; include_code=0; index=''; do_fetch=1

refuse() { printf '%s: expected %s; found %s; needed %s\n' "$1" "$2" "$3" "$4" >&2; exit 2; }
usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --plan) mode=plan; shift;;
    --archive) mode=archive; shift;;
    --retire) mode=retire; shift;;
    --include-code) include_code=1; shift;;
    --index) index="${2-}"; shift 2;;
    --repo) REPO="${2-}"; shift 2;;
    --remote) REMOTE="${2-}"; shift 2;;
    --workdir) WORKDIR="${2-}"; shift 2;;
    --lock) LOCK="${2-}"; shift 2;;
    --no-fetch) do_fetch=0; shift;;
    -h|--help) usage; exit 0;;
    *) refuse arguments 'a flag this script declares' "$1" 'run --help and pass one of the declared flags';;
  esac
done

top=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) \
  || refuse repo 'a git repository' "$REPO" 'pass --repo PATH'
REPO="$top"
mkdir -p "$WORKDIR" || refuse workdir 'a writable working directory' "cannot create $WORKDIR" 'pass --workdir PATH'
PLAN="$WORKDIR/branch-plan.tsv"

if [ "$do_fetch" = 1 ]; then
  git -C "$REPO" fetch --quiet --prune "$REMOTE" 2>/dev/null \
    || refuse remote "a fetch from $REMOTE" 'the fetch failed' 'fix the remote, or pass --no-fetch and accept stale refs'
fi

# ---------------------------------------------------------------- plan
plan() {
  [ -f "$LOCK" ] || refuse lock 'the deliverable lock, to see which branches are live' \
    "no file at $LOCK" 'pass --lock PATH'
  local tmp; tmp=$(mktemp -d) || refuse scratch 'a temporary directory' 'mktemp failed' 'free space in TMPDIR'
  trap 'rm -rf "$tmp"' RETURN

  git -C "$REPO" ls-tree -r --name-only "$REMOTE/$TRUNK" > "$tmp/devfiles" \
    || refuse trunk "a readable $REMOTE/$TRUNK" 'could not list its tree' 'fetch the remote and try again'
  gh pr list --repo "$(git -C "$REPO" remote get-url "$REMOTE" | sed -E 's#.*[:/]([^/]+/[^/.]+)(\.git)?$#\1#')" \
     --state open --limit 500 --json headRefName --jq '.[].headRefName' > "$tmp/openpr" 2>/dev/null \
    || refuse pullrequests 'the open pull requests, so a live head is never planned' \
       'gh could not list them' 'authenticate gh, or fix the remote name'
  git -C "$REPO" worktree list --porcelain | awk '/^branch /{sub("refs/heads/","",$2); print $2}' > "$tmp/checkedout"

  : > "$PLAN"
  printf '# branch retirement plan, %s, %s at %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$REMOTE/$TRUNK" "$(git -C "$REPO" rev-parse "$REMOTE/$TRUNK")" >> "$PLAN"
  printf '# name\tsha\tclass\tverdict\treason\n' >> "$PLAN"

  local n=0
  while read -r ref; do
    local name sha
    # refs/remotes/<remote>/HEAD shortens to the bare remote name: it is a
    # symbolic pointer, not a branch, and never belongs in a plan.
    [ "$ref" = "$REMOTE" ] && continue
    name=${ref#"$REMOTE"/}
    [ "$name" = HEAD ] && continue
    sha=$(git -C "$REPO" rev-parse "$ref")
    n=$((n+1))
    local class verdict reason
    if [ "$name" = "$TRUNK" ] || [ "$name" = "$RELEASE" ]; then
      class=permanent; verdict=KEEP; reason='the trunk or the release'
    elif grep -qxF "$name" "$tmp/openpr"; then
      class=open-pr; verdict=KEEP; reason='an open pull request heads here'
    elif grep -qxF "$name" "$tmp/checkedout"; then
      class=checked-out; verdict=KEEP; reason='a worktree has it checked out'
    elif grep -qF "\"$name\"" "$LOCK"; then
      class=live-session; verdict=KEEP; reason='the deliverable lock names it'
    elif [ "$(git -C "$REPO" rev-list --count "$REMOTE/$TRUNK..$ref")" = 0 ]; then
      class=in-dev; verdict=RETIRE; reason='every commit is already on the trunk'
    elif [ -z "$(git -C "$REPO" merge-base "$REMOTE/$TRUNK" "$ref" 2>/dev/null)" ]; then
      # No merge base: an unrelated history. Every diff against the trunk is
      # meaningless here, so it is never planned by a reading this script can take.
      class=unrelated-history; verdict=KEEP; reason='no merge base with the trunk'
    else
      local subs cherry_new files differing absent
      subs=$(git -C "$REPO" log --format=%s "$ref" --not "$REMOTE/$TRUNK")
      cherry_new=$(git -C "$REPO" cherry "$REMOTE/$TRUNK" "$ref" | grep -c '^+')
      files=$(git -C "$REPO" diff --name-only "$REMOTE/$TRUNK...$ref")
      if [ -n "$subs" ] && [ -z "$(printf '%s\n' "$subs" | grep -v '^dispatch: record')" ]; then
        class=dispatch-records; verdict=RETIRE; reason='every commit is a dispatch record the store also holds'
      elif [ "$cherry_new" = 0 ]; then
        class=patch-equivalent; verdict=RETIRE; reason='the trunk has an equivalent patch for every commit'
      elif [ -z "$files" ]; then
        class=no-file-change; verdict=RETIRE; reason='the commits change no file'
      else
        differing=$(printf '%s\n' "$files" | while read -r f; do
          [ -n "$f" ] || continue
          git -C "$REPO" diff --quiet "$REMOTE/$TRUNK" "$ref" -- "$f" || printf '%s\n' "$f"; done)
        if [ -z "$differing" ]; then
          class=content-in-dev; verdict=RETIRE; reason="the trunk's content already matches every file it touches"
        elif [ -z "$(printf '%s\n' "$differing" | grep -v '^\.dev/')" ]; then
          class=records-only; verdict=RETIRE; reason='the only files it adds are session records under .dev/'
        else
          absent=$(printf '%s\n' "$differing" | grep -v '^\.dev/' | while read -r f; do
            grep -qxF "$f" "$tmp/devfiles" || printf '%s\n' "$f"; done | wc -l)
          if [ "$absent" -gt 0 ]; then
            class=holds-new-code; reason="$absent files outside .dev/ the trunk does not have"
          else
            class=holds-changed-code
            reason="no new file, but it differs from the trunk on $(printf '%s\n' "$differing" | grep -vc '^\.dev/') tracked files"
          fi
          if [ "$include_code" = 1 ]; then verdict=RETIRE; else verdict=KEEP; fi
        fi
      fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$sha" "$class" "$verdict" "$reason" >> "$PLAN"
  done < <(git -C "$REPO" for-each-ref --format='%(refname:short)' "refs/remotes/$REMOTE")

  local r k
  r=$(awk -F'\t' '$4=="RETIRE"' "$PLAN" | wc -l); k=$(awk -F'\t' '$4=="KEEP"' "$PLAN" | wc -l)
  echo "== planned over $n branches on $REMOTE: retire $r, keep $k"
  echo
  echo "== by class"
  awk -F'\t' '!/^#/{printf "  %-18s %-7s %s\n", $3, $4, $1}' "$PLAN" | sort | awk '{print $1, $2}' | uniq -c | sort -rn
  echo
  echo "== plan written to $PLAN"
  echo "== next: $0 --archive"
  [ "$k" -gt 2 ] && return 1
  return 0
}

# ---------------------------------------------------------------- archive
archive() {
  [ -f "$PLAN" ] || refuse plan 'a plan to archive against' "no file at $PLAN" "run $0 --plan first"
  local stamp bundle idx tmp
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  bundle="$WORKDIR/$LANE_NAME-refs-$stamp.bundle"
  idx="$REPO/ops/conductor/handoff/branch-archive-$stamp.tsv"
  tmp=$(mktemp -d) || refuse scratch 'a temporary directory' 'mktemp failed' 'free space in TMPDIR'
  trap 'rm -rf "$tmp"' RETURN

  git -C "$REPO" bundle create "$bundle" --all >/dev/null 2>&1 \
    || refuse bundle 'a bundle of every ref' 'git bundle create failed' 'check disk space in the workdir'
  git -C "$REPO" bundle verify "$bundle" >/dev/null 2>&1 \
    || refuse bundle 'a bundle git can verify' 'git bundle verify refused it' 'rerun the archive'

  # Prove it restores: clone the bundle into a throwaway repository and compare
  # every planned tip, by sha, against what the bundle actually carries.
  git clone --quiet --bare "$bundle" "$tmp/restored.git" 2>/dev/null \
    || refuse restore 'a clone of the bundle' 'git clone from the bundle failed' 'rerun the archive'
  local missing=0 checked=0
  while IFS=$'\t' read -r name sha class verdict reason; do
    case "$name" in \#*|'') continue;; esac
    [ "$verdict" = RETIRE ] || continue
    checked=$((checked+1))
    git -C "$tmp/restored.git" cat-file -e "$sha^{commit}" 2>/dev/null || { missing=$((missing+1)); echo "  MISSING $sha $name"; }
  done < "$PLAN"
  [ "$missing" = 0 ] || refuse restore 'every planned tip present in the restored bundle' \
    "$missing of $checked tips missing" 'do not retire; rerun the plan and the archive'

  local bytes sum refs
  bytes=$(wc -c < "$bundle"); sum=$(sha256sum "$bundle" | cut -d' ' -f1)
  refs=$(git -C "$REPO" bundle list-heads "$bundle" | wc -l)
  {
    printf '# branch archive, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# bundle: %s\n' "$bundle"
    printf '#   bytes %s, refs %s, sha256 %s\n' "$bytes" "$refs" "$sum"
    printf '#   restore all: git clone %s <dir>\n' "$bundle"
    printf '#   restore one: git fetch %s <branch>:<branch>\n' "$bundle"
    printf '# every tip below was found in a clone of this bundle before any branch was retired\n'
    printf 'name\tsha\tclass\treason\n'
    awk -F'\t' '$4=="RETIRE" {printf "%s\t%s\t%s\t%s\n", $1, $2, $3, $5}' "$PLAN"
  } > "$idx"
  echo "== bundle: $bundle"
  echo "   $bytes bytes, $refs refs, sha256 $sum"
  echo "== restored into a throwaway clone and checked $checked planned tips: all present"
  echo "== index: $idx"
  echo "== next: commit the index, then $0 --retire --index $idx"
  return 0
}

# ---------------------------------------------------------------- retire
retire() {
  [ -n "$index" ] || index=$(ls -1t "$REPO"/ops/conductor/handoff/branch-archive-*.tsv 2>/dev/null | head -n 1)
  [ -n "$index" ] || refuse index 'an archive index to retire against' \
    'no branch-archive-*.tsv under ops/conductor/handoff/' "run $0 --archive first, or pass --index PATH"
  [ -f "$index" ] || refuse index 'an archive index to retire against' "no file at $index" \
    "run $0 --archive first, or pass an index that exists"
  local bundle sum_recorded sum_now
  bundle=$(awk '/^# bundle:/{print $3; exit}' "$index")
  sum_recorded=$(awk '/sha256/{print $NF; exit}' "$index")
  [ -f "$bundle" ] || refuse archive 'the bundle the index names' "no file at $bundle" 'restore the bundle before retiring'
  sum_now=$(sha256sum "$bundle" | cut -d' ' -f1)
  [ "$sum_now" = "$sum_recorded" ] || refuse archive 'the bundle unchanged since the index was written' \
    "sha256 $sum_now, index says $sum_recorded" 'rerun the archive'
  git -C "$REPO" bundle verify "$bundle" >/dev/null 2>&1 \
    || refuse archive 'a bundle git can verify' 'git bundle verify refused it' 'rerun the archive'

  local stamp out moved=0 gone=0 done_n=0 failed=0
  stamp=$(date -u +%Y%m%dT%H%M%SZ); out="$WORKDIR/branch-retire-$stamp.log"
  : > "$out"
  local batch=() names=()
  while IFS=$'\t' read -r name sha class reason; do
    case "$name" in \#*|''|name) continue;; esac
    local now; now=$(git -C "$REPO" ls-remote "$REMOTE" "refs/heads/$name" | cut -f1)
    if [ -z "$now" ]; then gone=$((gone+1)); echo "already gone   $name" >> "$out"; continue; fi
    if [ "$now" != "$sha" ]; then moved=$((moved+1)); echo "MOVED skipped  $name $sha -> $now" >> "$out"; continue; fi
    batch+=(":refs/heads/$name"); names+=("$name")
    if [ "${#batch[@]}" -ge 50 ]; then
      if git -C "$REPO" push "$REMOTE" "${batch[@]}" >/dev/null 2>>"$out"; then
        for b in "${names[@]}"; do echo "retired        $b" >> "$out"; done
        done_n=$((done_n+${#batch[@]}))
      else
        for b in "${names[@]}"; do echo "FAILED         $b" >> "$out"; done
        failed=$((failed+${#batch[@]}))
      fi
      batch=(); names=()
    fi
  done < "$index"
  if [ "${#batch[@]}" -gt 0 ]; then
    if git -C "$REPO" push "$REMOTE" "${batch[@]}" >/dev/null 2>>"$out"; then
      for b in "${names[@]}"; do echo "retired        $b" >> "$out"; done
      done_n=$((done_n+${#batch[@]}))
    else
      for b in "${names[@]}"; do echo "FAILED         $b" >> "$out"; done
      failed=$((failed+${#batch[@]}))
    fi
  fi
  echo "== retired $done_n; skipped because the tip moved $moved; already gone $gone; failed $failed"
  echo "== log: $out"
  echo "== branches on $REMOTE now: $(git -C "$REPO" ls-remote --heads "$REMOTE" | wc -l)"
  [ "$moved" -gt 0 ] || [ "$failed" -gt 0 ] && return 1
  return 0
}

case "$mode" in
  plan) plan;;
  archive) archive;;
  retire) retire;;
esac
