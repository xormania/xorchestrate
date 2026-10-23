#!/usr/bin/env bash
# Run this script with bash; sourcing it must not change the caller's shell.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    printf '%s\n' 'Run git-sync.sh with bash; do not source it.' >&2
    return 0
fi
set +x
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: bash devtools/scripts/git-sync.sh [--repo PATH] [--apply [--push]]

Without --apply, inspect the current branch using locally cached remote refs.
--apply       Fetch that branch and fast-forward the local branch when behind.
--push        With --apply, also publish ahead commits to its tracking branch.
--repo PATH   Checkout to synchronize; defaults to this script's project.

Dirty, detached, and diverged checkouts are refused. This script never resets,
stashes, switches branches, or force-pushes. Fetch/push may request SSH credentials.
Git command output is suppressed so authentication failures cannot expose secrets.
EOF
}

fail() { printf 'Git sync: %s\n' "$*" >&2; exit 1; }

# Ignore inherited repository/config/transport overrides, inside this child only.
for variable in "${!GIT_@}"; do
    unset "$variable" || fail 'Could not clear an inherited Git environment override.'
done
export GIT_TERMINAL_PROMPT=0
export GIT_OPTIONAL_LOCKS=0

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
repo="$script_dir/../.."
apply=false
push=false
while (($#)); do
    case "$1" in
        --repo)
            (($# >= 2)) || fail '--repo needs a path.'
            repo=$2; shift 2 ;;
        --apply) apply=true; shift ;;
        --push) push=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail 'Unknown argument. Use --help.' ;;
    esac
done
if "$push" && ! "$apply"; then fail '--push also requires --apply.'; fi
command -v git >/dev/null 2>&1 || fail 'Git is not installed.'
repo=$(CDPATH='' cd -- "$repo" 2>/dev/null && pwd -P) || fail 'Repository directory does not exist.'

git_safe() {
    git -c core.hooksPath=/dev/null -c protocol.ext.allow=never \
        -c credential.interactive=never -c merge.autoStash=false -C "$repo" "$@"
}

root=$(git_safe rev-parse --show-toplevel 2>/dev/null) || fail 'Path is not a Git checkout.'
[[ "$root" == "$repo" ]] || fail '--repo must name the checkout root.'
branch=$(git_safe symbolic-ref --quiet --short HEAD 2>/dev/null) || fail 'Detached HEAD; select a branch yourself first.'
dirty=$(git_safe status --porcelain --untracked-files=normal --ignore-submodules=none 2>/dev/null) || fail 'Could not inspect checkout status.'
[[ -z "$dirty" ]] || fail 'Checkout has local changes or untracked files; preserve them before syncing.'
remote=$(git_safe config --get "branch.$branch.remote" 2>/dev/null) || fail 'Current branch has no tracking remote.'
merge=$(git_safe config --get "branch.$branch.merge" 2>/dev/null) || fail 'Current branch has no tracking branch.'
[[ "$remote" != '.' && "$merge" == refs/heads/* && "$merge" != *$'\n'* ]] || fail 'An ordinary remote tracking branch is required.'
upstream=$(git_safe rev-parse --symbolic-full-name '@{upstream}' 2>/dev/null) || fail 'Tracking ref is missing; fetch/configure it manually first.'
expected="refs/remotes/$remote/${merge#refs/heads/}"
[[ "$upstream" == "$expected" ]] || fail 'Nonstandard tracking ref; synchronize this checkout manually.'

# Do not print remote URLs. Refuse URL userinfo except the conventional SSH git
# account, query strings/fragments, and external helpers before connecting.
check_urls() {
    local urls url
    urls=$(git_safe remote get-url "$@" --all "$remote" 2>/dev/null) || fail 'Could not inspect remote URL configuration.'
    while IFS= read -r url; do
        if [[ "$url" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@ && ! "$url" =~ ^ssh://git@[^/:@]+/ ]] || \
            [[ "$url" == *'?'* || "$url" == *'#'* || "$url" == *'::'* ]]; then
            fail 'Remote URL contains userinfo, a query/fragment, or an external helper; use a clean Git URL and credential storage.'
        fi
    done <<< "$urls"
}
check_urls
if "$push"; then check_urls --push; fi

printf 'Repository: %s\nBranch: %s\nTracking remote: %s\n' "$repo" "$branch" "$remote"
if "$apply"; then
    printf '%s\n' 'Fetching the tracked branch…'
    git_safe fetch --no-tags --no-recurse-submodules -- "$remote" \
        "+$merge:$upstream" >/dev/null 2>&1 || fail 'Fetch failed. Check network access and Git authentication; no local branch was updated.'
fi

counts=$(git_safe rev-list --left-right --count "HEAD...$upstream" 2>/dev/null) || fail 'Could not compare local and tracking revisions.'
read -r ahead behind <<< "$counts"
printf 'Local commits ahead: %s; behind: %s%s\n' "$ahead" "$behind" "$(if ! "$apply"; then printf ' (cached remote state)'; fi)"
if ((ahead > 0 && behind > 0)); then fail 'Branches have diverged; resolve that explicitly before syncing.'; fi
if ! "$apply"; then
    printf '%s\n' 'Preview only. Use --apply to fetch and fast-forward; add --push to publish ahead commits.'
    exit 0
fi

if ((behind > 0)); then
    git_safe merge --ff-only --no-edit "$upstream" >/dev/null 2>&1 || fail 'Fast-forward failed; inspect the checkout before retrying.'
    printf '%s\n' 'Local branch fast-forwarded.'
fi
if "$push" && ((ahead > 0)); then
    head=$(git_safe rev-parse --verify HEAD 2>/dev/null) || fail 'Could not resolve the revision to push.'
    git_safe push --no-verify -- "$remote" "$head:$merge" >/dev/null 2>&1 || fail 'Push failed. The remote may have advanced or rejected these commits; no force-push was attempted.'
    printf '%s\n' 'Ahead commits published.'
elif ((ahead > 0)); then
    printf '%s\n' 'Ahead commits remain local. Run with --apply --push when ready to publish them.'
else
    printf '%s\n' 'Local and fetched remote revisions match.'
fi
