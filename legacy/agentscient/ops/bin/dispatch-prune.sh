#!/usr/bin/env bash
# dispatch-prune.sh — reclaim the dispatch store's reproducible bulk, keeping
# every piece of evidence a job actually produced.
#
# Three steps, each its own run, each refusing without the one before it:
#
#   --plan    read every job directory, resolve its snapshot sha from the
#             record (in the store, else the repository's tracked copy),
#             prove that sha is present in the repository, and write the index
#   --stage   move each planned job's snapshot/ and home/ out of the store into
#             a staging directory, counting what moved; nothing is deleted
#   --remove  delete the staging directory, after re-reading the index and
#             proving the store still holds every job's evidence
#
# A job is planned only when a reading says nothing is lost: its snapshot/ is a
# git checkout whose sha the repository still has, so the tree is reproducible
# by `git worktree add <sha>`; and its home/ is the harness isolation HOME the
# lane's own law calls runtime residue. A job whose record exists in neither
# the store nor the repository is kept whole — its directory is the only trace
# of it, and the plan says so.
#
# What is never touched: record.json, state.json, diff.patch, prompt.txt,
# raw.out, stderr, exit, launcher.log, lane-mcp.json, in/, out/, context/.
# Those are the job's evidence and are what the store exists to hold.
#
# Exit: 0 clean, 1 findings kept for a human, 2 refusal.
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) \
  || { printf 'root: expected the repository root above this lever; found dirname failed; needed run the lever from its checkout\n' >&2; exit 2; }
# LANE_NAME, TRUNK_BRANCH and RELEASE_BRANCH come from the shared conf, never
# from a literal in a lever (laneconf.py literals).
# shellcheck source=../lane.conf
. "$REPO/ops/lane.conf"
STORE="${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/dispatch"
STAGE='/home/scientdb/dispatch-bulk-staging-20260918'
INDEX=''
mode='plan'

refuse() { printf '%s: expected %s; found %s; needed %s\n' "$1" "$2" "$3" "$4" >&2; exit 2; }
usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --plan) mode=plan; shift;;
    --stage) mode=stage; shift;;
    --remove) mode=remove; shift;;
    --index) INDEX="${2-}"; shift 2;;
    --repo) REPO="${2-}"; shift 2;;
    --store) STORE="${2-}"; shift 2;;
    --staging) STAGE="${2-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) refuse arguments 'a flag this script declares' "$1" 'run --help and pass one of the declared flags';;
  esac
done

[ "$(id -u)" != 0 ] \
  || refuse identity 'the lever run as the user that owns the store' \
       'running as root (sudo sets HOME=/root, so the store path resolves there)' \
       'run it as yourself, without sudo; every file it touches is already yours'

top=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) \
  || refuse repo 'a git repository' "$REPO" 'pass --repo PATH'
REPO="$top"
[ -d "$STORE" ] || refuse store 'the dispatch store directory' "$STORE is absent" 'pass --store PATH'
[ -n "$INDEX" ] || INDEX="$REPO/ops/conductor/handoff/dispatch-snapshot-index-20260918.tsv"

# A job still running must never be pruned: the launcher writes into snapshot/
# and home/ for the life of the job.
running=$(pgrep -c -f 'dispatch\.sh|launcher\.py' 2>/dev/null || true)
[ "${running:-0}" = 0 ] \
  || refuse running 'no dispatch in flight' "$running launcher process(es)" \
       'wait for every dispatch to close, then run again'

case "$mode" in

plan)
  python3 - "$REPO" "$STORE" "$INDEX" <<'PY'
import json, os, subprocess, sys
from collections import Counter

repo, store, out = sys.argv[1], sys.argv[2], sys.argv[3]
seen = {}

def present(sha):
    if not sha or len(sha) < 7:
        return "no-sha"
    if sha not in seen:
        r = subprocess.run(["git", "-C", repo, "cat-file", "-e", sha + "^{commit}"],
                           capture_output=True, timeout=30)
        seen[sha] = "present" if r.returncode == 0 else "absent"
    return seen[sha]

