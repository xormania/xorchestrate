#!/usr/bin/env bash
# One local/CI entry point. Definitions and verdict: workflow/checks/lane.py;
# bounded execution and discovery: ops/testkit. See CONTRACT-ci.md.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$here/../.."
exec python3 -m ops.devlane.workflow.checks.lane "$@"
