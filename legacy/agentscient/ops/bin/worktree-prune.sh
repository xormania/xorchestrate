#!/usr/bin/env bash
# worktree-prune.sh — drop only the worktree registrations that are safe to drop.
#
# A registration is safe only when all of these hold, each measured, never assumed:
#   - its directory is gone (a live worktree is never touched),
#   - it is not locked,
#   - its branch exists on the remote and the remote already contains every
#     commit the local branch and the archived head carry,
#   - the archive manifest says its working tree was archived,
#   - no deliverable-lock measure names its path.
# Anything else is kept and named, with the reason.
#
# It never deletes a branch, never rewrites a ref, never runs `git worktree prune`
# (which drops every missing registration, safe or not). What it removes under
# --apply is the administrative directory under .git/worktrees/<id>, and it tars
# those directories first so the removal can be undone.
#
# Exit: 0 nothing kept for review, 1 entries kept that a human should read,
#       2 refusal (expected; found; needed on stderr).
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) \
  || { printf 'root: expected the repository root above this lever; found dirname failed; needed run the lever from its checkout\n' >&2; exit 2; }
# LANE_NAME, TRUNK_BRANCH and RELEASE_BRANCH come from the shared conf, never
# from a literal in a lever (laneconf.py literals).
# shellcheck source=../lane.conf
. "$REPO/ops/lane.conf"
REPO_DEFAULT="$REPO"
MANIFEST_DEFAULT='ops/conductor/handoff/worktree-archive-2026-09-17.tsv'
LOCK_DEFAULT="${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/deliverable.json"
BACKUP_DIR_DEFAULT='/home/scientdb/wt-archive'

repo="$REPO_DEFAULT"; manifest=''; lock="$LOCK_DEFAULT"; remote='origin'
backup_dir="$BACKUP_DIR_DEFAULT"; apply=0; check_manifest=1; check_lock=1; do_fetch=1

refuse() { printf '%s: expected %s; found %s; needed %s\n' "$1" "$2" "$3" "$4" >&2; exit 2; }

usage() {
  cat <<'USAGE'
usage: worktree-prune.sh [--apply] [--repo PATH] [--manifest PATH] [--lock PATH]
                         [--remote NAME] [--backup-dir PATH]
                         [--no-manifest] [--no-lock] [--no-fetch]

Without --apply it only reports: this is the dry run, and it is the default.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift;;
    --repo) repo="${2-}"; shift 2;;
    --manifest) manifest="${2-}"; shift 2;;
    --lock) lock="${2-}"; shift 2;;
    --remote) remote="${2-}"; shift 2;;
    --backup-dir) backup_dir="${2-}"; shift 2;;
    --no-manifest) check_manifest=0; shift;;
    --no-lock) check_lock=0; shift;;
    --no-fetch) do_fetch=0; shift;;
    -h|--help) usage; exit 0;;
    *) refuse arguments 'a flag this script declares' "$1" 'run --help and pass one of the declared flags';;
  esac
done

[ -n "$repo" ] || refuse repo 'a repository path' 'empty' 'pass --repo PATH'
top=$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null) \
  || refuse repo 'a git repository' "$repo" 'pass --repo PATH pointing at the checkout that owns the worktrees'
repo="$top"
[ -n "$manifest" ] || manifest="$repo/$MANIFEST_DEFAULT"

gitdir=$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null) \
  || refuse repo 'a readable git directory' "$repo" 'check the checkout is intact'
