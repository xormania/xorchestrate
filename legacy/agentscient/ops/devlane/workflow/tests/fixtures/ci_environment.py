"""Emit only the environment fields the CI isolation test observes."""

import json
import os
import sys

with open(sys.argv[1], "w", encoding="utf-8") as output:
    json.dump({key: os.environ.get(key) for key in
               ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR",
                "GITHUB_TOKEN", "WF_AGENT", "GIT_CONFIG_GLOBAL")}, output)
