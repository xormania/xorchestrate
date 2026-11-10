# Releasing xorchestrate

A release is an annotated tag `vMAJOR.MINOR.PATCH` on `master`; a pre-release is
`vMAJOR.MINOR.PATCH-rc.N`. Pushing the tag runs
[`.github/workflows/release.yml`](../.github/workflows/release.yml), which checks
the release, publishes the wheel and source distribution to PyPI and attaches them
to the GitHub release. xor decides when to release.

> **TODO (license): the first publish waits on the license.** xorchestrate will be
> licensed under the Business Source License 1.1, and its parameters (licensor,
> Additional Use Grant, Change Date, Change License) are not set yet. Add the
> `LICENSE` file and the `pyproject.toml` license metadata (`BUSL-1.1`) before
> pushing the first release tag, so the first published package carries them.
> A TestPyPI dry run may happen before that.

## What the workflow does

| Job | Runs on | Does |
| --- | --- | --- |
| Build distributions and check release identity | tag push, manual run | builds the wheel and sdist with the locked validation tools (`python -m build --no-isolation`), then runs `scripts/ci/release_identity.py` |
| Installed package acceptance of the built distributions | after build | runs [installed package acceptance](package-acceptance.md) on exactly the built files, including the launcher install paths |
| Publish | after both | uploads the built files with trusted publishing: to PyPI (environment `pypi`) for a tag push, to TestPyPI (environment `testpypi`) for a manual run |
| Attach distributions to the GitHub release | tag push, after publish | creates the release for the tag with the changelog entry as notes (marked pre-release for `-rc.N`), or uploads the files to the release if it already exists |

The identity gate fails the run unless:

- the tag matches `vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-rc.N`;
- `__version__` in `xorchestrate/__init__.py` and `version` in `pyproject.toml`
  both equal the tag without the `v`;
- `CHANGELOG.md` has an entry headed `## X.Y.Z` (also accepted: `## [X.Y.Z]`,
  `## vX.Y.Z`, optionally followed by a date) with a non-empty `### Upgrade notes`
  section;
- the build produced exactly one wheel and one sdist carrying that version (in
  PEP 440 form, so `1.3.0-rc.2` becomes `1.3.0rc2` in file names);
- for a tag push, the tag is annotated and its commit is on `master`.

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
2. **Trusted publishers.** The project `xorchestrate` does not exist on PyPI yet,
   so register a *pending* publisher: on PyPI, *Your account → Publishing → Add a
   new pending publisher → GitHub*, with:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `xorchestrate` |
   | Owner | `xormania` |
   | Repository name | `xorchestrate` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   Repeat on TestPyPI with environment name `testpypi`. The project is created
   on the first upload from this workflow.

   **A pending publisher does not reserve the name.** Until the first upload,
   anyone can register a project called `xorchestrate` on PyPI; if that happens,
   the pending publisher is invalidated and the name is lost to this project.
   The name was unclaimed when checked (2026-11-10), but nothing holds it. The
   only way to hold it is to upload a release, so the gap between this setup and
   the first publish should be short.
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
2. **Version.** Set `__version__` in `xorchestrate/__init__.py` and `version` in
   `pyproject.toml` to `X.Y.Z` in one commit titled with the version. Run
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
   `uvx --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match --from xorchestrate==X.Y.Z xorchestrate --version`.
5. **Tag.** Merge, then tag the merge commit on `master` with an annotated tag
   carrying the upgrade notes, and push it:

   ```sh
   git switch master && git pull --ff-only
   git tag -a vX.Y.Z -m "vX.Y.Z" -m "<upgrade notes>"
   git push origin vX.Y.Z
   ```

6. **Publish.** The tag push runs the workflow. A reviewer checks that the run's
   commit is the merged release commit on `master`, then approves the `pypi`
   deployment. When it finishes, the release is on PyPI and the GitHub
   release for the tag has the wheel and sdist attached, with the changelog entry
   as notes if the workflow created it. Check:

   ```sh
   uvx --from xorchestrate==X.Y.Z xorchestrate --version
   ```

7. **Roll out.** In the companion, open a pull request changing `default_release`
   or specific projects' `release`; merge when ready, then upgrade instances one by
   one.

A failed run publishes nothing until the publish job; fix the cause, delete and
recreate the tag only if nothing was uploaded, and push again. A version that
reached PyPI cannot be uploaded again: release a new patch version instead.
