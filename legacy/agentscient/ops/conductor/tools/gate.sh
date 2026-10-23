#!/bin/sh

set -u

tool_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$tool_dir/../../.." && pwd)

env_log_dir_set=${GATE_LOG_DIR+x}
env_log_dir=${GATE_LOG_DIR-}
env_tail_set=${GATE_TAIL+x}
env_tail=${GATE_TAIL-}

for conf in "$tool_dir/dispatch.conf" "$tool_dir/../dispatch.conf"; do
    if [ -f "$conf" ]; then
        # shellcheck disable=SC1090
        . "$conf"
    fi
done

if [ "$env_log_dir_set" = x ]; then
    GATE_LOG_DIR=$env_log_dir
fi
if [ "$env_tail_set" = x ]; then
    GATE_TAIL=$env_tail
fi

GATE_LOG_DIR=${GATE_LOG_DIR:-${TMPDIR:-/tmp}/agentscient-gates}
GATE_TAIL=${GATE_TAIL:-15}
gate_kinds=$repo_root/ops/devlane/workflow/gate-kinds

refuse() {
    printf 'gate: refusal: %s\n' "$*" >&2
    exit 2
}

case $GATE_TAIL in
    ''|*[!0-9]*) refuse "GATE_TAIL must be a non-negative integer; got '$GATE_TAIL'" ;;
esac

[ "$#" -gt 0 ] || refuse "expected mode: check, suite, or run"
mode=$1
shift

case $mode in
    run)
        [ "${1-}" = -- ] || refuse "run expects: gate.sh run -- <argv...>"
        shift
        [ "$#" -gt 0 ] || refuse "run expects at least one command argument"
        ;;
    suite)
        if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
            refuse "suite expects: gate.sh suite <start-dir> [pattern]"
        fi
        start_dir=$1
        shift
        if [ "$#" -eq 1 ]; then
            set -- python3 -m unittest discover -s "$start_dir" -p "$1"
        else
            set -- python3 -m unittest discover -s "$start_dir"
        fi
        ;;
    check)
        [ "$#" -eq 1 ] || refuse "check expects: gate.sh check <name>"
        check_name=$1
        known=$(python3 -c '
import json, pathlib, sys
names = []
for path in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    for check in json.loads(path.read_text(encoding="utf-8")).get("checks") or []:
        if check.get("name") and check["name"] not in names:
            names.append(check["name"])
print(", ".join(names))
' "$gate_kinds") || refuse "could not read gate-kinds under $gate_kinds"
        python3 -c '
import json, pathlib, sys
name, root = sys.argv[1:]
for path in sorted(pathlib.Path(root).glob("*.json")):
    for check in json.loads(path.read_text(encoding="utf-8")).get("checks") or []:
        if check.get("name") == name:
            argv = check.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
                raise SystemExit(3)
            raise SystemExit(0)
raise SystemExit(127)
' "$check_name" "$gate_kinds" >/dev/null 2>&1
        resolver_rc=$?
        if [ "$resolver_rc" -eq 127 ]; then
            refuse "unknown check '$check_name'; known checks: $known"
        fi
        if [ "$resolver_rc" -eq 3 ]; then
            refuse "check '$check_name' has no valid argv; expected a non-empty string array"
        fi
        if [ "$resolver_rc" -ne 0 ]; then
            refuse "could not resolve check '$check_name' from gate-kinds under $gate_kinds"
        fi
        set -- python3 -c '
import json, os, pathlib, sys
name, root = sys.argv[1:]
for path in sorted(pathlib.Path(root).glob("*.json")):
    for check in json.loads(path.read_text(encoding="utf-8")).get("checks") or []:
        if check.get("name") == name:
            argv = check["argv"]
            os.execvp(argv[0], argv)
raise SystemExit(127)
' "$check_name" "$gate_kinds"
        ;;
    *) refuse "unknown mode '$mode'; expected check, suite, or run" ;;
esac

if [ ! -d "$GATE_LOG_DIR" ]; then
    mkdir -p -- "$GATE_LOG_DIR" 2>/dev/null || \
        refuse "log directory '$GATE_LOG_DIR' does not exist and could not be created"
fi
[ -w "$GATE_LOG_DIR" ] || \
    refuse "log directory '$GATE_LOG_DIR' is not writable"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
log_path=$(mktemp "$GATE_LOG_DIR/gate-$stamp-XXXXXX.log" 2>/dev/null) || \
    refuse "could not create a log file in '$GATE_LOG_DIR'; the directory must be writable"

"$@" >"$log_path" 2>&1
rc=$?

tail -n "$GATE_TAIL" -- "$log_path"
printf 'exit: %s\n' "$rc"
printf 'log: %s\n' "$log_path"
exit "$rc"
