# testkit: exempt the kit suite's planted refuser, a subject
#!/usr/bin/env python3
"""A twenty-line program that speaks the wire: `serve NAME` prints the
name; anything else refuses `arguments` on stderr, exit 2, stdout empty.
Its mutants are declared in test_cases.py."""

import sys


def main(argv):
    if len(argv) == 2 and argv[0] == "serve":
        sys.stdout.write(argv[1] + "\n")
        return 0
    sys.stderr.write(
        "arguments: expected serve NAME; found "
        + (" ".join(argv) or "nothing")
        + "; needed the verb and its one argument\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
