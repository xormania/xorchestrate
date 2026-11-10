# Releasing xorchestrate

A release is an annotated tag `vMAJOR.MINOR.PATCH` on `master`; a pre-release is
`vMAJOR.MINOR.PATCH-rc.N`. Pushing the tag runs
[`.github/workflows/release.yml`](../.github/workflows/release.yml), which checks
the release, publishes the wheels and source distributions of `xorchestrate` and
its alias `xork` to PyPI and attaches them to the GitHub release. xor decides
when to release.

`xork` ([`packaging/xork`](../packaging/xork)) is a short install name with no
code: its only dependency is `xorchestrate==X.Y.Z` of exactly its own version,
and it declares the same `xo` and `xorchestrate` commands, because `uv tool
install` and `pipx install` expose only the commands of the package they name.
Its version and pin are set by hand with each release; the identity gate refuses
a release where they differ from the package version.

> **TODO (license): the first publish waits on the license.** xorchestrate will be
> licensed under the Business Source License 1.1, and its parameters (licensor,
> Additional Use Grant, Change Date, Change License) are not set yet. Add the
> `LICENSE` file and the `pyproject.toml` license metadata (`BUSL-1.1`), in the
> repository and in `packaging/xork`, before pushing the first release tag, so
> the first published packages carry them.
> A TestPyPI dry run may happen before that.

## What the workflow does

| Job | Runs on | Does |
| --- | --- | --- |
| Build distributions and check release identity | tag push, manual run | builds the wheel and sdist of the repository and of `packaging/xork` with the locked validation tools (`python -m build --no-isolation`), then runs `scripts/ci/release_identity.py` |
| Installed package acceptance of the built distributions | after build | runs [installed package acceptance](package-acceptance.md) on exactly the built files, including the launcher install paths and `xork` |
| Publish | after both | uploads the built files with trusted publishing: to PyPI (environment `pypi`) for a tag push, to TestPyPI (environment `testpypi`) for a manual run |
| Attach distributions to the GitHub release | tag push, after publish | creates the release for the tag with the changelog entry as notes (marked pre-release for `-rc.N`), or uploads the files to the release if it already exists |

The identity gate fails the run unless:

- the tag matches `vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-rc.N`;
- `__version__` in `xorchestrate/__init__.py` and `version` in `pyproject.toml`
  both equal the tag without the `v`;
- `packaging/xork/pyproject.toml` declares that same version, exactly one
  dependency, `xorchestrate==` that version, and the same console scripts as
  `pyproject.toml`;
- `CHANGELOG.md` has an entry headed `## X.Y.Z` (also accepted: `## [X.Y.Z]`,
  `## vX.Y.Z`, optionally followed by a date) with a non-empty `### Upgrade notes`
  section;
- the build produced exactly one wheel and one sdist of `xorchestrate` and of
  `xork`, and nothing else, all carrying that version (in PEP 440 form, so
  `1.3.0-rc.2` becomes `1.3.0rc2` in file names);
- every runtime dependency resolves from a package index: no direct reference,
  no `[tool.uv.sources]` git, path, URL or workspace source for it, and only
  registry packages in `uv.lock`;
- for a tag push, the tag is annotated and its commit is on `master`.

> **Before the first release with xcontracts:** development takes xcontracts
> from a pinned git commit (`[tool.uv.sources]` in `pyproject.toml`), because
> it is not on PyPI yet; the gate refuses that. Once xcontracts 0.1.0 is
> published, delete the `xcontracts` entry under `[tool.uv.sources]` (the
> dependency already reads `xcontracts>=0.1,<0.2`), run
> `python3 -m scripts.ci.dependencies update` and commit `uv.lock`. Until
> then, the pin is a commit of xcontracts' unmerged `feat/initial-contracts`
> branch. **Before the xorchestrate pull request that introduced it merges,**
> merge the xcontracts pull request with a merge commit, repin
> `[tool.uv.sources]` to the xcontracts `master` commit that contains the
> pinned work, and run `python3 -m scripts.ci.dependencies update`, so the
> lock never names a commit only reachable from a deleted branch.

Run the gate locally before tagging:

```sh
python3 scripts/ci/release_identity.py --tag vX.Y.Z
```

Only the publish job can request an OIDC token (`id-token: write`); no PyPI token
is stored anywhere. Only the GitHub release job can write repository contents.

## One-time setup (owner)

1. **PyPI account.** Create an account on <https://pypi.org> and enable two-factor
   authentication. Do the same on <https://test.pypi.org> for dry runs; the two are
   separate services with separate accounts.
2. **Trusted publishers.** Both projects, `xorchestrate` and `xork`, already
   exist on PyPI and TestPyPI: each name is reserved by a `0.0.1` placeholder
   upload, so nobody else can register it. Add a normal (not pending) trusted
   publisher to each project: on PyPI, *Your projects → (project) → Manage →
   Publishing → Add a new publisher → GitHub*, with:

   | Field | Value |
   | --- | --- |
   | Owner | `xormania` |
   | Repository name | `xorchestrate` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   Do this for `xorchestrate` and for `xork`, then repeat both on TestPyPI with
   environment name `testpypi`. One publish step uploads both projects' files
   in name order (`xorchestrate` first), so a missing `xork` publisher fails
   the run after `xorchestrate` was uploaded. The step does not skip existing
   files, so a rerun then fails on the `xorchestrate` files already on the
   index; that version cannot be completed and needs a new patch release. Add
   all four publishers before tagging.
   The placeholders stay in each project's history; a real release has a higher
   version, so installs never pick them unless someone asks for `==0.0.1`.
