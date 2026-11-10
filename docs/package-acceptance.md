# Installed package acceptance

`python -m scripts.ci.package_acceptance --output /outside/checkout/package-runs`
builds a wheel and source distribution from a captured candidate, then installs
each into its own fresh environment outside the checkout. Run it using the locked
validation environment described in [CI](../.github/CI.md). The builder needs the
`validation` group; the consumers install only the package's base runtime.

The locked builder runs `python -m build --no-isolation`. Runtime dependency
constraints are exported from `uv.lock`; the source distribution's isolated build
also uses constraints exported from the validation group. Package support ranges
remain those declared in `pyproject.toml`.

Each consumer checks the installed console command, creates persisted work in
one process and reads it from another, then reads the same work through a real
stdio MCP connection. It also starts the installed native gateway by absolute
script filename and verifies its local transport. This gateway fixture does not
establish authenticated Claude, Grok or Codex acceptance.

The consumer runs from a private directory with isolated Python inputs. It checks
that the imported package belongs to the consumer environment and that optional
telemetry, pytest, Ruff and build tools were not installed as runtime dependencies.
The two consumers do not share state or dependencies.

The report binds artifact filenames, sizes and SHA-256 values to the candidate,
runtime and dependency exports. Existing command receipts retain build, install
and protocol failures. Nested consumer failures retain each output stream's final
16,384 characters, with an explicit marker when truncated. CI retains
`package-acceptance-evidence`, including the
report, command logs and the actual distribution files. These are test artifacts;
the workflow does not publish a release.

For a disposable fault witness, `--artifact /path/to/package.whl` checks an existing
artifact instead of building. For example, remove an essential runtime module
from a copied wheel, keep its RECORD consistent, and confirm installation succeeds
but the installed public command fails for that missing module. Re-run with the
healthy artifact as a control. The command returns nonzero and retains its report
when any selected artifact fails; an empty or failed build cannot establish a pass.
