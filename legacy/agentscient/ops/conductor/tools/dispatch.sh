#!/usr/bin/env bash
set -uo pipefail

refuse() {
    printf 'dispatch: refusal: %s\n' "$*" >&2
    exit 3
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
conf_path=${LANE_CONF:-"$script_dir/../../lane.conf"}
[[ -f $conf_path ]] \
    || refuse "conf-file: expected a sourced lane conf; found no file at $conf_path; needed set LANE_CONF to a readable file"

validate_conf() {
    local file=$1 line number=0
    [[ -r $file ]] \
        || refuse "conf-file: expected a readable lane conf; found unreadable $file; needed correct its permissions"
    while IFS= read -r line || [[ -n $line ]]; do
        ((number += 1))
        [[ -z ${line//[[:space:]]/} || $line =~ ^[[:space:]]*# ]] && continue
        [[ $line =~ ^[A-Z][A-Z0-9_]*=\'[^\']*\'[[:space:]]*$ ]] \
            || refuse "syntax: expected a KEY='literal' assignment or comment; found line $number in $file; needed replace it with a literal single-quoted assignment"
    done < "$file"
}

reject_legacy_check_name() {
    local file=$1 line
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line =~ ^CHECK_NAME= ]] || continue
        refuse "conf-key: expected CHECK_NAME absent from $file; found CHECK_NAME in $file; needed remove CHECK_NAME from $file"
    done < "$file"
}

validate_conf "$conf_path"
reject_legacy_check_name "$conf_path"
conf_dir=$(CDPATH='' cd -- "$(dirname -- "$conf_path")" && pwd)
local_path=$conf_dir/lane.local.conf
if [[ -f $local_path ]]; then
    validate_conf "$local_path"
    reject_legacy_check_name "$local_path"
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line =~ ^([A-Z][A-Z0-9_]*)= ]] || continue
        case ${BASH_REMATCH[1]} in
            LANE_NAME|TRUNK_BRANCH|RELEASE_BRANCH|REPO_SLUG|CHECK_NAMES)
                refuse "local-key: expected lane identity keys only in the tracked conf; found ${BASH_REMATCH[1]} in $local_path; needed remove ${BASH_REMATCH[1]} from $local_path"
                ;;
        esac
    done < "$local_path"
fi

# LAUNCHER_PIN is resolved after sourcing, not required here: by default the
# launcher is found relative to this script (portable across worktrees and
# machines, and no home path leaks into a public conf). An operator who wants
# the hard supply-chain lock may still set an absolute LAUNCHER_PIN in the conf.
keys=(LANE_NAME TRUNK_BRANCH RELEASE_BRANCH REPO_SLUG CHECK_NAMES WF_AGENT_DEFAULT LANE_AUTHOR MODEL_GROK MODEL_CODEX MODEL_CLAUDE_READ MODEL_CLAUDE_PLAN EFFORT_GROK EFFORT_CODEX EFFORT_CLAUDE SCOPE_CAP)
env_keys=("${!MODEL_@}" "${!EFFORT_@}" "${!EXECUTABLE_@}")
# GRAFT_INSTALL is saved and restored but never required: the tracked conf declares it
# empty, and a worktree has no local conf, so without the restore the environment's
# install path would be emptied on the way to the launcher and no worker would get
# Graft (LD-13). TMUX_TMPDIR and XAI_API_KEY are the same shape: declared empty
# for the closure, read from the environment by the sessions lever and by the
# harnesses the launcher starts. DISPATCH_RECLAIM and DISPATCH_RECLAIM_DAYS are
# declared with values and read by the launcher, so without the restore an
# exported DISPATCH_RECLAIM=off read 'on' there and the sweep ran anyway.
saved_keys=("${keys[@]}" "${env_keys[@]}" WORKER_HARNESSES GRAFT_INSTALL TMUX_TMPDIR XAI_API_KEY
    DISPATCH_RECLAIM DISPATCH_RECLAIM_DAYS)
for key in "${saved_keys[@]}"; do
    if [[ -v $key ]]; then
        printf -v "saved_$key" '%s' "${!key}"
        printf -v "had_$key" '%s' 1
    else
        printf -v "had_$key" '%s' 0
    fi
done
# The environment's LAUNCHER_PIN beats the conf's (row 7 of the wrapper contract): remember it before sourcing.
had_pin=0; [[ -v LAUNCHER_PIN ]] && { had_pin=1; env_pin=$LAUNCHER_PIN; }
had_timeout=0; [[ -v DISPATCH_TIMEOUT ]] && { had_timeout=1; env_timeout=$DISPATCH_TIMEOUT; }
invoking_agent=${WF_AGENT:-}
identity_pattern=$'^[^<>\n]+ <[^<>[:space:]@]+@[^<>[:space:]]+>$'
unset DEFAULT_TIMEOUT DISPATCH_TIMEOUT
# shellcheck source=/dev/null
source "$conf_path" || refuse "expected a sourceable conf; found an error in $conf_path; fix its shell assignments"
if [[ -v DEFAULT_TIMEOUT ]]; then
    legacy_timeout=$DEFAULT_TIMEOUT
    unset DEFAULT_TIMEOUT
    refuse "expected DEFAULT_TIMEOUT absent from $conf_path; found DEFAULT_TIMEOUT=$legacy_timeout; needed remove the retired conf key"
fi
# shellcheck source=/dev/null
[[ ! -f $local_path ]] || source "$local_path" \
    || refuse "syntax: expected a sourceable local conf; found an error in $local_path; needed fix its literal assignments"
for key in "${saved_keys[@]}"; do
    had="had_$key"
    saved="saved_$key"
    if [[ ${!had} == 1 ]]; then
        printf -v "$key" '%s' "${!saved}"
    fi
done
# Only original operator overrides reach the loader as environment keys.
# File declarations are read there, with their original file provenance.
for key in WORKER_HARNESSES "${!EXECUTABLE_@}"; do
    had="had_$key"
    [[ ${!had:-0} == 1 ]] || unset "$key"
done
export LANE_CONF=$conf_path
if ((had_timeout)); then
    DISPATCH_TIMEOUT=$env_timeout
else
    unset DISPATCH_TIMEOUT
fi
for key in "${keys[@]}"; do
    [[ $key == WF_AGENT_DEFAULT ]] && continue
    [[ -v $key ]] \
        || refuse "conf-key: expected conf key $key; found it unset in $conf_path; needed define $key in $conf_path"
done
export CHECK_NAMES

if ((had_pin)); then
    LAUNCHER_PIN=$env_pin
    launcher_source=env
elif [[ -n ${LAUNCHER_PIN:-} ]]; then
    launcher_source=conf
else
    LAUNCHER_PIN="$script_dir/../../devlane/dispatch/launch.py"
    launcher_source=sibling
fi
# Resolve the launcher. An absolute LAUNCHER_PIN from the environment, else the conf,
# wins (the operator's hard lock); otherwise default to this script's sibling
# launcher, so a fresh checkout or worktree runs without editing any path.
LAUNCHER_PIN=$(realpath -e -- "$LAUNCHER_PIN" 2>/dev/null) \
    || refuse "expected LAUNCHER_PIN to name a launcher; found none at the resolved path; needed place launch.py or set LAUNCHER_PIN"

branch=''
worktree=''
job=''
harness=''
model=''
effort=''
unit=''
stage=''
scope_file=''
assignment=''
correlation=''
# Repeated --input flags preserve their order for launch.py's append action.
inputs=()
follows=()
answers=()
timeout=${DISPATCH_TIMEOUT:-}
timeout_set=$had_timeout
agent=''
agent_set=0
if [[ $invoking_agent =~ $identity_pattern ]]; then
    agent=$invoking_agent
    agent_set=1
fi
dry_run=0
detach=0

need_value() {
    (($# >= 2)) || refuse "expected a value after $1; found end of arguments; provide $1 VALUE"
    [[ $2 != --* ]] || refuse "expected a value after $1; found $2; provide $1 VALUE"
}

usage() {
    cat <<'USAGE'
usage: dispatch.sh --job JOB --harness {grok,codex,claude} --scope-file FILE (--branch BRANCH | --worktree DIR)
                   [--model MODEL] [--effort EFFORT] [--unit UNIT] [--stage STAGE]
                   [--input PATH]... [--follows RECORD_ID] [--timeout SECONDS] [--agent 'Name <address>']
                   [--for ASSIGNMENT] [--correlation UUID] [--dry-run] [--detach]
One job to one harness through the branch's own launcher; --detach prints the
record id and launcher pid and returns. Contract: ops/devlane/dispatch/CONTRACT.md.
USAGE
}

while (($#)); do
    case $1 in
        --help|-h) usage; exit 0 ;;
        --branch) need_value "$@"; branch=$2; shift 2 ;;
        --worktree) need_value "$@"; worktree=$2; shift 2 ;;
        --job) need_value "$@"; job=$2; shift 2 ;;
        --harness) need_value "$@"; harness=$2; shift 2 ;;
        --model) need_value "$@"; model=$2; shift 2 ;;
        --effort) need_value "$@"; effort=$2; shift 2 ;;
        --unit) need_value "$@"; unit=$2; shift 2 ;;
        --stage) need_value "$@"; stage=$2; shift 2 ;;
        --scope-file) need_value "$@"; scope_file=$2; shift 2 ;;
        --for) need_value "$@"; assignment=$2; shift 2 ;;
        --correlation) need_value "$@"; correlation=$2; shift 2 ;;
        --input) need_value "$@"; inputs+=("$2"); shift 2 ;;
        --follows) need_value "$@"; follows+=("$2"); shift 2 ;;
        --answers) need_value "$@"; answers+=("$2"); shift 2 ;;
        --timeout) need_value "$@"; timeout=$2; timeout_set=1; shift 2 ;;
        --agent) need_value "$@"; agent=$2; agent_set=1; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --detach) detach=1; shift ;;
        *) refuse "expected a documented flag; found unknown flag $1; remove it" ;;
    esac
