# Install xorchestrate

Every install path below puts the same thing on your machine: the `xorchestrate`
launcher, installed for your user, without root. Pick one path; the next steps
are the same whichever you use.

xorchestrate needs Python 3.12 or newer, which uv provides when your system has
none. uv is the only hard requirement; the install script installs it if missing.

## Choose a path

| Path | Command | Status |
| --- | --- | --- |
| Package index | `uv tool install xorchestrate` or `pipx install xorchestrate` | works once the first release is published to PyPI (see below) |
| Git | `uv tool install git+https://github.com/xormania/xorchestrate@vX.Y.Z` | works today |
| Install script | `curl -LsSf https://raw.githubusercontent.com/xormania/xorchestrate/master/scripts/install.sh \| sh` | installs uv if missing, then the launcher from the package index (or git) |
| No install | `uvx --from <source> xorchestrate <command>` | runs the launcher from uv's cache each time |

Replace `vX.Y.Z` with a release tag, for example the newest one listed on the
repository's releases page.

### Package index

```sh
uv tool install xorchestrate          # or: pipx install xorchestrate
uv tool install xorchestrate==X.Y.Z   # a specific release
```

**Not available yet.** Releases are published to PyPI automatically when a
release tag is pushed, but no release has been published so far. Until then,
`uv tool install xorchestrate` fails with "not found"; use the git path.

### Git

```sh
uv tool install git+https://github.com/xormania/xorchestrate@vX.Y.Z
pipx install git+https://github.com/xormania/xorchestrate@vX.Y.Z
```

The tag is pinned in the install. `xorchestrate --version` reports the commit it
resolved.

### Install script

Read the script first; it is short:
[`scripts/install.sh`](../scripts/install.sh).

```sh
curl -LsSf https://raw.githubusercontent.com/xormania/xorchestrate/master/scripts/install.sh | sh
curl -LsSf https://raw.githubusercontent.com/xormania/xorchestrate/master/scripts/install.sh | sh -s -- --version X.Y.Z
curl -LsSf https://raw.githubusercontent.com/xormania/xorchestrate/master/scripts/install.sh | sh -s -- --git vX.Y.Z
```

It:

1. uses uv from your `PATH` (or `~/.local/bin`); if there is none, installs uv for
   your user with uv's official installer (`https://astral.sh/uv/install.sh`);
2. runs `uv tool install xorchestrate` (or `xorchestrate==X.Y.Z`, or
   `git+…@REF` with `--git REF`);
3. prints the installed `--version` and the next steps.

It never uses `sudo`, and running it again is safe: an installed launcher is kept
unless you ask for another release. Until the first PyPI release, use
`--git vX.Y.Z`. Settings: `XORCHESTRATE_INDEX_URL` selects another package index,
`XORCHESTRATE_REPOSITORY` another git repository, and `UV_INSTALLER_URL` another
uv installer.

### No install (`uvx`)

```sh
uvx --from git+https://github.com/xormania/xorchestrate@vX.Y.Z xorchestrate --version
uvx --from xorchestrate==X.Y.Z xorchestrate --version   # once published to PyPI
```

uv builds a cached environment on first use and reuses it afterwards. Nothing is
put on your `PATH`, so every command is prefixed with `uvx --from …`.

## Next steps

These are the same for every path.

1. Check the installation:

   ```sh
   xorchestrate --version
   ```

   It prints JSON with the release `version`, the `commit` for a git install,
   `install_source` (`index`, `git`, `local`, `archive` or `uvx`) and the
   `upgrade_command` that upgrades the launcher the way it was installed.

2. If the command is not found, uv's tool directory is not on your `PATH`: run
   `uv tool update-shell` and open a new shell.

3. Use it from a project directory, as described in the
   [README](../README.md#run-locally) and the
   [configuration examples](../examples/README.md):

   ```sh
   xorchestrate describe
   xorchestrate --config /absolute/path/team.json serve
   ```

`xorchestrate setup` (once per user) and `xorchestrate init` (once per project)
are not available yet. They arrive with the project-configuration work and will
then replace step 3.

## Upgrade and uninstall

Run the `upgrade_command` from `xorchestrate --version`. For example:

| Installed with | Upgrade | Uninstall |
| --- | --- | --- |
| `uv tool install xorchestrate` | `uv tool upgrade xorchestrate` | `uv tool uninstall xorchestrate` |
| `pipx install xorchestrate` | `pipx upgrade xorchestrate` | `pipx uninstall xorchestrate` |
| `uv tool install git+…@vX.Y.Z` | `uv tool install --force git+…@<new tag>` | `uv tool uninstall xorchestrate` |
| `uvx --from …` | `uvx --from xorchestrate@latest xorchestrate` (index) or a new `--from` | nothing to remove; `uv cache clean` frees the cache |

## Install paths checked in CI

The [installed package acceptance](package-acceptance.md) job builds the wheel and
source distribution, then installs each into a clean environment and checks the
public commands. It also installs the launcher through `uv tool install` from a
private flat index (the package-index path, offline), from the wheel file, from a
local git repository at a release tag, and from a source directory, and runs it
through `uvx --from <wheel>`. Each must report the matching `install_source`.
The public PyPI path and `pipx` are not exercised in CI.
