# Validation

CI runs on GitHub-hosted Ubuntu with Python 3.12. It needs no repository secrets
or authenticated model accounts. The workflow uses pinned action revisions and
read-only repository permissions.

## Active runtime

```bash
python3 -m pip install -r .github/requirements-ci.txt
python3 -m ruff check devtools .github
python3 .github/lint_checks.py
python3 -m devtools.consistency
python3 -m unittest discover -s .github/tests -v
python3 -m devtools.tests.run --report /tmp/xorchestrate-test-census.json
```

The suite reports exact collection, execution, and skips. For the complete CI gate,
install tmux and CUE v0.17.1, then run:

```bash
python3 -m devtools.role_packages --check
python3 -m devtools.configuration --check
AGENTSCIENT_CUE=cue AGENTSCIENT_REQUIRE_TMUX=1 \
  AGENTSCIENT_REQUIRE_ABSTRACT_SOCKETS=1 \
  python3 -m devtools.tests.run --strict --report /tmp/xorchestrate-test-census.json
```

Strict mode fails on skips, unexecuted tests, expected failures, or test failures.
The transport tests exercise real local MCP/process boundaries with synthetic
harnesses. Passing them does not establish authenticated vendor/model acceptance.

## Earlier toolkit

The reusable testkit and conductor have their original tests under
`legacy/agentscient/`. Run only the suites documented in its extraction README;
the entire historical tree is not an active acceptance gate. Its application
fixtures deliberately contain faults and are not production applications.

## Optional integrations

Real Serena/Docker, Symfony/Maker/Mate, model subscriptions, GitHub mutation, and
Jev endpoint acceptance require separately provisioned environments. They are
not silently substituted with passing stubs. Original CI and its tests are kept
under `reference/agentscient-dev/.github/` for reuse; they do not run here.

The retained PHP toolchain helper is an optional dependency of upstream setup
scripts. Its release manifest still names the original retained assets; configure
and provision an appropriate toolchain before using those scripts in a new project.
