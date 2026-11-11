"""Start a hosted command with an exact environment that never appears in a process argv.

Run as ``python -I terminal_exec.py ENVIRONMENT_FILE -- ARGV...``. It reads the
environment (a private JSON object written by the terminal host), removes the
file, and replaces itself with the command, so the command keeps this process's
identity. Command lines are readable by other users; environments are not.
Standard library only, so it runs in isolated mode from its own path.
"""

import json
import os
import sys


def main(arguments):
    if len(arguments) < 3 or arguments[1] != "--":
        sys.stderr.write("usage: terminal_exec.py ENVIRONMENT_FILE -- ARGV...\n")
        return 64
    path, argv = arguments[0], arguments[2:]
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            metadata = os.fstat(descriptor)
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise PermissionError("the environment file is not private")
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = None
                environment = json.load(stream)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        sys.stderr.write("terminal_exec: invalid environment\n")
        return 65
    os.execvpe(argv[0], argv, environment)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
