"""Standard unittest discovery with a checked collection and an honest census."""

import argparse
import collections
import json
import re
import sys
import unittest
from pathlib import Path


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def collect(suite):
    selected = list(cases(suite))
    counts = collections.Counter(test.id() for test in selected)
    if not counts:
        raise ValueError("Test discovery collected zero cases")
    duplicate = sorted(ident for ident, count in counts.items() if count != 1)
    if duplicate:
        raise ValueError("Duplicate test IDs: " + ", ".join(duplicate))
    return selected


class Result(unittest.TextTestResult):
    def __init__(self, *args, collected, **kwargs):
        super().__init__(*args, **kwargs)
        self.collected = {test.id(): test for test in collected}
        self.started, self.skipped_cases = set(), {}
        self.skipped_subtests = 0

    def startTest(self, test):
        self.started.add(test.id())
        super().startTest(test)

    def addSkip(self, test, reason):
        ident = test.id()
        if ident in self.collected:
            self.skipped_cases[ident] = reason
        elif match := re.fullmatch(r"setUp(Class|Module) \((.+)\)", ident):
            prefix = match[2] + "."
            self.skipped_cases.update((name, reason) for name in self.collected if name.startswith(prefix))
        elif isinstance(test, unittest.case._SubTest):
            self.skipped_subtests += 1
        super().addSkip(test, reason)

    def census(self):
        skipped = set(self.skipped_cases)
        executed = self.started - skipped
        return {"collected": len(self.collected), "executed": len(executed), "skipped": len(skipped),
                "not_run": len(set(self.collected) - executed - skipped),
                "failures": len(self.failures), "errors": len(self.errors),
                "expected_failures": len(self.expectedFailures), "unexpected_successes": len(self.unexpectedSuccesses),
                "skipped_subtests": self.skipped_subtests,
                "skipped_cases": [{"id": ident, "reason": reason} for ident, reason in sorted(self.skipped_cases.items())]}


def run(suite, *, stream=None, verbosity=1, durations=None):
    selected = collect(suite)

    def resultclass(*args, **kwargs):
        return Result(*args, collected=selected, **kwargs)

    options = {"stream": stream or sys.stderr, "verbosity": verbosity, "resultclass": resultclass}
    if durations is not None:
        options["durations"] = durations
    result = unittest.TextTestRunner(**options).run(suite)
    census = result.census()
    options["stream"].write("Test census: " + ", ".join(
        f"{name}={census[name]}" for name in ("collected", "executed", "skipped", "not_run")) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--start-directory", default="devtools/tests")
    parser.add_argument("-p", "--pattern", default="test*.py")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--durations", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="Require every collected test to execute successfully, with no skips or expected failures")
    parser.add_argument("--allow-skip", action="append", default=[], metavar="TEST_ID",
                        help="In strict mode, permit this exact collected test ID to skip because another gate requires it")
    args = parser.parse_args(argv)
    if args.allow_skip and not args.strict:
        parser.error("--allow-skip requires --strict")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"state": "collecting"}) + "\n")
    loader = unittest.TestLoader()
    try:
        suite = loader.discover(args.start_directory, pattern=args.pattern)
        if loader.errors:
            raise ValueError("Test discovery failed:\n" + "\n".join(loader.errors))
        allowed = set(args.allow_skip)
        if allowed and (unknown := allowed - {test.id() for test in collect(suite)}):
            raise ValueError("Allowed skip IDs were not collected: " + ", ".join(sorted(unknown)))
        result = run(suite, verbosity=2 if args.verbose else 1, durations=args.durations)
    except (ImportError, ValueError) as error:
        if args.report:
            args.report.write_text(json.dumps({"state": "collection_failed", "error": str(error)}, indent=2) + "\n")
        print(str(error), file=sys.stderr)
        return 2
    census = result.census()
    omitted = {key: census[key] for key in ("not_run", "skipped_subtests", "expected_failures") if census[key]}
    if unexpected := set(result.skipped_cases) - allowed:
        omitted["unexpected_skips"] = len(unexpected)
    if not census["executed"]:
        omitted["no_executed_tests"] = 1
    passed = result.wasSuccessful() and not (args.strict and omitted)
    if args.strict and omitted:
        print("Strict test execution failed: " + ", ".join(f"{key}={value}" for key, value in omitted.items()), file=sys.stderr)
    if args.report:
        args.report.write_text(json.dumps({"state": "passed" if passed else "failed", **census}, indent=2) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
