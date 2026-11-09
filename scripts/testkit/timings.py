"""Regenerate contracts/test-timings.json from recorded JUnit reports.

Usage: python -m scripts.testkit.timings RUN/tests.xml [SHARD/tests.xml ...] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters.timings import regenerate
from .composition import ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aggregate JUnit test durations per catalog module")
    parser.add_argument("reports", nargs="+", type=Path, help="JUnit XML written by the tests suite or a shard")
    parser.add_argument("--output", type=Path, help="Timings file to write (default: contracts/test-timings.json)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = regenerate(ROOT, args.reports, args.output)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "setup_error", "type": type(error).__name__, "diagnostic": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