3. **GitHub environments.** In the repository's *Settings → Environments*, create
   `pypi` and `testpypi`. For `pypi` both of these are required:
   - *Required reviewers*: one or more maintainers who approve every upload;
   - *Deployment branches and tags*: only tags matching `v*.*.*`.

   For `testpypi`, *Required reviewers* is required too. A manual run uses the
   `release.yml` of whichever branch it is dispatched from, so without a
   reviewer anyone who can push a branch and run workflows could publish a
   modified build to the TestPyPI project used for dry runs.

   Nothing else is needed: no secrets.
4. **Tag ruleset.** In *Settings → Rules → Rulesets*, add a tag ruleset targeting
   `v*` that restricts creation, update and deletion to maintainers (bypass list:
   the maintainers or the admin role only).

**Why the reviewers and the ruleset matter.** On a tag push GitHub runs
`release.yml` *as it exists in the tagged commit*. Anyone who can push a `v*` tag
can tag an unmerged commit whose workflow has been modified, for example to skip
the checks, and that run can request the `pypi` OIDC token. The identity gate
(tag, versions, changelog, annotated tag on `master`) runs inside that same
workflow, so it guards against mistakes, not against someone who controls the
tagged commit. What stops that is outside the repository: the tag ruleset limits
who can push release tags, and the required reviewers on `pypi` and `testpypi`
mean a person approves each upload after seeing which commit it comes from.

These are settings outside the repository; this document does not claim they have
been made.

## Release procedure

1. **Changelog.** Turn the `Unreleased` entry of `CHANGELOG.md` into `## X.Y.Z`
   (with the date) and add a new empty `Unreleased` entry above it. Its
   **Upgrade notes** state, for every category, the change and the migration it
   needs, or "None": configuration format (and any rewrite), data version (and
   the migration), lifecycle definitions, adapter options, operator source schema
   and the telemetry collector.
2. **Version.** Set `__version__` in `xorchestrate/__init__.py`, `version` in
   `pyproject.toml`, and both `version` and the `xorchestrate==` pin in
   `packaging/xork/pyproject.toml` to `X.Y.Z` in one commit titled with the
   version. Run
   `python3 -m scripts.ci.dependencies update` so `uv.lock` records it, and commit
   the lock with it.
3. **Release pull request.** Open it. CI runs the normal suites.
   `python3 scripts/ci/release_identity.py` (without `--tag`) checks the version
   and changelog on the branch.
4. **Dry run (optional).** Run the *Release* workflow manually on the release
   branch (*Actions → Release → Run workflow*). It runs every check and publishes
   to TestPyPI. TestPyPI never accepts the same file name twice, so a repeated dry
   run of the same version fails at upload; rehearse again with an `-rc.N` version
   if needed. Check the result with
   `uvx --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match --from xorchestrate==X.Y.Z xorchestrate --version`,
   and the alias with `--from xork==X.Y.Z` instead.
5. **Tag.** Merge, then tag the merge commit on `master` with an annotated tag
   carrying the upgrade notes, and push it:

   ```sh
   git switch master && git pull --ff-only
   git tag -a vX.Y.Z -m "vX.Y.Z" -m "<upgrade notes>"
   git push origin vX.Y.Z
   ```

6. **Publish.** The tag push runs the workflow. A reviewer checks that the run's
   commit is the merged release commit on `master`, then approves the `pypi`
   deployment. When it finishes, both projects' releases are on PyPI and the
   GitHub release for the tag has the wheels and sdists attached, with the
   changelog entry as notes if the workflow created it. Check:

   ```sh
   uvx --from xorchestrate==X.Y.Z xo --version
   uvx --from xork==X.Y.Z xo --version
   ```

7. **Roll out.** In each project, open a pull request changing `release` in
   `.xorchestrate/project.json`; merge when ready. On each machine, pull it,
   `xorchestrate approve` it and run `xorchestrate upgrade` (see
   [upgrading](upgrade.md)).

## Release gates in CI

Besides the identity gate above, the ordinary suites carry these release gates
on every pull request:

| Gate | Where | Checks |
| --- | --- | --- |
| Configuration compatibility | `tests/hexagonal/test_config_versioning.py`, fixtures in [`tests/fixtures/configs/`](../tests/fixtures/configs/README.md) | a fixture exists for every supported format version and loads; `migrate-config` rewrites each older one, and an unversioned one, to the current fixture byte for byte; a configuration one version ahead is refused |
| Data migration chain | `tests/hexagonal/test_data_versioning.py`, fixtures in `tests/fixtures/stores/` | a fixture store exists for every data version the chain migrates from; each is migrated to the current version and read by the application; a newer store is refused |
| Pre-versioned store | `tests/hexagonal/test_data_versioning.py` | a store with `user_version` 0 opens as version 1 |
| Upgrade | `tests/hexagonal/test_upgrade.py` | an end-to-end upgrade between two fake release toolchains migrates with the target release, resumes after a failed migration and is refused while sessions are live |
| Installed upgrade dry run | [installed package acceptance](package-acceptance.md) | the installed launcher plans an upgrade of a fresh project (`upgrade --plan-only`), snapshots and archives its telemetry and checks its configuration format |

A release that bumps the data version commits the new version's fixture store
(`python3 -m tests.hexagonal.support.store_fixtures tests/fixtures/stores/data-vN.db`);
one that bumps the configuration format adds `tests/fixtures/configs/config-vN.json`.

A failed run publishes nothing until the publish job; fix the cause, delete and
recreate the tag only if nothing was uploaded, and push again. A version that
reached PyPI cannot be uploaded again: release a new patch version instead.

To yank a release, yank it for both `xorchestrate` and `xork`. A yanked
`xorchestrate` is still installed by `xork==X.Y.Z`, because its exact pin
selects the yanked version.
