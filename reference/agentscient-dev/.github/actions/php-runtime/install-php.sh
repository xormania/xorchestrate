#!/usr/bin/env bash
set -euo pipefail

[[ "$RUNNER_OS/$RUNNER_ARCH" == Linux/X64 ]] || { echo 'The pinned PHP runtime requires Linux/X64'; exit 1; }
[[ $# == 2 ]] || { echo 'Usage: install-php.sh ARCHIVE SHA256'; exit 1; }
ci_archive=$1
ci_digest=$2
[[ "$ci_digest" =~ ^[0-9a-f]{64}$ ]] || { echo 'Expected a pinned SHA-256 digest'; exit 1; }
# toolchain.py owns downloads and cache publication. This helper only accepts a
# local archive and extracts a fresh runtime; it never repairs or downloads one.
[[ -f "$ci_archive" ]] || { echo 'PHP archive is missing'; exit 1; }
printf '%s  %s\n' "$ci_digest" "$ci_archive" | sha256sum --check --status \
    || { echo 'PHP archive checksum mismatch'; exit 1; }

ci_php=$(mktemp -d "$RUNNER_TEMP/prod-php.XXXXXX")
trap 'rm -rf "$ci_php"' EXIT
tar --no-same-owner -xzf "$ci_archive" -C "$ci_php" php
"$ci_php/php" --version
printf '%s\n' "$ci_php" >> "$GITHUB_PATH"
trap - EXIT
