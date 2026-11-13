"""Pass loaded developer settings to the test kit's merge points.

The kit takes an optional, already-loaded ``DevConfig`` as ``dev=`` (agreed with the implementer), so tests
never read the real checkout's settings file or the user's file. ``with_dev`` passes it only where the
callable declares the parameter: before the kit reads developer settings, a test then fails on its own
assertion about the value instead of an unexpected keyword. ``SEAMS`` lists where the parameter belongs,
and a test checks it is there.
"""

import inspect

SEAMS = (("scripts.testkit.operator_cli", "settings"), ("scripts.testkit.operator_cli", "main"),
         ("scripts.testkit.adapters.profiles", "load_profile"), ("scripts.testkit.adapters.profiles", "bootstrap"),
         ("scripts.testkit.adapters.profiles", "doctor"), ("scripts.testkit.adapters.profiles", "setup"))


def accepts_dev(function):
    return "dev" in inspect.signature(function).parameters


def with_dev(function, *args, dev, **kwargs):
    if accepts_dev(function):
        kwargs["dev"] = dev
    return function(*args, **kwargs)


def trusted_executable(path, text="#!/bin/sh\nexit 0\n"):
    """A regular file the current user owns, executable and writable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o700)
    return path


def trusted_file(path, text="{}"):
    """A regular, non-executable file the current user owns and only its owner can write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path
