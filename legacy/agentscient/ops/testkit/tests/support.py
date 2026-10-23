# testkit: exempt the kit suite's own support leaf
"""Shared helpers of the kit's own suite: the kit's path, the fixture
refuser, and a sleeper the world must reap."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KIT = TESTS.parent
REPO = KIT.parents[1]
FIXTURES = TESTS / "fixtures"
REFUSER = FIXTURES / "refuser.py"
PYTHON = sys.executable

SLEEPER = [PYTHON, "-c", "import time; time.sleep(60)"]