case "$gitdir" in /*) ;; *) gitdir="$repo/$gitdir";; esac
wtdir="$gitdir/worktrees"
[ -d "$wtdir" ] || refuse registrations 'a .git/worktrees directory' "no $wtdir" 'nothing is registered; there is nothing to prune'

if [ "$check_manifest" = 1 ]; then
  [ -f "$manifest" ] || refuse manifest 'the archive manifest naming what was archived' "no file at $manifest" \
    'pass --manifest PATH, or --no-manifest to prune without that guard'
fi
if [ "$check_lock" = 1 ]; then
  [ -f "$lock" ] || refuse lock 'the deliverable lock, to see which paths a measure still names' "no file at $lock" \
    'pass --lock PATH, or --no-lock to prune without that guard'
fi

if [ "$do_fetch" = 1 ]; then
  git -C "$repo" fetch --quiet "$remote" 2>/dev/null \
    || refuse remote "a fetch from $remote, so containment is measured against fresh refs" \
       'the fetch failed' 'fix the remote, or pass --no-fetch and accept that the refs may be stale'
fi

# Verdicts, one line per registration: id \t verdict \t reason \t branch \t path
work=$(mktemp) || refuse scratch 'a temporary file' 'mktemp failed' 'free some space in TMPDIR'
trap 'rm -f "$work"' EXIT

for d in "$wtdir"/*/; do
  [ -d "$d" ] || continue
  id=$(basename "$d")
  gitdir_file="$d/gitdir"
  if [ ! -f "$gitdir_file" ]; then
    printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'no gitdir file: cannot tell what it points at' '-' '-' >> "$work"
    continue
  fi
  wt_git=$(cat "$gitdir_file")
  path=${wt_git%/.git}

  if [ -e "$path" ]; then
    printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'live: the directory is present' '-' "$path" >> "$work"
    continue
  fi
  if [ -f "$d/locked" ]; then
    printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'locked' '-' "$path" >> "$work"
    continue
  fi

  head_line=$(cat "$d/HEAD" 2>/dev/null)
  case "$head_line" in
    'ref: refs/heads/'*) branch=${head_line#ref: refs/heads/};;
    '') printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'no HEAD: cannot tell what it held' '-' "$path" >> "$work"; continue;;
    *)  branch='';;
  esac

  if [ -z "$branch" ]; then
    sha="$head_line"
    if [ -z "$(git -C "$repo" branch -r --contains "$sha" 2>/dev/null | head -n 1)" ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP "detached at $sha, on no remote branch" '(detached)' "$path" >> "$work"
      continue
    fi
    branch='(detached)'
    local_tip="$sha"; archived_ok=1
  else
    local_tip=$(git -C "$repo" rev-parse --verify --quiet "refs/heads/$branch")
    if [ -z "$local_tip" ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'its branch no longer exists locally' "$branch" "$path" >> "$work"
      continue
    fi
    remote_tip=$(git -C "$repo" rev-parse --verify --quiet "refs/remotes/$remote/$branch")
    if [ -z "$remote_tip" ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP "no branch on $remote" "$branch" "$path" >> "$work"
      continue
    fi
    if ! git -C "$repo" merge-base --is-ancestor "$local_tip" "$remote_tip" 2>/dev/null; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP "commits not on $remote/$branch" "$branch" "$path" >> "$work"
      continue
    fi
    archived_ok=1
  fi

  # The head the archive recorded must also be on the remote, or the tarball
  # holds work no ref carries.
  if [ "$check_manifest" = 1 ]; then
    row=$(awk -F'\t' -v p="$path" '$4==p {print; exit}' "$manifest")
    if [ -z "$row" ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'not named in the archive manifest' "$branch" "$path" >> "$work"
      continue
    fi
    status=$(printf '%s' "$row" | cut -f5)
    if [ "$status" != moved ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP "manifest says $status, not moved" "$branch" "$path" >> "$work"
      continue
    fi
    mhead=$(printf '%s' "$row" | cut -f3)
    if [ -n "$mhead" ] && [ -z "$(git -C "$repo" branch -r --contains "$mhead" 2>/dev/null | head -n 1)" ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP "archived head $mhead is on no remote branch" "$branch" "$path" >> "$work"
      continue
    fi
    archived_ok=1
  fi

  if [ "$check_lock" = 1 ] && grep -qF -- "$path" "$lock"; then
    printf '%s\t%s\t%s\t%s\t%s\n' "$id" KEEP 'a deliverable-lock measure names this path' "$branch" "$path" >> "$work"
    continue
  fi

  printf '%s\t%s\t%s\t%s\t%s\n' "$id" PRUNE 'gone, pushed, archived' "$branch" "$path" >> "$work"
done

total=$(wc -l < "$work")
prune_n=$(awk -F'\t' '$2=="PRUNE"' "$work" | wc -l)
live_n=$(awk -F'\t' '$3 ~ /^live/' "$work" | wc -l)
review_n=$(awk -F'\t' '$2=="KEEP" && $3 !~ /^live/' "$work" | wc -l)

echo "== registrations: $total; live: $live_n; prunable: $prune_n; kept for review: $review_n"
if [ "$review_n" -gt 0 ]; then
  echo
  echo "== kept, and why (each one is a branch or a tree nothing else carries)"
  awk -F'\t' '$2=="KEEP" && $3 !~ /^live/ {printf "  %-44s %-46s %s\n", $1, $3, $4}' "$work" | sort
fi
if [ "$prune_n" -gt 0 ]; then
  echo
  echo "== prunable (directory gone, every commit on $remote, working tree in the archive)"
  awk -F'\t' '$2=="PRUNE" {printf "  %s\n", $1}' "$work" | sort | head -n 20
  [ "$prune_n" -gt 20 ] && echo "  ... and $((prune_n - 20)) more"
fi

if [ "$apply" != 1 ]; then
  echo
  echo "== dry run: nothing was removed. Re-run with --apply to remove the $prune_n prunable registrations."
  [ "$review_n" -gt 0 ] && exit 1
  exit 0
fi

if [ "$prune_n" = 0 ]; then
  echo
  echo "== nothing to remove."
  [ "$review_n" -gt 0 ] && exit 1
  exit 0
fi

mkdir -p "$backup_dir" || refuse backup 'a writable backup directory' "cannot create $backup_dir" 'pass --backup-dir PATH'
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$backup_dir/worktree-admin-$stamp.tar.gz"
ids=$(awk -F'\t' '$2=="PRUNE" {print $1}' "$work")
printf '%s\n' "$ids" | tar czf "$backup" -C "$wtdir" -T - 2>/dev/null \
  || refuse backup 'the administrative directories tarred before removal' 'tar failed' 'fix the backup path and run again'
echo
echo "== backup: $backup ($(wc -c < "$backup") bytes), $(printf '%s\n' "$ids" | wc -l) entries"

removed=0
for id in $ids; do
  rm -rf -- "${wtdir:?}/$id" && removed=$((removed + 1))
done
echo "== removed $removed of $prune_n administrative directories"
echo "== git worktree list now reads: $(git -C "$repo" worktree list | wc -l)"
echo "== to undo: tar xzf $backup -C $wtdir"
[ "$review_n" -gt 0 ] && exit 1
exit 0
