#!/usr/bin/env bash
# Operator entry point: running/sourcing this file never enables caller errexit.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    printf '%s\n' 'Run this file with bash: bash devtools/scripts/dependencies.sh --help'
    return 0
fi
set +x
script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Install Python 3.11+ first. On supported Ubuntu: sudo apt-get update && sudo apt-get install python3' >&2
    exit 1
fi
exec python3 -I -B "$script_dir/dependencies.py" "$@"
