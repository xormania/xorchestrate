"""Test-only MCP entry point; production has no configurable API endpoint tool."""

import os
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from devtools import github, run, watcher
    from devtools.github_http import GitHubHTTP

    class FixtureHTTP(GitHubHTTP):
        def __init__(self, config=None):
            super().__init__(
                config, api_root=os.environ["FIXTURE_GITHUB_ROOT"], credential=lambda: "fixture-credential"
            )

    github.GitHubHTTP = FixtureHTTP
    watcher.ENTRY = Path(__file__).resolve()
    return run.main()


if __name__ == "__main__":
    raise SystemExit(main())
