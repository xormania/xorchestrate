#!/usr/bin/env bash
# dispatch.sh — the lever's front door. The wrapper itself lives with the
# instruments at ops/conductor/tools/dispatch.sh (where its tests and the
# doctor's probes look); this shim keeps the ops/bin address every document
# and go.sh already name.
exec bash "$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../conductor/tools/dispatch.sh" "$@"