done

if ((!agent_set)); then
    # An exported WF_AGENT that fails the form is a declaration that
    # failed, not an absence: dropping it launched the job under the conf
    # default and recorded an identity the caller never wrote. Unset is an
    # absence, and WF_AGENT_DEFAULT is the answer the conf declares for it.
    # The found clause shows a newline as backslash-n so the wire stays one
    # line and a broken value is not folded into a well-formed-looking one.
    [[ -z $invoking_agent ]] \
        || refuse "identity: expected WF_AGENT in the Name <address> form; found ${invoking_agent//$'\n'/\\n}; needed export WF_AGENT='Name <address>' or pass --agent 'Name <address>'"
    [[ -v WF_AGENT_DEFAULT ]] \
        || refuse "conf-key: expected conf key WF_AGENT_DEFAULT; found it unset in $conf_path; needed define WF_AGENT_DEFAULT in $conf_path"
    agent=$WF_AGENT_DEFAULT
fi

[[ -n $job ]] || refuse "expected required --job; found it missing; pass --job JOB"
[[ -n $harness ]] || refuse "expected required --harness; found it missing; pass --harness grok, codex, or claude"
[[ -n $scope_file ]] || refuse "expected required --scope-file; found it missing; pass --scope-file FILE"
[[ -n $branch || -n $worktree ]] || refuse "expected one of --branch or --worktree; found neither; select a lineage worktree"
[[ -z $branch || -z $worktree ]] || refuse "expected one of --branch or --worktree; found both; pass exactly one"
[[ $job =~ ^[a-z][a-z0-9-]*$ ]] || refuse "expected --job matching ^[a-z][a-z0-9-]*$; found $job; needed a catalogue-shaped job name"
worker_error=$(python3 "$script_dir/laneconf.py" worker "$harness" 2>&1 >/dev/null) \
    || refuse "$worker_error"
