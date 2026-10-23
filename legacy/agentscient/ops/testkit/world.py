"""The world a test runs in: a temporary root that owns HOME, the XDG
roots, TMPDIR, the working directory, a hermetic environment, planted
files and tools, owned child processes, and before/after snapshots of
declared paths. Hermetic by default: nothing from the host reaches a
subject unless it is named — values in ``ambient``, tools in
``ambient_tools``. Owned children are reaped at close. Daemons are
recorded by ``daemon`` and stopped at close, in reverse, and proven gone;
a process of the world left running at close is refused (§daemons below).
A plant proves it landed.

PATH is the one exception, and it is dated. ``World(host_path=False)`` is
the hermetic PATH the contract asks for: the world's own bin and nothing
else, so a tool the case neither planted nor named is absent. That is the
default-to-be, and it is not the default today. LD-72 is the defect; the
strict shape turns 1,339 rows red across 95 modules whose fixtures run a
host tool the world would no longer carry, measured and listed in
``ops/conductor/handoff/HANDOFF-2026-09-16-hermetic-path-sweep.md`` at
``d8219578``. Until that sweep lands, ``host_path`` defaults to True and a
subject sees the world's bin followed by the host's PATH, as before. The
kit's own suite runs strict, so the behaviour stays measured while the
callers catch up.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import signal
import socket as socketlib
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

FIXED_DATE = "2026-01-02T03:04:05Z"
USER = "Test User"
EMAIL = "test@example.invalid"

# The host values a world never carries unless ``ambient`` names them:
# every GIT_* value, the XDG roots, HOME, TMPDIR and the escape hatches a
# hook or a tool reads. PATH is not one of them: it is never settable
# through ``ambient`` and is governed by ``host_path`` instead, whose
# strict value is the world's bin alone (LD-72).
_ALWAYS_OWN = ("HOME", "TMPDIR", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")


class PlantFailed(AssertionError):
    """The fixture was not what the case claimed to set up: INVALID, not a verdict."""


#: Words a caller writes when they mean a choice. A PATH could in
#: principle be a one-entry relative path spelled `false`, so the ambiguity
#: is refused rather than settled one way
#: (20260916T122524Z-review-codex-05a9f5#9).
_CHOICE_WORDS = frozenset({"", "0", "1", "true", "false", "yes", "no", "on", "off"})


def a_tool_name(name):
    """The name, when it names a tool a world may place on its own bin.

    A world places a tool at ``world.bin / name`` but resolves the real one
    against the caller's directory, so a name carrying a separator or ``..``
    put an executable outside the world, which then survived its close
    (20260916T122524Z-review-codex-05a9f5#3). A tool name is a file name."""
    text = str(name)
    separators = [sep for sep in (os.sep, os.altsep) if sep]
    if (not text or text in (".", "..") or text.startswith("-")
            or any(sep in text for sep in separators)):
        raise PlantFailed(
            f"INVALID: {text!r} is not a tool name; a world places its tools on "
            "its own bin, so the name is one file name with no separator, no "
            "'.' or '..', and no leading dash"
        )
    return text


def a_path_choice(value):
    """``host_path`` for a World: True or False and nothing else.

    A World uses it as a choice, never as a PATH, and ``bool('false')`` is
    True, so a caller who spelled a false value got the host's PATH and no
    word about it (20260916T122524Z-review-codex-05a9f5#9)."""
    if isinstance(value, bool):
        return value
    raise PlantFailed(
        f"INVALID: host_path is True or False; found {type(value).__name__} "
        f"{value!r}; a World uses it as a choice, and hermetic_env is the door "
        "that takes a PATH to follow"
    )


def a_path_or_choice(value):
    """``host_path`` for hermetic_env: True, False, or the PATH to follow."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in _CHOICE_WORDS:
            raise PlantFailed(
                f"INVALID: host_path {value!r} reads as a choice and as a PATH "
                "of one entry, and nothing here can tell which was meant; pass "
                "True, False, or a PATH this is not"
            )
        return value
    raise PlantFailed(
        f"INVALID: host_path is True, False, or the PATH to follow; found "
        f"{type(value).__name__} {value!r}"
    )


class World:
    def __init__(self, *, ambient=(), ambient_tools=(), prefix="world-", keep=False,
                 host_path=True):
        self.root = Path(tempfile.mkdtemp(prefix=prefix))
        self.home = self.root / "home"
        self.xdg_state = self.root / "xdg" / "state"
        self.xdg_config = self.root / "xdg" / "config"
        self.xdg_cache = self.root / "xdg" / "cache"
        self.tmp = self.root / "tmp"
        self.cwd = self.root / "cwd"
        self.bin = self.root / "bin"
        for path in (self.home, self.xdg_state, self.xdg_config, self.xdg_cache,
                     self.tmp, self.cwd, self.bin):
            path.mkdir(parents=True)
        self.ambient = tuple(ambient)
        for name in self.ambient:
            if name == "PATH":
                raise PlantFailed(
                    "INVALID: PATH is the world's own and cannot be ambient; "
                    "name the tools the case needs in ambient_tools, or pass "
                    "host_path to choose whether the host's PATH follows the "
                    "world's bin"
                )
            if name in _ALWAYS_OWN:
                raise PlantFailed(f"INVALID: {name} is the world's own and cannot be ambient")
        for name in ambient_tools:
            a_tool_name(name)
        self.keep = keep
        # False is the hermetic PATH of LD-72, the world's bin alone; True,
        # today's default, appends the host's after it. See the module
        # docstring for the date and the sweep that moves the default.
        self.host_path = a_path_choice(host_path)
        self._children: list[subprocess.Popen] = []
        self._daemons: list = []
        self._saved_environ: dict[str, str] | None = None
        self._tools = None
        self.closed = False
        # Read once, at construction: a world entered later has replaced
        # os.environ with its own, and the host's PATH is gone by then.
        self._host_path = os.environ.get("PATH", "/usr/bin:/bin")
        self.ambient_tools = tuple(ambient_tools)
        for name in self.ambient_tools:
            self.ambient_tool(name)

    # -- the environment -------------------------------------------------

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The hermetic environment: the world's own roots, a fixed git
        identity and clock, C locales, and only the ambient names from the
        host. ``extra`` overrides, as the case declares."""
        return hermetic_env(self.home, xdg_state=self.xdg_state, xdg_config=self.xdg_config,
                            xdg_cache=self.xdg_cache, tmp=self.tmp, bin=self.bin,
                            host_path=self._host_path if self.host_path else False,
                            ambient=self.ambient, extra=extra)

    def enter(self) -> World:
        """Patch ``os.environ`` to the world's environment for an
        in-process subject; ``exit`` restores it exactly."""
        if self._saved_environ is None:
            self._saved_environ = dict(os.environ)
        # The new environment is read from the host before the host is
        # cleared, or the named ambient values are lost (ruled rank 4,
        # record 20260915T025600Z-adjudicate-codex-68d3d9).
        fresh = self.env()
        os.environ.clear()
        os.environ.update(fresh)
        return self

    def exit(self) -> None:
        if self._saved_environ is not None:
            os.environ.clear()
            os.environ.update(self._saved_environ)
            self._saved_environ = None

    def __enter__(self) -> World:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- planting --------------------------------------------------------

    def path(self, target) -> Path:
        target = Path(target)
        return target if target.is_absolute() else self.root / target

    def plant_text(self, target, content: str, *, recognisable: str | None = None) -> Path:
        """Write ``content`` under the world and prove it landed intact."""
        return plant_text(self.path(target), content, recognisable=recognisable)

    def plant_bytes(self, target, data: bytes) -> Path:
        return plant_bytes(self.path(target), data)

    def stage(self, subject, name: str | None = None, *, siblings=()) -> Path:
        """Copy a subject's bytes into the world so a test never runs the
        tree's own file; ``siblings`` ride along beside it. An absent
        subject stages an absent stand-in (exit 0, no output) so the case
        fails on its own assertion, never on the copy."""
        subject = Path(subject)
        dest = self.root / "stage" / (name or subject.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if subject.is_file():
            data = subject.read_bytes()
            dest.write_bytes(data)
            if dest.read_bytes() != data:
                raise PlantFailed(f"INVALID: staged copy of {subject} did not land intact")
            for sibling in siblings:
                source = subject.with_name(sibling)
                if not source.is_file():
                    raise PlantFailed(f"INVALID: sibling {sibling} is absent beside {subject}")
                (dest.parent / sibling).write_bytes(source.read_bytes())
        else:
            dest.write_bytes(b"import sys\nsys.exit(0)\n")
        return dest

    def ambient_tool(self, name: str) -> Path:
        """A host tool this world carries, named: resolved on the host by
        absolute path and placed on the world's bin as a wrapper that logs
        every call. A name the case already planted is left where it is, so
        a plant wins over an ambient tool of the same name."""
        name = a_tool_name(name)
        placed = self.bin / name
        if placed.exists():
            return placed
        return self.tools.wrap(name, host_path=self._host_path)

    def require(self, tool: str) -> str:
        """An ambient tool the case needs; its absence is INVALID, never a verdict."""
        return require(tool)

    # -- children --------------------------------------------------------

    def own(self, child: subprocess.Popen) -> subprocess.Popen:
        """Register a child the world reaps at close."""
        self._children.append(child)
        return child

    def reap(self, child: subprocess.Popen) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            child.kill()
        with contextlib.suppress(Exception):
            child.communicate(timeout=10)
        if child.poll() is None:
            raise PlantFailed(f"INVALID: child {child.pid} was not reaped")

    # -- daemons ---------------------------------------------------------

    def daemon(self, name: str, start, stop=None, *, match=(), socket=None,
               timeout: float = 10.0):
        """Start a daemon in this world and record it, as malloc records a
        block for free. ``start`` is an argv that returns once the daemon
        runs detached; ``stop`` an argv, a callable or None; ``match`` the
        tokens of its command line, when its environment does not carry
        this world's HOME; ``socket`` the path it serves, if any. ``close``
        stops it: the stop, then SIGTERM, then SIGKILL, each within
        ``timeout``, and refuses class ``daemon`` when its own stop did not
        bring it down."""
        return start_daemon(self, name, start, stop, match=match, socket=socket, timeout=timeout)

    def adopt(self, name: str, stop=None, *, match=(), socket=None, timeout: float = 10.0):
        """Record a daemon the subject started, so the case starts nothing
        and the subject's own path is the one under test. It is the
        processes holding the unix socket bound at ``socket`` and, when
        ``match`` is given too, whose command line holds every token; or,
        with no socket, the processes ``match`` names. One of the two is
        required. ``close`` stops it as it stops a daemon ``daemon``
        started."""
        daemon = adopt_daemon(self, name, stop, match=match, socket=socket, timeout=timeout)
        self._daemons.append(daemon)
        return daemon

    # -- git -------------------------------------------------------------

    def git(self, *args: str, cwd=None, check: bool = True, timeout: float = 60):
        """Git in this world. A world that offers git must carry it, so this
        method declares git ambient for itself: the first call places the
        logged wrapper on the world's bin. A planted git wins over it."""
        from .command import run
        self.ambient_tool("git")
        argv = ["git", "-c", f"user.name={USER}", "-c", f"user.email={EMAIL}",
                "-c", "commit.gpgsign=false", *args]
        return run(argv, cwd=cwd or self.cwd, env=self.env(), timeout=timeout,
                   expect=0 if check else None)

    # -- watching --------------------------------------------------------

    def watch(self, *paths, ignore=(), tolerate_new=(), contents=True) -> Watch:
        """Snapshot the declared paths now, to compare at the case's end.
        ``contents=True`` reads every descendant's bytes, so a rewrite in
        place shows; ``contents=False`` reads presence, kind and the
        immediate names only, the shape a guard over a large live store
        declares (ops/conductor/test-store-guard.md)."""
        return Watch(paths, ignore=ignore, tolerate_new=tolerate_new, contents=contents)

    # -- the fake registry ------------------------------------------------

    @property
    def tools(self):
        if self._tools is None:
            from .fakes import Tools
            self._tools = Tools(self)
        return self._tools

    # -- close -----------------------------------------------------------

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        refusals = []
        try:
            for child in self._children:
                self.reap(child)
        finally:
            # Reverse order, as a stack of allocations is freed: a daemon
            # started on another's socket stops before the one it leans on.
            for daemon in reversed(self._daemons):
                refusals.append(stop_daemon(self, daemon))
            refusals.append(sweep_daemons(self))
            self.exit()
            if not self.keep:
                shutil.rmtree(self.root, ignore_errors=True)
        refusals = [line for line in refusals if line]
        if refusals:
            raise DaemonRefused(refusals)


# -- the environment and the plants, as functions ---------------------------
# A suite whose fixture predates the World keeps its own home and reaches
# these by name; the World's methods are the same functions bound to its
# own roots, so one implementation answers both.

def hermetic_env(home, *, xdg_state=None, xdg_config=None, xdg_cache=None, tmp=None, bin=None,
                 host_path=True, ambient=(), extra: dict[str, str] | None = None) -> dict[str, str]:
    """The hermetic environment for ``home``: the roots (defaulting to the
    XDG layout under ``home`` and ``home``/tmp), a PATH built from ``bin``
    and ``host_path``, a fixed git identity and clock, C locales, and only
    the ``ambient`` names from the host. ``extra`` overrides.

    ``host_path`` is True to read the host's PATH now and put it after
    ``bin``, a string to use one already read, and False for the hermetic
    PATH of LD-72: ``bin`` alone, so a tool the case neither planted nor
    named is absent."""
    home = Path(home)
    # TMPDIR names a directory that exists: the world's own, or a caller's
    # home/tmp when it has one; a home without one keeps the system default,
    # since a TMPDIR nobody created breaks every mktemp under it.
    if tmp is None and (home / "tmp").is_dir():
        tmp = home / "tmp"
    host_path = a_path_or_choice(host_path)
    if host_path is True:
        host_path = os.environ.get("PATH", "")
    env = {
        "PATH": os.pathsep.join(
            part for part in (str(bin) if bin is not None else "", host_path or "") if part),
        "HOME": str(home),
        "XDG_STATE_HOME": str(xdg_state or home / ".local" / "state"),
        "XDG_CONFIG_HOME": str(xdg_config or home / ".config"),
        "XDG_CACHE_HOME": str(xdg_cache or home / ".cache"),
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        # Calibration copies Git fixtures. Detached automatic packing can
        # remove loose objects midway through a copy on newer Git versions.
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "gc.auto",
        "GIT_CONFIG_VALUE_0": "0",
        "GIT_CONFIG_KEY_1": "maintenance.auto",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_NAME": USER,
        "GIT_AUTHOR_EMAIL": EMAIL,
        "GIT_COMMITTER_NAME": USER,
        "GIT_COMMITTER_EMAIL": EMAIL,
        "GIT_AUTHOR_DATE": FIXED_DATE,
        "GIT_COMMITTER_DATE": FIXED_DATE,
    }
    if tmp is not None:
        env["TMPDIR"] = str(tmp)
    for name in ambient:
        if name in os.environ:
            env[name] = os.environ[name]
    if extra:
        env.update(extra)
    return env


def plant_text(path, content: str, *, recognisable: str | None = None) -> Path:
    """Write ``content`` and prove it landed intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    landed = path.read_text(encoding="utf-8")
    if landed != content:
        raise PlantFailed(f"INVALID: plant did not reach disk for {path} "
                          f"(wanted {len(content)} chars, got {len(landed)})")
    if content and not landed:
        raise PlantFailed(f"INVALID: plant emptied {path}")
    if recognisable is not None and recognisable not in landed:
        raise PlantFailed(f"INVALID: plant left {path} unrecognisable as the fixture")
    return path


def plant_bytes(path, data: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    landed = path.read_bytes()
    if landed != data:
        raise PlantFailed(f"INVALID: plant did not reach disk for {path}")
    return path


def require(tool: str) -> str:
    """An ambient tool a case needs; its absence is INVALID, never a verdict."""
    found = shutil.which(tool)
    if found is None:
        raise PlantFailed(f"INVALID: {tool} is not on PATH")
    return found


# -- snapshots -----------------------------------------------------------

def snapshot(path: Path) -> tuple[str, tuple[str, ...]]:
    """One path: ``absent``; ``file`` with its content digest; ``dir``
    with its immediate names; ``other`` for anything else."""
    path = Path(path)
    if not path.exists():
        return ("absent", ())
    if path.is_dir():
        return ("dir", tuple(sorted(p.name for p in path.iterdir())))
    if path.is_file():
        return ("file", (hashlib.sha256(path.read_bytes()).hexdigest(),))
    return ("other", ())


def snapshot_tree(path: Path) -> dict:
    """``snapshot`` of ``path`` and, under a directory, of every descendant,
    keyed by path: a rewrite in place shows as a changed digest (ruled
    rank 1, record 20260915T025600Z-adjudicate-codex-68d3d9)."""
    path = Path(path)
    tree = {str(path): snapshot(path)}
    if path.is_dir():
        for child in sorted(path.iterdir()):
            tree.update(snapshot_tree(child))
    return tree


def _patterns(items):
    return tuple(re.compile(p) if isinstance(p, str) else p for p in items)


def compare(before: dict, after: dict, *, ignore=(), tolerate_new=()) -> tuple[dict, dict]:
    """Return (expected, actual) with the declared exclusions applied:
    a name matching ``ignore`` is dropped on both sides; a name matching
    ``tolerate_new`` may appear after without being before, but a
    pre-existing one that vanished still shows."""
    ignore = _patterns(ignore)
    tolerate_new = _patterns(tolerate_new)

    def ignored(name):
        return any(p.fullmatch(name) for p in ignore)

    expected: dict = {}
    for key, (kind, names) in before.items():
        if kind == "dir":
            names = tuple(n for n in names if not ignored(n))
        expected[key] = (kind, names)
    actual: dict = {}
    for key, (kind, names) in after.items():
        if kind == "dir" and key in before and before[key][0] == "dir":
            previous = set(before[key][1])
            names = tuple(
                n for n in names
                if not ignored(n)
                and (n in previous or not any(p.match(n) for p in tolerate_new))
            )
        actual[key] = (kind, names)
    return expected, actual


class Watch:
    """A before snapshot of declared paths and their contents, compared
    at the case's end. ``ignore`` and ``tolerate_new`` are name patterns
    and reach every level: a descendant under an ignored name is dropped
    on both sides, one under a tolerated new name is dropped from the
    after side when that name was not there before."""

    def __init__(self, paths, *, ignore=(), tolerate_new=(), contents=True):
        self.paths = [Path(p) for p in paths]
        self.ignore = tuple(ignore)
        self.tolerate_new = tuple(tolerate_new)
        self.contents = contents
        self.before = self._tree(before=None)

    def _tree(self, *, before) -> dict:
        if not self.contents:
            return {str(p): snapshot(p) for p in self.paths}
        ignore = _patterns(self.ignore)
        tolerate_new = _patterns(self.tolerate_new)
        tree: dict = {}
        for root in self.paths:
            for key, value in snapshot_tree(root).items():
                parts = Path(key).relative_to(root).parts
                if any(p.fullmatch(name) for name in parts for p in ignore):
                    continue
                if before is not None and key not in before and any(
                        p.match(name) for name in parts for p in tolerate_new):
                    continue
                tree[key] = value
        return tree

    def now(self) -> dict:
        return self._tree(before=self.before)

    def changed(self) -> tuple[dict, dict]:
        return compare(self.before, self.now(), ignore=self.ignore, tolerate_new=self.tolerate_new)

    def assert_unchanged(self, case=None, message: str = "watched path mutated") -> None:
        expected, actual = self.changed()
        if case is not None:
            case.assertEqual(actual, expected, message)
        elif actual != expected:
            raise AssertionError(f"{message}: expected {expected!r}, found {actual!r}")


def make_executable(path: Path) -> Path:
    path = Path(path)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not os.access(path, os.X_OK):
        raise PlantFailed(f"INVALID: {path} is not executable")
    return path


# -- daemons ---------------------------------------------------------------
# Daemons a world owns, paired the way malloc pairs with free.
#
# ``World.daemon(name, start, stop)`` starts a process that outlives the
# command that started it (a server that forks and detaches) and records
# it; ``World.close`` stops every recorded daemon in reverse order and
# proves each one gone. A daemon is identified in the process table, never
# by the files it leaves: a pid and its start time, so a reused pid is
# never taken for it, and a daemon's own children are counted as its.
# Measured on the tmux fixture at a93368b5: 1 run in 20 leaked 18 servers
# with no failure, because the teardown skipped when the socket file was
# missing and never read the stop's exit.
#
# The stop escalates within the daemon's timeout: the stop argv (or
# callable), then SIGTERM, then SIGKILL. A daemon its own stop did not
# bring down is refused even when the signals then ended it, because the
# next close without signals would leak it. After the recorded daemons, a
# sweep reads every process still carrying the world's HOME: a daemon
# started around the kit. It is given a grace, then killed, and refused.
# ``close`` always finishes its cleanup and raises ``DaemonRefused`` last;
# each of its lines is a refusal in the wire's shape (docs/wire.md),
# class ``daemon``. A refusal names a process by its program and the size
# of its argv, never the argv's bytes, so nothing a case planted in an
# argument or a stop's stderr reaches a log.
#
# A declared gap: a child that drops the world's HOME and outlives the
# daemon that started it is neither adopted (its parent is gone) nor swept
# (it carries no HOME). The kit's daemons inherit the world's environment
# by construction; a subject that scrubs HOME from its children is not
# covered (A-528 #3, record 20260923T030855Z-review-codex-8585d2).

PROC = Path("/proc")
POLL = 0.05
SWEEP_GRACE = 2.0
START_MARK = "TESTKIT_DAEMON_START"


class DaemonRefused(AssertionError):
    """A world closed with a daemon its stop did not bring down, or one it
    never recorded. ``lines`` holds one wire line per daemon, class
    ``daemon``; the message is those lines."""

    def __init__(self, lines):
        self.lines = tuple(lines)
        super().__init__("\n".join(self.lines))


# -- the process table ------------------------------------------------------

def _stat(pid: int):
    """(state, parent pid, start time) of ``pid``, or None once it is gone."""
    try:
        raw = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    # The command name sits in parentheses and may hold spaces or parentheses
    # itself: the fields after it start past the last ')'.
    rest = raw[raw.rindex(")") + 2:].split()
    return rest[0], int(rest[1]), rest[19]


def _live(state: str) -> bool:
    return state not in ("Z", "X")


def processes() -> dict[int, tuple[int, str]]:
    """Every live pid the kit can see, with its parent and start time."""
    if not (PROC / "self" / "stat").is_file():
        raise PlantFailed(
            f"daemon: expected a process table at {PROC}; found none; "
            "needed a host whose /proc lists its processes, or no daemon in this world"
        )
    table = {}
    for entry in PROC.iterdir():
        if entry.name.isdigit():
            stat = _stat(int(entry.name))
            if stat is not None and _live(stat[0]):
                table[int(entry.name)] = (stat[1], stat[2])
    return table


def _read(pid: int, name: str) -> bytes:
    try:
        return (PROC / str(pid) / name).read_bytes()
    except OSError:
        return b""


def command_line(pid: int) -> str:
    return _read(pid, "cmdline").replace(b"\0", b" ").decode("utf-8", "replace").strip()


def carrying(home: Path, pids) -> set[int]:
    """The pids whose environment names ``home`` as HOME."""
    marker = b"HOME=" + os.fsencode(str(home))
    return {pid for pid in pids if marker in _read(pid, "environ").split(b"\0")}


def bearing(entry: str, pids) -> set[int]:
    """The pids whose environment holds ``entry``, a ``NAME=value``, verbatim."""
    want = os.fsencode(entry)
    return {pid for pid in pids if want in _read(pid, "environ").split(b"\0")}


def matching(tokens, pids) -> set[int]:
    """The pids whose command line holds every one of ``tokens``."""
    tokens = tuple(str(t) for t in tokens)
    if not tokens:
        return set()
    return {pid for pid in pids if all(t in command_line(pid) for t in tokens)}


def descendants(roots, table) -> set[int]:
    """``roots`` and every live process below them."""
    found, frontier = set(roots), set(roots)
    while frontier:
        frontier = {pid for pid, (parent, _) in table.items() if parent in frontier} - found
        found |= frontier
    return found


def socket_holders(path, pids) -> set[int]:
    """The pids holding a unix socket bound at ``path``, read from the
    kernel's socket table and each pid's descriptors, never from the file."""
    names = {str(path), str(Path(path).resolve())}
    inodes = set()
    for line in _read_table("net/unix").splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) == 8 and fields[7] in names:
            inodes.add(f"socket:[{fields[6]}]")
    if not inodes:
        return set()
    held = set()
    for pid in pids:
        try:
            descriptors = list((PROC / str(pid) / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            with contextlib.suppress(OSError):
                if os.readlink(descriptor) in inodes:
                    held.add(pid)
                    break
    return held


def _read_table(name: str) -> str:
    try:
        return (PROC / name).read_text()
    except OSError:
        return ""


def socket_state(path) -> str:
    if path is None:
        return "none declared"
    path = Path(path)
    if not path.exists():
        return "absent"
    probe = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
    except OSError:
        return "present, refusing connections"
    finally:
        probe.close()
    return "accepting"


# -- one daemon -------------------------------------------------------------

@dataclass
class Daemon:
    name: str
    start: tuple
    stop: object
    socket: Path | None
    timeout: float
    pids: dict  # pid -> start time

    def alive(self) -> list[int]:
        out = []
        for pid, started in self.pids.items():
            stat = _stat(pid)
            if stat is not None and _live(stat[0]) and stat[2] == started:
                out.append(pid)
        return sorted(out)

    def adopt(self) -> None:
        """Count the children the daemon has started since it was recorded."""
        table = processes()
        for pid in descendants(self.alive(), table):
            self.pids.setdefault(pid, table[pid][1])

    def gone(self, budget: float) -> bool:
        deadline = time.monotonic() + budget
        while self.alive():
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL)
        return True


def start_daemon(world, name: str, argv, stop, *, match=(), socket=None, timeout: float = 10.0) -> Daemon:
    """Run ``argv`` in the world and record the daemon it leaves running,
    in the world's list for close. A start that fails is judged after what
    it left is recorded, so close stops a half-started daemon (A-535 #2)."""
    from .command import Timeout, run
    _check_daemon(name, stop)
    # The start's own mark: ``match`` reads only the processes that carry it,
    # so a process the host starts meanwhile with the same tokens is never
    # this daemon (A-535 #1). A daemon that clears its whole environment
    # carries neither the mark nor HOME and is not found: a declared gap.
    mark = f"{START_MARK}={os.urandom(16).hex()}"
    env = dict(world.env())
    env[START_MARK] = mark.split("=", 1)[1]
    before = processes()
    # A refusal names the start by program and size, never its argv, stdout
    # or stderr, which may carry a credential (A-535 #4).
    failed = None
    try:
        result = run(argv, cwd=world.cwd, env=env, timeout=timeout)
    except Timeout:
        failed = (Timeout, f"daemon {name} start {program(argv)} outlived {timeout:g}s; killed and reaped")
    else:
        if result.exit != 0:
            failed = (PlantFailed,
                      (f"INVALID: daemon {name} start {program(argv)} exited {result.exit} (wanted 0), "
                       f"stdout {len(result.stdout.encode())} bytes, stderr {len(result.stderr.encode())} bytes"))
    owned = {child.pid for child in world._children}
    taken = {pid for other in world._daemons for pid in other.pids}
    deadline = time.monotonic() + (0 if failed else timeout)
    while True:
        table = processes()
        new = {pid for pid, (_, started) in table.items()
               if before.get(pid, (None, None))[1] != started}
        pids = (carrying(world.home, new) | matching(match, bearing(mark, new))) - owned - taken - {os.getpid()}
        if pids or time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    daemon = None
    if pids:
        daemon = Daemon(name, tuple(str(a) for a in argv), stop,
                        Path(socket) if socket is not None else None, timeout,
                        {pid: table[pid][1] for pid in pids})
        world._daemons.append(daemon)
    if failed is not None:
        kind, text = failed
        raise kind(f"{text}; {len(pids)} process(es) it left recorded for close")
    if daemon is None:
        raise PlantFailed(
            f"INVALID: daemon {name} started ({program(argv)} exited 0) but no new "
            f"process carries HOME={world.home} or its {len(match)} match token(s) within {timeout:g}s"
        )
    return daemon


def adopt_daemon(world, name: str, stop, *, match=(), socket=None, timeout: float = 10.0) -> Daemon:
    """Record the daemon the subject left running, and nothing outside this
    world: the holders of ``socket``, a path inside the world's root,
    narrowed by ``match`` when both are given; or, with no socket, the
    processes carrying the world's HOME that ``match`` names."""
    _check_daemon(name, stop)
    match = tuple(str(t) for t in match)
    if socket is None and not match:
        raise PlantFailed(
            f"INVALID: adopting daemon {name} needs socket= or match=; found neither; "
            "needed the socket it serves or the tokens of its command line"
        )
    if socket is not None and not Path(socket).resolve().is_relative_to(world.root.resolve()):
        raise PlantFailed(
            f"INVALID: adopting daemon {name} needs a socket inside the world's root "
            f"{world.root}; found {socket}; needed the socket the subject serves in this world"
        )
    owned = {child.pid for child in world._children}
    taken = {pid for other in world._daemons for pid in other.pids}
    deadline = time.monotonic() + timeout
    while True:
        table = processes()
        pids = socket_holders(socket, table) if socket is not None else carrying(world.home, table)
        if match:
            pids &= matching(match, table)
        pids -= owned | taken | {os.getpid()}
        if pids or time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    if not pids:
        raise PlantFailed(
            f"INVALID: daemon {name} to adopt, but no live process holds the socket "
            f"{str(socket) if socket is not None else '(none declared)'} with its "
            f"{len(match)} match token(s) within {timeout:g}s"
        )
    return Daemon(name, (), stop, Path(socket) if socket is not None else None, timeout,
                  {pid: table[pid][1] for pid in pids})


def _check_daemon(name, stop) -> None:
    if not name or not isinstance(name, str):
        raise PlantFailed(f"INVALID: a daemon needs a name, found {name!r}")
    if not (stop is None or callable(stop) or (isinstance(stop, (list, tuple)) and stop)):
        raise PlantFailed(f"INVALID: the stop of daemon {name} is an argv, a callable or None, "
                          f"found a {type(stop).__name__}")


def _run_stop(world, daemon: Daemon) -> str:
    from .command import Timeout, run
    if daemon.stop is None:
        return "no stop declared"
    if callable(daemon.stop):
        said = []

        def call():
            try:
                daemon.stop()
            except BaseException as exc:  # noqa: BLE001 - the stop's failure is the reading, not a crash of close
                said.append(f"stop callable raised {type(exc).__name__}")
            else:
                said.append("stop callable returned")

        worker = threading.Thread(target=call, name=f"stop {daemon.name}", daemon=True)
        worker.start()
        worker.join(daemon.timeout)
        return said[0] if said else f"stop callable outlived {daemon.timeout:g}s"
    try:
        result = run(daemon.stop, cwd=world.cwd, env=world.env(), timeout=daemon.timeout)
    except Timeout:
        return f"stop {program(daemon.stop)} outlived {daemon.timeout:g}s"
    except PlantFailed:
        return f"stop {program(daemon.stop)} did not start"
    except OSError as exc:
        # A stop the host refuses to spawn is a reading too; close goes on to
        # escalate, sweep and remove the root (A-535 #3).
        return f"stop {program(daemon.stop)} did not start: {type(exc).__name__}"
    return f"stop exit {result.exit}, stderr {len(result.stderr.encode())} bytes"


def program(argv) -> str:
    """An argv named by its program and its size, never its bytes."""
    argv = [str(a) for a in argv]
    size = sum(len(a.encode()) for a in argv)
    return f"{(Path(argv[0]).name or '?') if argv else '?'} with {max(len(argv) - 1, 0)} argument(s), {size} bytes"


def _signal(pids, signum) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signum)


def stop_daemon(world, daemon: Daemon) -> str | None:
    """Stop one daemon, escalating; the refusal line, or None when its own
    stop brought it down."""
    daemon.adopt()
    said = _run_stop(world, daemon)
    if daemon.gone(daemon.timeout) and socket_state(daemon.socket) != "accepting":
        return None
    left = daemon.alive()
    sock = socket_state(daemon.socket)
    ended = None
    for signum in (signal.SIGTERM, signal.SIGKILL):
        daemon.adopt()
        _signal(daemon.alive(), signum)
        if daemon.gone(daemon.timeout):
            ended = f"{signal.Signals(signum).name} ended it"
            break
    if ended is None:
        ended = f"pids {daemon.alive()} still alive after SIGKILL"
    return (
        f"daemon: expected {daemon.name} down after its stop within {daemon.timeout:g}s, "
        f"its pids dead and its socket not accepting; "
        f"found {said}; pids {left} alive after the stop; socket {sock}; {ended}; "
        f"needed a stop that brings {daemon.name} down on its own"
    )


def sweep_daemons(world) -> str | None:
    """Every process still carrying the world's HOME, given a grace, then
    killed: a daemon started around the kit."""
    if not (PROC / "self" / "stat").is_file():
        return None
    mine = {os.getpid()}
    deadline = time.monotonic() + SWEEP_GRACE
    while True:
        table = processes()
        stray = carrying(world.home, table) - mine
        if not stray or time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    if not stray:
        return None
    seen = {pid: program(_read(pid, "cmdline").decode("utf-8", "replace").split("\0")[:-1] or ["?"])
            for pid in sorted(stray)}
    started = {pid: table[pid][1] for pid in stray}
    loose = Daemon("unrecorded", (), None, None, SWEEP_GRACE, started)
    _signal(loose.alive(), signal.SIGKILL)
    loose.gone(SWEEP_GRACE)
    return (
        f"daemon: expected every process of this world recorded by world.daemon or owned; "
        f"found {len(seen)} alive {SWEEP_GRACE:g}s after close stopped the recorded ones, "
        f"started around the kit: "
        + "; ".join(f"pid {pid} {name}" for pid, name in seen.items())
        + f"; pids {loose.alive()} alive after SIGKILL"
        + "; needed world.daemon(name, start, stop), or world.adopt(name, stop, socket=) "
        "for one the subject starts"
    )
