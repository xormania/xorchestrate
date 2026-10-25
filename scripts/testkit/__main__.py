"""Reusable live-test entrypoint: the same composed checks locally and in CI."""

import argparse
import json
from pathlib import Path

from .composition import MODES, ROOT, SUITES, preflight, run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "doctor"))
    parser.add_argument("--suite", choices=SUITES, default="all")
    parser.add_argument("--mode", choices=MODES, default="protocol-fixture")
    parser.add_argument("--config", type=Path, help="Reusable native application config")
    parser.add_argument("--driver", help="Native acceptance driver name from the config")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path, default=ROOT.parent / "xorchestrate-runs")
    parser.add_argument("--brief", type=Path, help="Owner brief; copied and hash-bound for each run")
    args = parser.parse_args(argv)
    options = dict(suite=args.suite, mode=args.mode, config=args.config, driver=args.driver, timeout=args.timeout)
    try:
        if args.action == "doctor":
            result = preflight(**options)
        else:
            result = run(output=args.output, brief=args.brief, **options)
    except (OSError, ValueError, TypeError) as error:
        result = {"status": "setup_error", "type": type(error).__name__, "diagnostic": str(error)}
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
