#!/usr/bin/env bash
# Manual operator maintenance. Sourcing must not change the caller's shell.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    printf '%s\n' 'Run this file with bash: bash devtools/scripts/harnesses.sh --help'
    return 0
fi
set +x
script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Harness maintenance needs Python 3.11 or newer.' >&2
    exit 1
fi
exec python3 -I -B "$script_dir/harnesses.py" "$@"