def record_for(jid):
    """The job's record, from the store, else the repository's tracked copy."""
    p = os.path.join(store, jid, "record.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)), "store"
        except Exception:
            return {}, "unreadable"
    p = os.path.join(repo, ".dev/records/dispatches", jid + ".json")
    if os.path.exists(p):
        try:
            return json.load(open(p)), "repo"
        except Exception:
            return {}, "unreadable"
    return None, "none"

hdr = ["id", "stage", "harness", "branch", "base_sha", "snapshot_ref_sha", "mode",
       "ref_sha_in_repo", "has_snapshot", "has_home", "has_diff_patch",
       "record_source", "disposition"]
rows = []
for jid in sorted(os.listdir(store)):
    p = os.path.join(store, jid)
    if not os.path.isdir(p):
        continue
    rec, src = record_for(jid)
    o = rec or {}
    snap, lin = o.get("snapshot") or {}, o.get("lineage") or {}
    ref = snap.get("ref_sha") or ""
    state = present(ref)
    # Reproducible only when the repository still holds the sha the snapshot is of.
    if src in ("store", "repo") and state == "present":
        disp = "bulk-prunable"
    elif src == "none":
        disp = "kept-whole-no-record-anywhere"
    else:
        disp = "kept-whole-sha-absent"
    rows.append([jid, o.get("stage", "UNKNOWN"),
                 (o.get("harness") or {}).get("name", "UNKNOWN"),
                 lin.get("branch", "UNKNOWN"), lin.get("base_sha", ""), ref,
                 snap.get("mode", ""), state,
                 "yes" if os.path.isdir(os.path.join(p, "snapshot")) else "no",
                 "yes" if os.path.isdir(os.path.join(p, "home")) else "no",
                 "yes" if os.path.exists(os.path.join(p, "diff.patch")) else "no",
                 src, disp])

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as f:
    f.write("\t".join(hdr) + "\n")
    for r in rows:
        f.write("\t".join(str(x) for x in r) + "\n")

c = Counter(r[12] for r in rows)
print("jobs: %d" % len(rows))
for k in sorted(c):
    print("  %s: %d" % (k, c[k]))
print("index: %s" % out)
sys.exit(1 if any(k.startswith("kept-whole") for k in c) else 0)
PY
  ;;

stage)
  [ -f "$INDEX" ] || refuse plan 'the index --plan writes' "$INDEX is absent" 'run --plan first'
  mkdir -p "$STAGE" || refuse staging 'a writable staging directory' "cannot create $STAGE" 'pass --staging PATH'
  moved=0; failed=0
  while IFS=$'\t' read -r jid _ _ _ _ _ _ _ _ _ _ _ disp; do
    [ "$disp" = 'bulk-prunable' ] || continue
    for c in snapshot home; do
      [ -d "$STORE/$jid/$c" ] || continue
      mkdir -p "$STAGE/$jid"
      # The harness seals a finished snapshot 555; the seal is against the
      # worker, not its owner, so lift it before the move.
      chmod u+w "$STORE/$jid/$c" 2>/dev/null
      if mv "$STORE/$jid/$c" "$STAGE/$jid/$c" 2>/dev/null; then
        moved=$((moved + 1))
      else
        failed=$((failed + 1)); printf 'kept: %s/%s did not move\n' "$jid" "$c" >&2
      fi
    done
    rmdir "$STAGE/$jid" 2>/dev/null
  done < <(tail -n +2 "$INDEX")
  printf 'staged %d directories into %s\n' "$moved" "$STAGE"
  [ "$failed" = 0 ] || { printf '%d did not move\n' "$failed" >&2; exit 1; }
  ;;

remove)
  [ -f "$INDEX" ] || refuse plan 'the index --plan writes' "$INDEX is absent" 'run --plan first'
  [ -d "$STAGE" ] || refuse staging 'the staging directory --stage fills' "$STAGE is absent" 'run --stage first'
  # Every planned job must still hold its record and its diff in the store, or
  # the staging is not residue and must not be deleted.
  short=0
  while IFS=$'\t' read -r jid _ _ _ _ _ _ _ _ _ haddiff _ disp; do
    [ "$disp" = 'bulk-prunable' ] || continue
    [ -f "$STORE/$jid/record.json" ] || [ -f "$REPO/.dev/records/dispatches/$jid.json" ] \
      || { printf 'short: %s has no record in store or repository\n' "$jid" >&2; short=$((short + 1)); }
    [ "$haddiff" != 'yes' ] || [ -f "$STORE/$jid/diff.patch" ] \
      || { printf 'short: %s lost its diff.patch\n' "$jid" >&2; short=$((short + 1)); }
  done < <(tail -n +2 "$INDEX")
  [ "$short" = 0 ] \
    || refuse evidence 'every pruned job still holding its record and diff in the store' \
         "$short job(s) short" 'restore from the staging directory and investigate before removing'
  before=$(du -s --inodes "$STAGE" 2>/dev/null | cut -f1)
  # The harness seals a finished snapshot 555 through the whole tree; rm cannot
  # unlink from a directory it cannot write to, so lift the seal recursively.
  chmod -R u+w "$STAGE" 2>/dev/null
  sealed=$(find "$STAGE" -type d ! -perm -u+w 2>/dev/null | wc -l)
  [ "$sealed" = 0 ] \
    || refuse sealed 'every staged directory writable by its owner' \
         "$sealed directory(ies) still sealed" \
         'chmod -R u+w the staging directory by hand, then run --remove again'
  rm -rf "$STAGE" || refuse remove 'the staging directory deleted' "rm -rf $STAGE failed" 'check permissions and run again'
  printf 'removed %s: %s inodes reclaimed\n' "$STAGE" "${before:-unknown}"
  ;;

esac