[[ -f $scope_file ]] || refuse "expected --scope-file to name a file; found $scope_file; create the file or correct the path"

scope_bytes=$(wc -c < "$scope_file") || refuse "expected a readable --scope-file; found unreadable $scope_file; correct its permissions"
scope_bytes=${scope_bytes//[[:space:]]/}
[[ $SCOPE_CAP =~ ^[1-9][0-9]*$ ]] \
    || refuse "scope-cap: expected SCOPE_CAP as a positive whole number; found $SCOPE_CAP in $conf_path; needed correct SCOPE_CAP"
((scope_bytes <= SCOPE_CAP)) \
    || refuse "scope-cap: expected --scope-file at most $SCOPE_CAP bytes; found $scope_bytes bytes; needed shorten it"
scope=$(cat -- "$scope_file"; printf x) || refuse "expected a readable --scope-file; found unreadable $scope_file; correct its permissions"
scope=${scope%x}
for input in "${inputs[@]}"; do
    [[ -f $input ]] \
        || refuse "expected --input to name a file; found $input; needed create the file or correct the path"
done

if [[ -n $branch ]]; then
    found_path=
    candidate=
    while IFS= read -r line; do
        case $line in
            'worktree '*) candidate=${line#worktree } ;;
            "branch refs/heads/$branch") found_path=$candidate; break ;;
        esac
    done < <(git worktree list --porcelain)
    [[ -n $found_path ]] || refuse "expected branch $branch in a worktree; found none; run git worktree add <path> $branch"
    worktree=$found_path
