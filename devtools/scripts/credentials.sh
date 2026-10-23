#!/usr/bin/env bash
# Run as a child shell; sourcing must not alter the caller's shell options.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    printf '%s\n' 'Run with bash: bash devtools/scripts/credentials.sh --help'
    return 0
fi
set +x
script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Credential checks need Python 3.11 or newer.' >&2
    exit 1
fi
exec python3 -I -B "$script_dir/credentials.py" "$@"
