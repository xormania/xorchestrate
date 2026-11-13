#!/usr/bin/env bash
# Establish startup policy, then leave all proof decisions to the Python kit.
set -euo pipefail
umask 077

proof_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

proof_python=${PROOF_PYTHON:-python3}
if ! command -v -- "$proof_python" >/dev/null 2>&1; then
    printf '%s\n' "proof bootstrap: Python >=3.12 is required; set PROOF_PYTHON (missing: $proof_python)" >&2
    exit 2
fi
proof_python=$(command -v -- "$proof_python")
case "$proof_python" in
    /*) ;;
    *) proof_python="$PWD/$proof_python" ;;
esac
if ! PYTHONDONTWRITEBYTECODE=1 "$proof_python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    printf '%s\n' 'proof bootstrap: Python >=3.12 is required; set PROOF_PYTHON to a supported interpreter' >&2
    exit 2
fi

if [[ -n ${PYTHONPYCACHEPREFIX:-} ]]; then
    proof_cache=$(realpath -m -- "$PYTHONPYCACHEPREFIX")
else
    # The optional developer setting; looked up without writing bytecode, since no cache is chosen yet.
    configured_cache=$(cd -- "$proof_root" && PYTHONDONTWRITEBYTECODE=1 \
        "$proof_python" -m scripts.devconfig get XORCHESTRATE_DEV_PYCACHE_PREFIX 2>/dev/null) || configured_cache=
    if [[ -n $configured_cache ]]; then
        proof_cache=$(realpath -m -- "$configured_cache")
    else
        proof_cache=$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-pycache.XXXXXXXX")
    fi
fi
case "$proof_cache/" in
    "$proof_root/"*)
        printf '%s\n' 'proof bootstrap: bytecode cache must be outside the measured candidate' >&2
        exit 2 ;;
esac
mkdir -p -- "$proof_cache"
export PYTHONPYCACHEPREFIX="$proof_cache"
cd -- "$proof_root"
exec "$proof_python" -m scripts.testkit.adapters.profiles "$@"
