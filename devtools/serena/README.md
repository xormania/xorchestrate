# Serena image inputs

Build from the repository root:

```sh
docker build --tag agentscient-serena:v2-beta devtools/serena
```

Existing installations must rebuild to adopt these locks, either with that
command or `devtools/scripts/dependencies.sh update --build-serena`. The existing
readiness check verifies Serena's source revision label; it does not distinguish
an older image built from the same source with the previous dependency recipe.

The image also installs Agentscient's observe-only process audit hook in the
interpreter running Serena's REPL. Rebuild and start a new provider to adopt hook
changes; an already-running container keeps its existing code. The hook reports
supported process-launch attempts and startup coverage over stderr for the host
gateway to capture in private telemetry. It neither blocks launches nor turns
unrestricted Python into a sandbox. Child process activity and deliberate bypasses
are not comprehensively observed. Review records and missing coverage with the
[operator script](../OPERATIONS.md#review-serena-process-activity).

The provider remains a separately installed external service. Build inputs are
locked at each dependency boundary:

| Input | Lock |
| --- | --- |
| PHP base and uv installer | Official multi-platform image digests in [Dockerfile](Dockerfile). |
| Debian packages, including Python and mbstring build dependencies | Signed, dated Debian and security archives in [debian.sources](debian.sources). |
| Serena source and Python runtime dependencies | Exact Git revision in the Dockerfile and that revision's own `uv.lock`, installed with `uv sync --locked`. |
| Python build dependencies | Complete version/hash closure in [requirements-build.txt](requirements-build.txt), generated from [requirements-build.in](requirements-build.in). |
| PHPactor | Release URL and SHA-256 in the Dockerfile. |

APT uses only the snapshot sources and fails on any metadata fetch error.
Historical metadata expiry is disabled for those sources; signature verification
remains enabled. The Python environment starts unseeded. Build tools install from
hash-verified wheels before Serena and its source-only dependency are built with
build isolation disabled. This prevents a separate, unpinned build resolver.
The build checks installed dependency compatibility and rejects changes to the
upstream project metadata or lockfile. Runtime dependencies are not copied into
a second lockfile maintained here.

These locks fix dependency selection for a given platform; they do not promise
byte-identical OCI images or upstream artifact availability. Layer caching is
still allowed. Updates, including security updates, require deliberate pin changes.

## Updating

Update the relevant image digests, snapshot dates, source revision and PHPactor
checksum together when their compatibility requires it. Resolve image digests
from the official registries and verify the selected archive is available.
Keep the PHPactor release aligned with the selected Serena source.

When changing Serena or its build tools, use the uv version pinned in the
Dockerfile and a clean checkout of the selected Serena revision. From this
directory, set `serena_source` to that checkout and regenerate the build closure:

```sh
uv export --project "$serena_source" \
  --locked --all-extras --no-emit-project \
  --no-hashes --no-header --no-annotate --offline \
  --output-file /tmp/serena-upstream-constraints.txt

uv pip compile requirements-build.in \
  --constraint /tmp/serena-upstream-constraints.txt \
  --python-version 3.11 --python-platform linux \
  --only-binary :all: --generate-hashes --no-header --no-annotate \
  --index-url https://pypi.org/simple \
  --output-file requirements-build.txt
```

The temporary export only constrains the build tools to versions compatible with
the upstream lock; optional runtime extras are not installed in the image.
Review the generated diff, then run a cold build and the real integration from
the repository root:

```sh
docker build --no-cache --tag agentscient-serena:lock-check devtools/serena
AGENTSCIENT_SERENA_IMAGE=agentscient-serena:lock-check \
  python3 devtools/integration/serena.py
docker image rm agentscient-serena:lock-check
```

CI also builds the image and runs the real MCP REPL/PHPactor acceptance in both
workspace modes. Build success alone is not the acceptance check.
