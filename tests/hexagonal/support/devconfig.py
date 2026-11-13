"""A throwaway repository, HOME and settings files for the developer-settings tests.

The settings layer is imported when a test asks for it, so a missing module fails
that test instead of breaking collection.
"""

import contextlib
import importlib
import io
import os
import pwd
import socket
from pathlib import Path

from tests.hexagonal.support.git_repositories import isolate, linked_tree, make_repository

ROOT = Path(__file__).resolve().parents[3]


def personal_markers():
    """This machine's account name (as a path component), home directory and host name, read at run time.

    Tests use them to show that no published text names the machine it was written on; nothing
    local is ever written into the tests themselves.
    """
    account = pwd.getpwuid(os.getuid())
    markers = {f"/{account.pw_name}/", account.pw_dir.rstrip("/") + "/"}
    host = socket.gethostname()
    if len(host) >= 4:
        markers.add(host)
    return {marker for marker in markers if len(marker) > 2}


def names_someone(text, home_paths):
    """Whether ``text`` contains an absolute home path or one of this machine's personal markers."""
    return any(prefix in text for prefix in home_paths) or any(marker in text for marker in personal_markers())


def devconfig(name=""):
    return importlib.import_module("scripts.devconfig" + ("." + name if name else ""))


class SettingsWorld:
    """HOME, a main checkout with one linked tree, and helpers to write each settings file."""

    def __init__(self, base, monkeypatch):
        base = Path(base)
        self.home = base / "home"
        self.home.mkdir()
        isolate(monkeypatch, self.home)
        _, self.main = make_repository(base)
        self.tree = linked_tree(self.main, base / "linked" / "feature", "feature")
        self.base = base

    def env(self, **extra):
        values = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        values.update({name: str(value) for name, value in extra.items()})
        return values

    def repo_file(self, text, where=None):
        path = (where or self.main) / ".devtools.env"
        path.write_text(text, encoding="utf-8")
        return path

    def user_file(self, text, env=None):
        config = (env or {}).get("XDG_CONFIG_HOME") or str(self.home / ".config")
        path = Path(config) / "xorchestrate" / "devtools.env"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, repo=None, *, env=None, flags=None):
        loader = devconfig("loader")
        return loader.load(repo or self.main, env=self.env() if env is None else env, flags=flags or {})

    def error(self, **kwargs):
        model = devconfig("model")
        try:
            self.load(**kwargs)
        except model.DevConfigError as error:
            return error
        raise AssertionError("expected a DevConfigError")

    def cli(self, *argv, cwd=None):
        """Run ``python3 -m scripts.devconfig`` in-process; returns (code, stdout + stderr)."""
        main = devconfig("__main__").main
        out = io.StringIO()
        previous = Path.cwd()
        os.chdir(cwd or self.main)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                try:
                    code = main(list(argv), env=self.env())
                except SystemExit as exit_:
                    code = exit_.code if isinstance(exit_.code, int) else 2
        finally:
            os.chdir(previous)
        return code, out.getvalue()
