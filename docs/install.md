# Install xorchestrate

Every install path below puts the same thing on your machine: the xorchestrate
launcher, installed for your user, without root. Pick one path; the next steps
are the same whichever you use.

The launcher has two command names: `xo`, the short one used in these docs, and
`xorchestrate`. They run the same program with the same options; use whichever
you prefer. Files the launcher writes for other programs, such as the `.mcp.json`
entry from `init`, use `xorchestrate`, the name no other program is likely to
have (`xo` is also, for example, the name of a JavaScript linter). If another
`xo` comes first on your `PATH`, use `xorchestrate`.

xorchestrate needs Python 3.12 or newer, which uv provides when your system has
none. uv is the only hard requirement; the install script installs it if missing.

## Choose a path

| Path | Command | Status |
| --- | --- | --- |
| Package index | `uv tool install xork` (short for `uv tool install xorchestrate`), or `pipx install xork` | works once the first release is published to PyPI (see below) |
| Git | `uv tool install git+https://github.com/xormania/xorchestrate@vX.Y.Z` | works today |
| Install script | `curl -LsSf https://raw.githubusercontent.com/xormania/xorchestrate/master/scripts/install.sh \| sh` | installs uv if missing, then the launcher from the package index (or git) |
| No install | `uvx --from <source> xo <command>` | runs the launcher from uv's cache each time |

Replace `vX.Y.Z` with a release tag, for example the newest one listed on the
repository's releases page.

### Package index

```sh
uv tool install xork                  # or: pipx install xork
uv tool install xork==X.Y.Z           # a specific release
uv tool install xorchestrate          # the same, by the package's full name
```

`xork` is a short install name: a package without code whose only dependency is
the `xorchestrate` release of exactly the same version, and which provides the
same `xo` and `xorchestrate` commands. Every release publishes both, with the
same version. Upgrade and uninstall by the name you installed.

**Not available yet.** Releases are published to PyPI automatically when a
release tag is pushed, but no release has been published so far; both names are
reserved by `0.0.1` placeholders that contain nothing usable. Until the first
release, use the git path.

### Git

```sh
uv tool install git+https://github.com/xormania/xorchestrate@vX.Y.Z
pipx install git+https://github.com/xormania/xorchestrate@vX.Y.Z
```

The tag is pinned in the install. `xo --version` reports the commit it
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

1. uses uv from your `PATH` (or `~/.local/bin`, `$CARGO_HOME/bin`); if there is
   none, downloads uv's official installer (`https://astral.sh/uv/install.sh`) to a
   temporary file and runs it, installing uv for your user;
2. runs `uv tool install xorchestrate` (or `xorchestrate==X.Y.Z`, or
   `git+…@REF` with `--git REF`);
3. prints the installed `--version` and the next steps, naming `xo` when the
   installed release provides it.

It needs uv 0.4.23 or newer and stops with that message for an older uv. The whole
script runs from a function called on its last line, so a partly downloaded copy
does nothing. It never uses `sudo`, and running it again is safe: an installed launcher is kept
unless you ask for another release. Until the first PyPI release, use
`--git vX.Y.Z`. Settings: `XORCHESTRATE_INDEX_URL` selects another package index,
`XORCHESTRATE_REPOSITORY` another git repository, and `UV_INSTALLER_URL` another
uv installer.

### No install (`uvx`)

```sh
uvx --from git+https://github.com/xormania/xorchestrate@vX.Y.Z xo --version
uvx --from xorchestrate==X.Y.Z xo --version   # once published to PyPI
```

uv builds a cached environment on first use and reuses it afterwards. Nothing is
put on your `PATH`, so every command is prefixed with `uvx --from …`.

## Next steps

These are the same for every path.

1. Check the installation:

   ```sh
   xo --version
   ```

   It prints JSON with the release `version`, the `commit` for a git install,
   `install_source` (`index`, `git`, `local`, `archive`, `uvx`, or `unknown`) and the
   `upgrade_command` that upgrades the launcher the way it was installed.

2. If the command is not found, uv's tool directory is not on your `PATH`: run
   `uv tool update-shell` and open a new shell.

3. Set it up once per user, then once per project (see [projects](projects.md)):

   ```sh
   xo setup ~/companion     # create (or link) your private companion; runs doctor
   cd /path/to/project      # a git repository
   xo init                  # registers this machine's instance, writes .mcp.json for a new project
   xo approve               # approve the tracked .xorchestrate/ configuration
   xo describe
   ```

   Agent harnesses then run `xorchestrate connect` (the entry `init` writes
   into Claude Code's `.mcp.json`, by the long name); the project server starts
   by itself. The
   launcher hands each project command to the release the project pins in
   `.xorchestrate/project.json`, building that toolchain with uv when needed.

## Upgrade and uninstall

Run the `upgrade_command` from `xo --version`. For example:

| Installed with | Upgrade | Uninstall |
| --- | --- | --- |
| `uv tool install xork` | `uv tool upgrade xork` | `uv tool uninstall xork` |
| `uv tool install xorchestrate` | `uv tool upgrade xorchestrate` | `uv tool uninstall xorchestrate` |
| `uv tool install xorchestrate==X.Y.Z` (also the install script's `--version`) | `uv tool install xorchestrate@latest` | `uv tool uninstall xorchestrate` |
| `pipx install xorchestrate` | `pipx upgrade xorchestrate` | `pipx uninstall xorchestrate` |
| `pipx install xorchestrate==X.Y.Z` | `pipx install --force xorchestrate` | `pipx uninstall xorchestrate` |
| `uv tool install git+…@vX.Y.Z` | `uv tool install --force git+…@<new tag>` | `uv tool uninstall xorchestrate` |
| `uvx --from …` | `uvx --from xorchestrate@latest xorchestrate` (index) or a new `--from` | nothing to remove; `uv cache clean` frees the cache |

A pinned install stays pinned: `uv tool upgrade` and `pipx upgrade` keep a
recorded `==X.Y.Z` (uv says "Nothing to upgrade"), so the launcher reinstalls it
without the pin instead. If the launcher cannot find its own distribution,
`install_source` is `unknown` and `upgrade_command` is `null`.

## Install paths checked in CI

The [installed package acceptance](package-acceptance.md) job builds the wheel and
source distribution, then installs each into a clean environment and checks the
public commands, through both `xo` and `xorchestrate`. It does the same for the
`xork` wheel and sdist, which must install the candidate xorchestrate. It also
installs the launcher through `uv tool install` from a private flat index (the
package-index path, offline, as `xorchestrate` and as `xork`), from the wheel
file, from a local git repository at a release tag, and from a source directory,
and runs it through `uvx --from <wheel>`. Each must report the matching
`install_source`, with `xo` reporting the same as `xorchestrate`. The public PyPI
path and `pipx` are not exercised in CI.
