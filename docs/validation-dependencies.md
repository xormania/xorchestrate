# Reproducible validation dependencies

`uv.lock` is the resolved validation baseline. `pyproject.toml` keeps the public
package dependency ranges and optional integration selection; its `validation`
dependency group declares the exact Ruff and Zizmor validation tools alongside
packaging tools. Zizmor 1.32.0 is installed as an executable rather than an
importable Python module. Each exact `build-system.requires` declaration also
appears in that group, so native uv resolution includes the backend requirements;
update the paired declarations together when changing them. CI selects Python 3.12.16 and
uv 0.13.7. The lock records transitive packages and artifact identities. It does not claim to freeze operating-system packages
or vendor/model services.

Run this before installation to reject a stale lock or a modified generated export:

```sh
python3 -m scripts.ci.dependencies check
```

The check first verifies that build requirements are included in the validation
group, then invokes native `uv lock --check` and `uv export --locked`, compares the
validation export to `.github/requirements-ci.txt`, and never repairs either input.
The requirements file remains available for existing static evidence and pip users;
edit the validation group and regenerate instead of hand-editing that export.

Select an environment outside the candidate before a locked installation:

```sh
export UV_PROJECT_ENVIRONMENT="$(mktemp -d "${TMPDIR:-/tmp}/xor-validation.XXXXXX")"
uv sync --locked --only-group validation --no-install-project --managed-python --python 3.12.16
uv sync --locked --no-default-groups --group validation --extra test --no-install-project --managed-python --python 3.12.16
uv pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" --no-deps --no-build-isolation -e .
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
```

Omit `--extra test` for static/package-build tooling; the telemetry collector ships
with the application and needs no extra. `--no-install-project` installs the locked
tools before source builds, so their backend is already installed. The editable
installation uses that backend without resolving another build environment. Package builders
likewise use this Python with `python -m build --no-isolation`.

Managed test-kit setup installs the generated validation requirements into its
selected environment. Its readiness probe observes the `zizmor` executable beside
that environment's Python without executing the scanner; this keeps reusable
profiles independent of whether the environment's `bin` directory is active on
the caller's `PATH`.

Dependency auditing uses the already pinned uv 0.13.7 runtime manager itself, not
another Python validation dependency, so it does not add a declaration to
`pyproject.toml`, `uv.lock` or `.github/requirements-ci.txt`. Test-kit preparation
resolves uv from the explicit execution environment, verifies its exact version,
and binds `pyproject.toml` plus `uv.lock` before invoking the preview audit command.
CI gets that executable from the checksum-pinned `setup-uv` step. The resulting
OSV observation is network- and time-dependent advisory evidence; it neither
changes the resolved baseline nor replaces the offline dependency consistency
check above.

Update the reviewed baseline intentionally:

```sh
python3 -m scripts.ci.dependencies update              # account for changed declarations
python3 -m scripts.ci.dependencies update --upgrade    # deliberately refresh compatible versions
python3 -m scripts.ci.dependencies check
```

Commit `pyproject.toml` when declarations changed, `uv.lock`, and the generated
`.github/requirements-ci.txt` together. Review the dependency/source changes and
run the affected checks. A new resolver result is not itself compatibility proof.

For independent consumers or build isolation, export constraints from the same lock:

```sh
python3 -m scripts.ci.dependencies export --profile runtime --output /tmp/xor-runtime.txt
python3 -m scripts.ci.dependencies export --profile validation --output /tmp/xor-build.txt
```

The runtime profile excludes validation tools and optional extras. The `tests`
profile adds the `test` extra explicitly. Exports omit the source
project and hashes so pip/uv consumers can install the selected wheel or sdist
while constraining its dependencies; `uv.lock` remains the artifact identity record.

For fresh-resolution compatibility evidence, create a disposable worktree at the
candidate revision, run `update --upgrade` there, install with the commands above,
and run the same selected test-kit suites. Retain the candidate identity, resolved
lock and actual outcomes outside that worktree, labeling them moving-input
compatibility evidence. Do not overwrite the shared regression baseline to run
this experiment. No scheduled update or compatibility run is introduced.
