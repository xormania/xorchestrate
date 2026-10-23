"""Unittest discovery that rejects zero collected cases on every Python version."""

from __future__ import annotations

import sys
import unittest


def cases(tests):
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    index, count = 0, 1
    excluded = []
    while len(argv) >= 2 and argv[0] == "--exclude-case":
        excluded.append(argv[1])
        argv = argv[2:]
    if len(argv) == 4 and argv[0] == "--partition":
        try:
            index, count = map(int, argv[1].split("/"))
            if not 0 <= index < count:
                raise ValueError("invalid partition")
        except ValueError:
            print("suite: expected INDEX/COUNT within bounds; found invalid partition; needed 0 <= INDEX < COUNT", file=sys.stderr)
            return 2
        argv = argv[2:]
    if len(argv) != 2:
        print("suite: expected DIRECTORY PATTERN; found invalid arguments; needed a discovery pair",
              file=sys.stderr)
        return 2
    loader = unittest.TestLoader()
    tests = loader.discover(argv[0], pattern=argv[1])
    if excluded and not loader.errors:
        discovered = list(cases(tests))
        absent = set(excluded) - {test.id() for test in discovered}
        if absent:
            print(f"suite: excluded cases absent: {sorted(absent)}", file=sys.stderr)
            return 2
        for name in sorted(excluded):
            print(f"EXCLUDED {name}: outside this verification scope", flush=True)
        tests = unittest.TestSuite(test for test in discovered if test.id() not in excluded)
    # An import error is never filtered out by a partition. Each child reads
    # the whole discovery result, then executes its disjoint set of real cases.
    if count > 1 and not loader.errors:
        discovered = sorted(cases(tests), key=lambda test: test.id())
        tests = unittest.TestSuite(discovered[index::count])
    if not tests.countTestCases():
        print(f"suite: expected collected test cases; found zero in {argv[0]} ({argv[1]}); needed executable tests",
              file=sys.stderr)
        return 2
    result = unittest.TextTestRunner().run(tests)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
