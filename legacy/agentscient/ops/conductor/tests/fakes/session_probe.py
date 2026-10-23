# testkit: exempt fixture program planted as the probed claude executable
"""The executable the claude adapter's probe runs.

The plant substitutes two anchors, MARKER (a file it writes, proving it
ran) and TOKEN (the version text it prints), each written once below.
Its argv is the adapter's, so nothing reaches it by argument. origin: 20260923T003033Z-review-codex-c99cc9#7
"""
from __future__ import annotations

import sys
from pathlib import Path

Path("@@MARKER@@").write_text("ran\n", encoding="utf-8")
sys.stdout.write("@@TOKEN@@" + "\n")
