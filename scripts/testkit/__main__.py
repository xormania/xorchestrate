"""Test framework entrypoint: shared contracts, calibration and live workflows."""

from .composition import preflight, run
from .operator_cli import main as operator_main


def main(argv=None):
    return operator_main(argv, direct_runner=run, preflight=preflight)


if __name__ == "__main__":
    raise SystemExit(main())
