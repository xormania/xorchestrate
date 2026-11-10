#!/bin/sh
# Install the xorchestrate launcher for the current user with `uv tool install`.
#
#   sh install.sh                    latest release from the package index
#   sh install.sh --version X.Y.Z    a specific release from the package index
#   sh install.sh --git REF          a tag, branch or commit from the git repository
#
# uv is installed first, with its official installer, when it is not found.
# Nothing here uses sudo; everything goes under your home directory. Running it
# again is safe: an installed launcher is kept unless you ask for another release.
#
# Settings (environment variables):
#   XORCHESTRATE_PACKAGE     package name                (default: xorchestrate)
#   XORCHESTRATE_INDEX_URL   package index for the launcher and its dependencies
#                            (default: uv's default index, PyPI)
#   XORCHESTRATE_REPOSITORY  git repository for --git    (default: https://github.com/xormania/xorchestrate)
#   UV_INSTALLER_URL         uv's installer               (default: https://astral.sh/uv/install.sh)
#
# Everything runs from main, called on the last line, so a partly downloaded
# copy of this script does nothing.
set -eu

# Oldest uv with every option used here (`uv tool install --default-index`).
MIN_UV_MAJOR=0
MIN_UV_MINOR=4
MIN_UV_PATCH=23

say() { printf 'xorchestrate-install: %s\n' "$*"; }
fail() { printf 'xorchestrate-install: error: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' "usage: install.sh [--version X.Y.Z | --git REF]" \
        "Installs the xorchestrate launcher for this user with uv tool install," \
        "installing uv first when it is missing. Never uses sudo."
}

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for candidate in ${UV_INSTALL_DIR:+"$UV_INSTALL_DIR/uv"} ${XDG_BIN_HOME:+"$XDG_BIN_HOME/uv"} \
        "$HOME/.local/bin/uv" "${CARGO_HOME:-$HOME/.cargo}/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

install_uv() {
    say "uv was not found; installing it for this user from $installer_url"
    installer=$(mktemp "${TMPDIR:-/tmp}/uv-installer.XXXXXX") || fail "cannot create a temporary file"
    trap 'rm -f "$installer"' EXIT
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$installer_url" -o "$installer" || fail "could not download the uv installer from $installer_url"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$installer" "$installer_url" || fail "could not download the uv installer from $installer_url"
    else
        fail "neither curl nor wget is available to download uv; install uv (https://docs.astral.sh/uv/) and re-run"
    fi
    sh "$installer" || fail "the uv installer failed"
    rm -f "$installer"
    trap - EXIT
}

require_uv_version() {
    reported=$("$1" --version 2>/dev/null) || fail "cannot run $1 --version"
    found=${reported#uv }
    found=${found%% *}
    major=${found%%.*}
    rest=${found#*.}
    minor=${rest%%.*}
    patch=${rest#*.}
    patch=${patch%%[!0-9]*}
    minimum="$MIN_UV_MAJOR.$MIN_UV_MINOR.$MIN_UV_PATCH"
    case "$major.$minor.$patch" in
        *[!0-9.]* | .* | *..* | *.) fail "cannot read the uv version from '$reported'; uv $minimum or newer is required" ;;
    esac
    if [ "$major" -lt "$MIN_UV_MAJOR" ] \
        || { [ "$major" -eq "$MIN_UV_MAJOR" ] && [ "$minor" -lt "$MIN_UV_MINOR" ]; } \
        || { [ "$major" -eq "$MIN_UV_MAJOR" ] && [ "$minor" -eq "$MIN_UV_MINOR" ] && [ "$patch" -lt "$MIN_UV_PATCH" ]; }; then
        fail "uv $found at $1 is too old; uv $minimum or newer is required (run '$1 self update')"
    fi
}

main() {
    package=${XORCHESTRATE_PACKAGE:-xorchestrate}
    repository=${XORCHESTRATE_REPOSITORY:-https://github.com/xormania/xorchestrate}
    installer_url=${UV_INSTALLER_URL:-https://astral.sh/uv/install.sh}
    index_url=${XORCHESTRATE_INDEX_URL:-}
    version=
    git_ref=

    while [ $# -gt 0 ]; do
        case $1 in
            --version) [ $# -ge 2 ] || fail "--version needs X.Y.Z"; version=$2; shift 2 ;;
            --version=*) version=${1#*=}; shift ;;
            --git) [ $# -ge 2 ] || fail "--git needs a tag, branch or commit"; git_ref=$2; shift 2 ;;
            --git=*) git_ref=${1#*=}; shift ;;
            -h | --help) usage; exit 0 ;;
            *) fail "unknown option: $1 (see --help)" ;;
        esac
    done
    [ -z "$version" ] || [ -z "$git_ref" ] || fail "use either --version or --git, not both"

    if uv=$(find_uv); then
        say "using uv at $uv"
    else
        install_uv
        uv=$(find_uv) || fail "uv was installed but cannot be found; open a new shell and re-run"
        say "installed uv at $uv"
    fi
    require_uv_version "$uv"

    if [ -n "$git_ref" ]; then
        requirement="git+$repository@$git_ref"
    elif [ -n "$version" ]; then
        requirement="$package==$version"
    else
        requirement=$package
    fi

    say "installing $requirement with uv tool install"
    if [ -n "$index_url" ]; then
        "$uv" tool install --default-index "$index_url" "$requirement"
    else
        "$uv" tool install "$requirement"
    fi

    bin_dir=$("$uv" tool dir --bin)
    launcher="$bin_dir/$package"
    [ -x "$launcher" ] || fail "uv did not install $launcher"
    identity=$("$launcher" --version) || fail "the installed launcher $launcher does not run"
    say "installed: $identity"

    case ":$PATH:" in
        *":$bin_dir:"*) on_path=yes ;;
        *) on_path=no ;;
    esac

    # The package also installs the short command `xo`; name it when the release has it.
    command_name=$package
    if [ -x "$bin_dir/xo" ]; then
        command_name=xo
    fi

    say "next steps:"
    if [ "$on_path" = no ]; then
        say "  $bin_dir is not on your PATH; run '$uv tool update-shell' and open a new shell"
    fi
    say "  $command_name --version   show the release, how it was installed and its upgrade_command"
    say "  then follow the next steps in $repository/blob/master/docs/install.md"
}

main "$@"