fi
[[ -d $worktree ]] || refuse "expected a worktree directory; found $worktree; correct --worktree"

ref=$(git -C "$worktree" rev-parse HEAD 2>/dev/null) || refuse "expected a git worktree; found $worktree without HEAD; correct the target"
lineage=$(git -C "$worktree" symbolic-ref --quiet --short HEAD 2>/dev/null || true)

# Owner, 2026-09-12: "why are the branches always out of date - or some other
# bullshit git issue". A worktree behind origin/<trunk> is merged forward here,
# before the launch, so the launch sees a tree that contains the trunk; a
# conflict refuses stale-base with the files named and the worktree as it was.
# A dry run reports what it would do and moves nothing.
trunk_ref="origin/$TRUNK_BRANCH"
if git -C "$worktree" rev-parse --verify -q "$trunk_ref" >/dev/null 2>&1 \
   && ! git -C "$worktree" merge-base --is-ancestor "$trunk_ref" HEAD 2>/dev/null; then
    behind=$(git -C "$worktree" rev-list --count "HEAD..$trunk_ref" 2>/dev/null || echo '?')
    if ((dry_run)); then
        printf 'would merge: %s into %s (%s commits behind)\n' "$trunk_ref" "${lineage:-HEAD}" "$behind"
    else
        [[ -z $(git -C "$worktree" status --porcelain) ]] \
            || refuse "stale-base: expected a clean worktree to merge $trunk_ref forward; found uncommitted changes in $worktree; needed commit or stash them"
        merge_msg=$(printf 'merge %s into %s before dispatch (%s commits)\n\nSource: generated: ops/conductor/tools/dispatch.sh\nCo-Authored-By: %s\n' "$trunk_ref" "${lineage:-HEAD}" "$behind" "$agent")
        if ! git -C "$worktree" merge --no-ff --no-edit -q -m "$merge_msg" "$trunk_ref" >/dev/null 2>&1; then
            conflicted=$(git -C "$worktree" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ')
            git -C "$worktree" merge --abort >/dev/null 2>&1 || true
            refuse "stale-base: expected $trunk_ref to merge cleanly into ${lineage:-HEAD}; found conflicts in ${conflicted:-unnamed files}; needed resolve them in $worktree (git merge $trunk_ref), commit, then dispatch"
        fi
        ref=$(git -C "$worktree" rev-parse HEAD)
        printf 'merged: %s into %s (%s commits) at %s\n' "$trunk_ref" "${lineage:-HEAD}" "$behind" "$ref"
    fi
fi

job_key=${job^^}
job_key=${job_key//-/_}
model_job="MODEL_${harness^^}_${job_key}"
effort_job="EFFORT_${harness^^}_${job_key}"
if [[ -z $model && -n ${!model_job:-} ]]; then
    model=${!model_job}
fi
if [[ -z $model ]]; then
    model_harness="MODEL_${harness^^}"
    model=${!model_harness:-}
fi
if [[ -z $model && $harness == claude ]]; then
    if [[ $stage == plan ]]; then model=$MODEL_CLAUDE_PLAN; else model=$MODEL_CLAUDE_READ; fi
fi
[[ -n $model ]] || refuse "model: expected a model; found none for $harness; needed pass --model or configure MODEL_${harness^^}"
if [[ -z $effort ]]; then
    if [[ -n ${!effort_job:-} ]]; then
        effort=${!effort_job}
    else
        effort_harness="EFFORT_${harness^^}"
        effort=${!effort_harness:-}
    fi
fi

argv=("$LAUNCHER_PIN" "$job" --harness "$harness" --model "$model" --ref "$ref")
[[ -z $effort ]] || argv=("${argv[@]:0:6}" --effort "$effort" "${argv[@]:6}")
[[ -z $lineage ]] || argv+=(--lineage "$lineage")
[[ -z $unit ]] || argv+=(--unit "$unit")
[[ -z $stage ]] || argv+=(--stage "$stage")
[[ -z $assignment ]] || argv+=(--for "$assignment")
# The intent's join to its job directory (sessions-lever.md, slice 3) is not
# passed to the launcher: the pinned launch.py admits no --correlation and
# argparse exits 2 on it (A-524 #1). A detached launch writes it as the
# launcher log's first line instead, which dispatch_intents.scan reads.
argv+=(--scope "$scope")
for input in "${inputs[@]}"; do
    argv+=(--input "$input")
done
for follow in "${follows[@]}"; do
    argv+=(--follows "$follow")
done
for answer in "${answers[@]}"; do
    argv+=(--answers "$answer")
done

print_launch() {
    WF_AGENT=$agent python3 -c 'import json, os, sys
env = {"WF_AGENT": os.environ["WF_AGENT"],
       "CHECK_NAMES": os.environ["CHECK_NAMES"]}
if "DISPATCH_TIMEOUT" in os.environ:
    env["DISPATCH_TIMEOUT"] = os.environ["DISPATCH_TIMEOUT"]
if os.environ.get("GRAFT_INSTALL"):
    env["GRAFT_INSTALL"] = os.environ["GRAFT_INSTALL"]
print(json.dumps({"argv": sys.argv[4:], "env": env, "exec": True,
                  "detach": sys.argv[3] == "1",
                  "launcher": {"path": sys.argv[2], "source": sys.argv[1]}},
                 ensure_ascii=False))' "$launcher_source" "$LAUNCHER_PIN" "$detach" "${argv[@]}"
}

if ((timeout_set)); then
    export DISPATCH_TIMEOUT=$timeout
else
    unset DISPATCH_TIMEOUT
fi

print_launch
((dry_run)) && exit 0

printf 'launcher: %s (%s)\n' "$LAUNCHER_PIN" "$launcher_source"
if ((!detach)); then
    WF_AGENT=$agent "${argv[@]}"
    exit $?
fi

jobs_root=${DISPATCH_JOBS:-"${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/dispatch"}
mkdir -p -- "$jobs_root" \
    || refuse "expected a writable jobs root; found mkdir failed at $jobs_root; needed set DISPATCH_JOBS to a writable directory"
export DISPATCH_JOBS=$jobs_root
launch_log=$(mktemp -p "$jobs_root" .launcher.XXXXXX) \
    || refuse "expected a temporary launcher log in $jobs_root; found mktemp failed; needed make the jobs root writable"
detach_wait=${DETACH_WAIT:-60}
[[ $detach_wait =~ ^[1-9][0-9]*$ ]] || {
    rm -f -- "$launch_log"
    refuse "expected DETACH_WAIT as positive whole seconds; found $detach_wait; needed set DETACH_WAIT to a positive integer"
}
[[ -z $correlation ]] || printf 'correlation: %s\n' "$correlation" >"$launch_log"
setsid nohup env WF_AGENT="$agent" "${argv[@]}" >>"$launch_log" 2>&1 </dev/null &
launcher_pid=$!
record_line=''
for ((attempt = 0; attempt < detach_wait * 100; attempt++)); do
    record_line=$(awk '/^record: / {print; exit}' "$launch_log")
    [[ -n $record_line ]] && break
    if ! jobs -pr | grep -qx "$launcher_pid"; then
        wait "$launcher_pid"
        rc=$?
        cat -- "$launch_log" >&2
        rm -f -- "$launch_log"
        exit "$rc"
    fi
    sleep 0.01
done
if [[ -z $record_line ]]; then
    kill "$launcher_pid" 2>/dev/null || true
    wait "$launcher_pid" 2>/dev/null || true
    cat -- "$launch_log" >&2
    rm -f -- "$launch_log"
    refuse "expected the launcher to print record: ID at mint; found no record within $detach_wait seconds; needed repair the launcher mint signal"
fi
record_id=${record_line#record: }
[[ $record_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    kill "$launcher_pid" 2>/dev/null || true
    wait "$launcher_pid" 2>/dev/null || true
    rm -f -- "$launch_log"
    refuse "expected a path-safe launcher record id; found $record_id; needed print a catalogue-shaped record id"
}
job_dir=$jobs_root/$record_id
[[ -d $job_dir ]] || {
    kill "$launcher_pid" 2>/dev/null || true
    wait "$launcher_pid" 2>/dev/null || true
    rm -f -- "$launch_log"
    refuse "expected launcher record directory $job_dir; found it absent; needed mint the record directory before printing its id"
}
[[ ! -e $job_dir/launcher.log ]] || {
    kill "$launcher_pid" 2>/dev/null || true
    wait "$launcher_pid" 2>/dev/null || true
    rm -f -- "$launch_log"
    refuse "expected no prior launcher log at $job_dir/launcher.log; found an existing path; needed use a fresh record id"
}
mv -- "$launch_log" "$job_dir/launcher.log"
printf '%s\n' "$record_line"
printf 'launcher-pid: %s\n' "$launcher_pid"
