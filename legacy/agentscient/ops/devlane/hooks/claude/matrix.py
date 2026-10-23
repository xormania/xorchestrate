#!/usr/bin/env python3
"""Repository diagnostics. CI owns acceptance; ordinary writes need no grant.

The callable detectors retain their structured findings for tests and readers.
The hook entry point emits advice only. Dispatch and pipelines are optional.
"""

import ast
import json
import os
import re
import shlex
import string
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# A-218: the shared entry binds trusted source configuration and the existing
# instrument identity; legacy standalone probes retain their fixture context.
from contextvars import ContextVar

from command_shape import commands

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guidance

GUARD_CONTEXT = ContextVar("matrix_guard_context", default=None)

CODE_ROOTS = (
    "ops/", ".github/", ".claude/", "docs/", "packages/", "src/", "config/",
    "bin/", "tests/", "templates/", "composer.json", "composer.lock",
    "AGENTS.md", "CLAUDE.md", "CONTRIB.md", "README.md", "pyproject.toml",
    "ruff.toml", "phpunit.dist.xml", "phpunit.xml",
)
# Session records live under ops/ but are not code: the freeze is about code.
RECORD_PREFIXES = (
    "ops/conductor/ideas/", "ops/conductor/handoff/", "ops/conductor/AAR-",
    "ops/conductor/probes/",
)
WRITE_JOBS = ("author-tests", "implement")
ALLOWED_BY_HAND = {
    "plan": "planning is a session-owned read job",
    "adjudicate": "adjudication is a session-owned decision job",
    "sweep": "sweeping is a session-owned read job",
    "verify": "verification is a session-owned read job",
}
DISPATCHERS = ("dispatch.sh", "go.sh")
# graft-first (owner, 2026-09-11: "go - force it"): the conductor navigated
# code with greps all day and called Graft zero times. A content search over
# a code root refuses with the Graft query as the remedy. Counts (-c), file
# lists (-l/-L), a grep reading a pipe, and records paths are not searches.
# Narrowed (owner, 2026-09-13, token thrift #3; measured: 17 graft-first
# refusals in one session, each a whole-context turn): one named file is a
# read, and a conf, json, yaml, markdown or log target is data Graft never
# indexed; neither is navigation.
SEARCHERS = ("grep", "egrep", "fgrep", "rg", "ugrep", "evgrep.sh")
DATA_SUFFIXES = (".json", ".jsonl", ".yaml", ".yml", ".md", ".conf", ".txt", ".toml",
                 ".cue", ".log", ".lock", ".cfg", ".ini", ".example", ".csv")
SEARCH_COUNT_FLAGS = {"-c", "--count", "-l", "--files-with-matches", "-L", "--files-without-match"}
GRAFT_LEVER = "ops/devlane/harness/graft.py"
GRAFT_LOG = "graft-first.log"
LAUNCHER = "launch.py"
DRIVER = "ladder.sh"
# Launcher verbs that launch nothing: they read a record the dispatcher
# already minted. ops/README.md documents the wait verb; the lane conf the
# direct-launch row protects is applied at the mint, not at a read.
LAUNCHER_READ_VERBS = ("status", "wait", "brief", "help")
SCOPE_CAP_DEFAULT = 1024
COPY_WORDS = ("cp", "mv", "install", "rsync", "ln")
WHOLE_SUITE = re.compile(r"suites\.sh|unittest\s+discover(?![^\n]*\s-p\s)")
WALK_HEADING = re.compile(r"^#{1,6}\s+first-run walkthrough\b", re.IGNORECASE | re.MULTILINE)
WALK_LINE = re.compile(r"^\s*[-*]\s*assumption\b.*\b(measured|refusal)\b", re.IGNORECASE | re.MULTILINE)
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\S+)", re.MULTILINE)
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ENV_LONG_OPTIONS = {
    "ignore-environment": "none", "null": "none", "debug": "none",
    "unset": "required", "chdir": "required", "split-string": "required",
    "block-signal": "optional", "default-signal": "optional",
    "ignore-signal": "optional", "list-signal-handling": "none",
    "help": "terminal", "version": "terminal",
}
HEREDOC_OPEN = re.compile(
    r"(?:(?<![\w])(\d+))?(<<-?)[ \t]*(['\"]?)([A-Za-z_]\w*)\3"
)
HEREDOC_TOKEN = re.compile(r"^(?:(\d+))?<<-?(.*)$")
PRUNE_TARGETS = (".dev/records", ".git", "ops/conductor/ideas")
STATE_DIR = os.path.expanduser("~/.local/state")
STATE_STORE = "state.db"
ROLE_MACHINE = "dispatcher"
BOUNDARIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "boundaries.json")


def declared_boundaries():
    """The registry every row of this matrix is declared in (owner,
    2026-09-12: "a framework for verification"). One row per refusal
    class: boundary, actor, trigger, evidence duty, gate, tests, escape.
    A class not declared here cannot be refused with: deny() raises, and
    the hook reports hook-error, so an undeclared gate is itself a
    finding, never a silent rule."""
    with open(BOUNDARIES_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return {row["class"]: row for row in data["rows"]}


def deny(klass, expected, found, needed):
    rows = declared_boundaries()
    if klass not in rows:
        raise RuntimeError(f"undeclared gate: {klass} has no row in ops/devlane/hooks/boundaries.json")
    # Owner, 2026-09-12 (four answers): a hard row stays refused for an
    # agent; the owner authorizes the action, when wanted, by a harness
    # approval click. A row whose escape is `ask` answers ask, and the
    # harness puts that one action in front of the owner; nothing an
    # agent writes can click it.
    decision = "ask" if rows[klass].get("escape") == "ask" else "deny"
    line = f"{klass}: expected {expected}; found {found}; needed {needed}"
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": f"[matrix:{klass}] {line}",
    }}


# --- the tree ---------------------------------------------------------------

def repo_root(path):
    """The repository (or worktree) root holding ``path``, or None."""
    probe = os.path.abspath(path)
    if not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent


def relative(path, cwd):
    """(root, path relative to root) for a command path, or (None, None)."""
    absolute = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    root = repo_root(absolute)
    if root is None:
        return None, None
    return root, os.path.relpath(absolute, root)


def is_code(rel):
    if rel is None or rel.startswith(".."):
        return False
    if rel.startswith(RECORD_PREFIXES):
        return False
    return rel.startswith(CODE_ROOTS) or rel in CODE_ROOTS


def lane_conf(root):
    """The lane conf as a dict; guarded calls use the installed source."""
    guard = GUARD_CONTEXT.get()
    if guard is not None:
        return dict(guard["conf"])
    conf = {}
    path = os.path.join(root or "", "ops", "lane.conf")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                conf[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return conf


# --- statements -------------------------------------------------------------

def tokens_of(statement):
    """Tokens of one command, leading NAME=value assignments peeled."""
    try:
        toks = shlex.split(statement, posix=True)
    except ValueError:
        toks = statement.split()
    while toks and ENV_ASSIGN.match(toks[0]):
        toks = toks[1:]
    if toks and toks[0] == "export":
        return []
    return toks


def base(word):
    return word.rsplit("/", 1)[-1]


def is_python(word):
    return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", base(word)))


def unique_long_option(argument, vocabulary):
    """A GNU long option's unique full name and whether it carries ``=VALUE``."""
    name, equals, _value = argument[2:].partition("=")
    matches = [option for option in vocabulary if option.startswith(name)]
    if len(matches) != 1:
        return None
    return matches[0], bool(equals)


def env_operand(tokens, program):
    """The command operand after bounded GNU env options, or None."""
    if program >= len(tokens) or base(tokens[program]) != "env":
        return program
    program += 1
    while program < len(tokens):
        argument = tokens[program]
        if argument == "--":
            return program + 1
        if argument == "-" or ENV_ASSIGN.match(argument):
            program += 1
            continue
        if argument.startswith("--"):
            resolved = unique_long_option(argument, ENV_LONG_OPTIONS)
            if resolved is None:
                return None
            option, attached = resolved
            semantics = ENV_LONG_OPTIONS[option]
            if attached and semantics not in ("required", "optional"):
                return None
            if semantics == "terminal":
                return None
            if semantics == "required" and not attached:
                if program + 1 >= len(tokens):
                    return None
                program += 2
            else:
                program += 1
            continue
        if argument.startswith("-"):
            options = argument[1:]
            offset = 0
            while offset < len(options) and options[offset] in "i0v":
                offset += 1
            if offset == len(options):
                program += 1
                continue
            if options[offset] in "uCS" and offset + 1 == len(options):
                program += 2
                continue
            if options[offset] in "uCS":
                program += 1
                continue
            return None
        return program
    return None


def python_operand(tokens, program):
    """The script operand after Python options, or the literal -c source."""
    program += 1
    while program < len(tokens):
        argument = tokens[program]
        if argument == "--":
            return program + 1, None
        if argument == "-":
            return program, None
        if argument in ("-h", "-?", "-V", "--help", "--version", "--help-env",
                        "--help-xoptions", "--help-all"):
            return None, None
        if argument == "--check-hash-based-pycs":
            program += 2
            continue
        if argument.startswith("--check-hash-based-pycs="):
            program += 1
            continue
        if argument in ("--debug", "--inspect", "--isolated", "--no-site", "--no-user-site", "--quiet", "--verbose"):
            program += 1
            continue
        if argument.startswith("-"):
            options = argument[1:]
            for offset, option in enumerate(options):
                if option == "c":
                    source = options[offset + 1:]
                    if not source and program + 1 < len(tokens):
                        source = tokens[program + 1]
                    return None, source or None
                if option == "m" or option in "h?V":
                    return None, None
                if option in "WX":
                    program += 1 if options[offset + 1:] else 2
                    break
                if option not in "bBdEiIOPqRsSuvx":
                    return None, None
            else:
                program += 1
            continue
        return program, None
    return program, None


def literal_arg(call, position, keyword, default=None):
    node = call.args[position] if len(call.args) > position else None
    for item in call.keywords:
        if item.arg == keyword:
            node = item.value
            break
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else default


def python_sources(text):
    """Literal Python supplied to an executing interpreter, never quoted shell data."""
    sources = []
    for command in commands(text):
        tokens = tokens_of(command)
        program = env_operand(tokens, 0)
        if program is None or program >= len(tokens) or not is_python(tokens[program]):
            continue
        _program, source = python_operand(tokens, program)
        if source is not None:
            sources.append(source)

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = HEREDOC_OPEN.search(lines[index])
        if not match:
            index += 1
            continue
        fd = match.group(1)
        delimiter = match.group(4)
        end = index + 1
        while end < len(lines) and lines[end].strip() != delimiter:
            end += 1
        executes_python = False
        for command in commands(lines[index]):
            tokens = tokens_of(command)
            for offset, token in enumerate(tokens):
                redirect = HEREDOC_TOKEN.fullmatch(token)
                if redirect is None or redirect.group(1) != fd:
                    continue
                operand = redirect.group(2)
                if operand == delimiter:
                    del tokens[offset]
                elif not operand and offset + 1 < len(tokens) and tokens[offset + 1] == delimiter:
                    del tokens[offset:offset + 2]
                break
            if fd is not None and int(fd) != 0:
                continue
            program = env_operand(tokens, 0)
            if program is not None and program < len(tokens) and is_python(tokens[program]):
                operand, source = python_operand(tokens, program)
                executes_python = source is None and operand is not None and (
                    operand >= len(tokens) or tokens[operand] == "-"
                )
                if executes_python:
                    break
        if executes_python:
            sources.append("\n".join(lines[index + 1:end]))
        index = end + 1
    return sources


UNRESOLVED_WRITE = "<unresolved python write>"


def _path_expr(node, bindings, path_names, module_names):
    """The literal path an expression denotes, or None: a string, a
    Path('literal') call, a name bound earlier, or a `/` join of those."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Call):
        function = node.func
        is_path = ((isinstance(function, ast.Name) and function.id in path_names) or
                   (isinstance(function, ast.Attribute) and function.attr == "Path"
                    and isinstance(function.value, ast.Name)
                    and module_names.get(function.value.id) == "pathlib"))
        if is_path and node.args:
            parts = [_path_expr(arg, bindings, path_names, module_names) for arg in node.args]
            return None if any(p is None for p in parts) else os.path.join(*parts)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_expr(node.left, bindings, path_names, module_names)
        right = _path_expr(node.right, bindings, path_names, module_names)
        return None if left is None or right is None else os.path.join(left, right)
    return None


def python_written_paths(text):
    """Paths bound to writable stdlib open calls in executed Python: literal
    paths, Path('literal') calls, names bound to those (LD-26, 2026-09-12:
    `p = Path('ops/x.py'); p.write_text(...)` passed the row with no grant),
    and `/` joins of them. A write whose target cannot be resolved yields
    UNRESOLVED_WRITE, which the row refuses unless a grant is open: could
    not look is not a pass."""
    paths = []
    for source in python_sources(text):
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        module_names = {}
        open_names = {"open"}
        path_names = set()
        bindings = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name in ("builtins", "io", "pathlib"):
                        module_names[item.asname or item.name] = item.name
            elif isinstance(node, ast.ImportFrom) and node.module in ("builtins", "io", "pathlib"):
                for item in node.names:
                    local = item.asname or item.name
                    if item.name == "open" and node.module in ("builtins", "io"):
                        open_names.add(local)
                    elif item.name == "Path" and node.module == "pathlib":
                        path_names.add(local)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                resolved = _path_expr(node.value, bindings, path_names, module_names)
                if resolved is not None:
                    bindings[node.targets[0].id] = resolved
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            function = call.func
            if (isinstance(function, ast.Name) and function.id in open_names) or (
                    isinstance(function, ast.Attribute) and function.attr == "open"
                    and isinstance(function.value, ast.Name)
                    and module_names.get(function.value.id) in ("builtins", "io")):
                mode = literal_arg(call, 1, "mode", "r")
                if not (mode and set(mode) <= set("rwaxtb+") and any(c in mode for c in "wax+")):
                    continue
                target = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "file"), None)
                path = _path_expr(target, bindings, path_names, module_names) if target is not None else None
                paths.append(path if path is not None else UNRESOLVED_WRITE)
                continue
            if not isinstance(function, ast.Attribute) or function.attr not in ("open", "write_text", "write_bytes"):
                continue
            owner = function.value
            path = _path_expr(owner, bindings, path_names, module_names)
            mode = literal_arg(call, 0, "mode", "r") if function.attr == "open" else "w"
            if not (mode and set(mode) <= set("rwaxtb+") and any(c in mode for c in "wax+")):
                continue
            if path is not None:
                paths.append(path)
                continue
            # a method named open/write_text/write_bytes on something that is
            # not a path we can see: a file object's .write is not here (it is
            # `write`), so this is a Path-like owner the row cannot resolve
            if isinstance(owner, (ast.Name, ast.Call, ast.BinOp, ast.Attribute)):
                paths.append(UNRESOLVED_WRITE)
    return paths


def redirect_targets(tokens):
    """stdout redirection targets, whether `> f`, `>f` or `>> f`."""
    targets = []
    pending = False
    for token in tokens:
        if pending:
            targets.append(token)
            pending = False
            continue
        if token in (">", ">>", "1>", "1>>"):
            pending = True
        elif token.startswith((">", "1>")) and not token.startswith(">&"):
            targets.append(token.lstrip("1").lstrip(">"))
    return [t for t in targets if t and not t.startswith(("&", "/dev/"))]


def written_paths(tokens):
    """Paths a statement writes by its own shape (not by redirection)."""
    if not tokens:
        return []
    word = base(tokens[0])
    rest = tokens[1:]
    if word in ("sudo", "env", "nice", "time"):
        return written_paths(rest)
    if word in ("sed", "perl") and any(t.startswith(("-i", "--in-place", "-pi", "-ni")) for t in rest):
        files, script_taken, expect_script = [], False, False
        for token in rest:
            if expect_script:
                expect_script = False
                script_taken = True
                continue
            if token in ("-e", "--expression", "-f", "--file"):
                expect_script = True
                continue
            if token.startswith("-"):
                if token.startswith(("-e", "--expression=")) and len(token) > 2:
                    script_taken = True
                continue
            if not script_taken:
                script_taken = True
                continue
            files.append(token)
        return files
    if word in ("touch", "mkdir", "rm", "rmdir", "unlink"):
        return [t for t in rest if not t.startswith("-")]
    if word == "tee":
        return [t for t in rest if not t.startswith("-")]
    if word in COPY_WORDS:
        bare = [t for t in rest if not t.startswith("-")]
        return bare[-1:] if len(bare) >= 2 else []
    if word == "truncate":
        return [t for t in rest if not t.startswith("-") and not t.isdigit()]
    if word == "git":
        sub = [t for t in rest if not t.startswith("-")]
        verb = sub[0] if sub else ""
        if verb in ("checkout", "restore") and "--" in rest:
            return rest[rest.index("--") + 1:]
        if verb == "restore" and "--" not in rest:
            return [t for t in sub[1:] if not t.startswith("-")]
        if verb in ("apply", "am") or (verb == "cherry-pick" and ("-n" in rest or "--no-commit" in rest)):
            return ["."]
    if word == "patch":
        return ["."]
    return []


def prune_targets(tokens, cwd, conf):
    if not tokens or base(tokens[0]) != "rm":
        return []
    flags = [t for t in tokens[1:] if t.startswith("-")]
    if not any("r" in f.lower() for f in flags if not f.startswith("--")) and "--recursive" not in flags:
        return []
    hits = []
    wt_root = os.path.normpath(os.path.join(cwd, conf.get("WORKTREE_ROOT", "../wt")))
    state = os.path.join(STATE_DIR, conf.get("LANE_NAME"))
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        absolute = os.path.normpath(os.path.join(cwd, os.path.expanduser(token)))
        _, rel = relative(token, cwd)
        if absolute.startswith((wt_root, state)) or (rel is not None and any(
                rel == t or rel.startswith(t + "/") for t in PRUNE_TARGETS)):
            hits.append(token)
    return hits


# --- dispatch rows ----------------------------------------------------------

SHELLS = ("sh", "bash", "dash", "ksh", "zsh")
SOURCERS = (".", "source")


def shell_operand(tokens, program):
    """The script operand after a shell's options, or None.

    A dispatcher handed to a shell is still that dispatcher. The rows read
    the first word of the statement, so `bash ops/bin/dispatch.sh` and
    `source ops/bin/dispatch.sh` were classified as nothing and walked past
    every dispatch row, the `dispatcher` row included
    (20260916T122524Z-review-codex-05a9f5#1). `-c` carries a command string
    rather than a script and is not a script operand: check_text judges that
    string on its own."""
    program += 1
    while program < len(tokens):
        argument = tokens[program]
        if argument == "--":
            return program + 1 if program + 1 < len(tokens) else None
        if argument == "-":
            return None
        if argument.startswith("-"):
            if "c" in argument[1:]:
                return None
            program += 1
            continue
        return program
    return None


def dispatch_args(tokens):
    """(kind, flags) when the statement dispatches; kind is dispatcher|launcher|driver|None."""
    kind = None
    program = env_operand(tokens, 0)
    if program is None:
        return None, {}
    name = base(tokens[program]) if program < len(tokens) else ""
    if is_python(name):
        program, _source = python_operand(tokens, program)
        if program is None:
            return None, {}
        name = base(tokens[program]) if program < len(tokens) else ""
    elif name in SHELLS or name in SOURCERS:
        program = (shell_operand(tokens, program) if name in SHELLS
                   else (program + 1 if program + 1 < len(tokens) else None))
        if program is None or program >= len(tokens):
            return None, {}
        name = base(tokens[program])
    if name in DISPATCHERS:
        kind = "dispatcher"
    elif name == LAUNCHER:
        kind = "launcher"
    elif name == DRIVER:
        kind = "driver"
    if kind is None:
        return None, {}
    flags = {"input": [], "command": name}
    if kind == "launcher" and program + 1 < len(tokens) and not tokens[program + 1].startswith("-"):
        flags["verb"] = tokens[program + 1]
    elif kind == "launcher" and program + 1 < len(tokens) and tokens[program + 1] in ("--help", "-h"):
        flags["verb"] = "help"  # triage 2026-09-11: reading the usage is a read
    it = iter(tokens)
    for flag in it:
        if flag in ("--scope-file", "--worktree", "--job", "--model", "--effort", "--harness", "--stage", "--unit"):
            flags[flag[2:]] = next(it, "")
        elif flag == "--input":
            flags["input"].append(next(it, ""))
        elif flag.startswith("--input="):
            flags["input"].append(flag.split("=", 1)[1])
        elif "=" in flag and flag.startswith("--"):
            key, _, value = flag[2:].partition("=")
            flags[key] = value
    return kind, flags


def path_form_unittest(tokens):
    """The path argument of ``python -m unittest``, or None."""
    if not tokens or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", base(tokens[0])):
        return None
    try:
        module = tokens.index("-m")
    except ValueError:
        return None
    if module + 1 >= len(tokens) or tokens[module + 1] != "unittest":
        return None
    args = tokens[module + 2:]
    if "discover" in args:
        return None
    for argument in args:
        if not argument.startswith("-") and ("/" in argument or argument.endswith(".py")):
            return argument
    return None


def expand(script_text, cwd):
    """Expand shell variables from the script's assignments, then the environment."""
    mapping = dict(os.environ)
    for match in ASSIGN.finditer(script_text):
        mapping[match.group(1)] = string.Template(match.group(2).strip("'\"")).safe_substitute(mapping)
    return string.Template(script_text).safe_substitute(mapping)


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def harness_session():
    """The shared instrument is the one native/issued session resolver."""
    guard = GUARD_CONTEXT.get()
    if guard is not None:
        return guard["instrument"].session("")
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), "../../../conductor/tools/instrument.py")
    spec = importlib.util.spec_from_file_location("hook_instrument", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.session("")






def check_dispatch(kind, flags, cwd, text):
    if kind in {"launcher", "driver"}:
        return None
    worktree = flags.get("worktree", "")
    root = os.path.normpath(os.path.join(cwd, worktree)) if worktree else repo_root(cwd)
    conf = lane_conf(root)
    files = [f for f in [flags.get("scope-file", "")] + flags["input"] if f]
    for path in files:
        absolute = os.path.normpath(os.path.join(cwd, path))
        if "$" in path:
            return deny("input-absent", "a literal input path", f"unresolved {path}", "write the path out in the run script")
        if not os.path.isfile(absolute) or os.path.getsize(absolute) == 0:
            return deny("input-absent", "a readable non-empty input", f"{path} missing or empty", "write it before dispatch")
        content = read_text(absolute)
        unresolved = re.search(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[^}]+\})", content or "")
        if unresolved:
            return deny("input-absent", "inputs with literal resolved paths",
                        f"unresolved {unresolved.group(0)} in {path}", "write the resolved value into the input")
    scope_file = flags.get("scope-file", "")
    cap = int(conf.get("SCOPE_CAP", SCOPE_CAP_DEFAULT))
    if scope_file:
        size = os.path.getsize(os.path.normpath(os.path.join(cwd, scope_file)))
        if size > cap:
            return deny("scope-cap", f"<= {cap} bytes", f"{size} bytes in {scope_file}",
                        "cut the scope before dispatch, not after the mint")
    job = flags.get("job", "")
    scope_text = read_text(os.path.normpath(os.path.join(cwd, scope_file))) or "" if scope_file else ""
    if job == "implement":
        hit = WHOLE_SUITE.search(scope_text)
        if hit:
            return deny("bucket-command", "the bucket's tests as the named command",
                        f'"{hit.group(0)}" in {scope_file}',
                        "name the bucket's test files; the whole suite is not a bucket (ranking K2)")
        for statement in commands(scope_text):
            argument = path_form_unittest(tokens_of(statement))
            if argument:
                return deny("bucket-command", "the bucket's tests in unittest discover form",
                            f"path-form unittest argument {argument} in {scope_file}",
                            "name the tests with discover -s and -p")
    if job in WRITE_JOBS:
        inputs_text = "\n".join(read_text(os.path.normpath(os.path.join(cwd, p))) or "" for p in flags["input"])
        if not (WALK_HEADING.search(inputs_text) and WALK_LINE.search(inputs_text)):
            return deny("walkthrough",
                        'a "First-run walkthrough" section whose assumption lines are each measured or a refusal',
                        f"none in {' '.join(flags['input']) or 'no --input'}",
                        "walk the first run against the isolated home, no network, the read-only .git, "
                        "the job prompt and the runner workspace before dispatch (planning rule)")
    if worktree:
        trunk = conf.get("TRUNK_BRANCH")
        probe = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", f"origin/{trunk}", "HEAD"],  # noqa: S603, S607 - PATH git; root is a path this hook resolved
                               capture_output=True, text=True, check=False)
        if probe.returncode == 1:
            # Owner, 2026-09-12: "why are the branches always out of date".
            # Behind is the dispatcher's job: it merges origin/<trunk> in
            # before the launch. Only a merge that would conflict refuses.
            trial = subprocess.run(["git", "-C", root, "merge-tree", "--write-tree", "--name-only",  # noqa: S603, S607 - PATH git; a dry merge, no ref moves
                                    "HEAD", f"origin/{trunk}"],
                                   capture_output=True, text=True, check=False)
            if trial.returncode == 1:
                files = [line for line in trial.stdout.splitlines()[1:] if line.strip()]
                return deny("stale-base", f"origin/{trunk} to merge cleanly into {worktree}",
                            f"conflicts in {', '.join(files) or 'unnamed files'}",
                            f"resolve them in the worktree (git merge origin/{trunk}), commit, then dispatch")
            if trial.returncode > 1:
                return deny("stale-base", f"a dry merge of origin/{trunk} from {worktree}",
                            (trial.stderr.strip().splitlines() or ["no answer"])[-1],
                            "a worktree the hook can read")
        if probe.returncode > 1:
            return deny("stale-base", f"a git answer from {worktree}", probe.stderr.strip().splitlines()[-1:] or "no answer",
                        "a worktree the hook can read")
        harness_dir = os.path.join(root, "ops", "devlane", "harness")
        if os.path.isdir(harness_dir):
            try:
                vet = subprocess.run(["cue", "vet", "./ops/devlane/harness/"], cwd=root,  # noqa: S607 - PATH cue; a fixed argv
                                     capture_output=True, text=True, timeout=60, check=False)
            except FileNotFoundError:
                return deny("contract-vet", "cue on PATH to vet the harness contracts", "no cue",
                            "install cue; could not look is not a pass")
            except subprocess.TimeoutExpired:
                return deny("contract-vet", "cue vet within 60 s", "timeout", "a vettable contract package")
            if vet.returncode != 0:
                first = (vet.stderr.strip().splitlines() or ["no output"])[0]
                return deny("contract-vet", "ops/devlane/harness to vet clean as a package", first,
                            "inspect the contract errors")
        pin = re.search(r"LAUNCHER_PIN=(\S+)", text)
        if not pin or not pin.group(1).strip("'\"").startswith(root):
            return deny("dispatch-env", f"LAUNCHER_PIN={root}/ops/devlane/dispatch/launch.py (the branch's own launcher)",
                        pin.group(1) if pin else "no LAUNCHER_PIN", "export it in the run script")
        for name in ("DISPATCH_TIMEOUT", "WF_AGENT"):
            if f"{name}=" not in text:
                return deny("dispatch-env", f"{name} set for the launch", f"no {name}", "export it in the run script")
    return None


# --- the decision -----------------------------------------------------------

def graft_log(lane, kind, detail):
    """Append one line to the lane's graft-first log; never raise."""
    if not lane:
        return
    try:
        path = os.path.join(STATE_DIR, lane, GRAFT_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{_now_iso()}\t{kind}\t{detail}\n")
    except OSError:
        pass


def _now_iso():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def graft_context_path(root, lane):
    """The prepared Graft context for ``root`` under the lane's state, else None."""
    import hashlib
    if not root or not lane:
        return None
    digest = hashlib.sha256(root.encode()).hexdigest()
    path = os.path.join(STATE_DIR, lane, "graft", f"{digest}.context.json")
    return path if os.path.isfile(path) else None


def search_targets(toks):
    """(pattern, code-ish targets) of a grep-family statement, or (None, [])."""
    if not toks:
        return None, []
    word = base(toks[0])
    args = toks[1:]
    if word == "git" and len(toks) > 1 and toks[1] == "grep":
        args = toks[2:]
    elif word not in SEARCHERS:
        return None, []
    if any(a in SEARCH_COUNT_FLAGS for a in _split_short_flags(args)):
        return None, []
    positional = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--":
            continue
        if a.startswith("-"):
            if a in ("-e", "-f", "-m", "-A", "-B", "-C", "--include", "--exclude", "--exclude-dir"):
                skip = True
            continue
        positional.append(a)
    if len(positional) < 2:
        return None, []  # a pipe filter or a pattern with no path is not code navigation
    return positional[0], positional[1:]


def _split_short_flags(args):
    """Every flag as it reads: a combined short flag (-rl, -rnc) yields each
    letter as its own flag, so -l and -c are seen inside one (LD-33, owner
    2026-09-13); long flags and positionals pass through unchanged."""
    out = []
    for a in args:
        if len(a) > 2 and a[0] == "-" and a[1] != "-":
            out.extend("-" + ch for ch in a[1:])
        else:
            out.append(a)
    return out


def graft_first(toks, cwd, root, lane):
    pattern, targets = search_targets(toks)
    if pattern is None:
        return None
    if len(targets) == 1 and not targets[0].endswith("/") and "." in base(targets[0]):
        _, single = relative(targets[0], cwd)
        # A dotted name that is a directory (.github, .claude) is a walk, not
        # one file (Grok, dispatch 20260913T105222Z-review-grok-00b782, finding 1).
        walk = (single is not None and f"{single}/" in CODE_ROOTS) or os.path.isdir(os.path.join(cwd, targets[0]))
        if not walk:
            return None  # one named file is a read, not navigation
    hits = []
    for target in targets:
        _, rel = relative(target, cwd)
        if rel is not None and rel.lower().endswith(DATA_SUFFIXES):
            continue  # data Graft never indexed
        # a directory target normalises without its slash ("docs/" -> "docs")
        if is_code(rel) or (rel is not None and is_code(rel + "/")):
            hits.append(target)
    if not hits:
        return None
    context = graft_context_path(root, lane)
    query = (f"python3 {GRAFT_LEVER} query --context {context} graft_find_code "
             f"'{{\"query\": \"{pattern}\", \"limit\": 20}}'") if context else (
             f"python3 {GRAFT_LEVER} prepare --root {root}  (prints the context path), then "
             f"python3 {GRAFT_LEVER} query --context <that path> graft_find_code "
             f"'{{\"query\": \"{pattern}\", \"limit\": 20}}'")
    graft_log(lane, "deny", f"{base(toks[0])} {pattern} {' '.join(hits)}")
    return deny("graft-first", "code navigated through Graft (graft_find_code, graft_repo_map, graft_file_api)",
                f"{base(toks[0])} searching {' '.join(hits)} for {pattern!r}",
                f"{query}; a count (-c) or file list (-l) stays allowed")


DELIVERABLE_LOCK = "deliverable.json"


def deliverable_lock(lane):
    """The lane's deliverable lock (branches, why, by, at) or None."""
    if not lane:
        return None
    path = os.path.join(STATE_DIR, lane, DELIVERABLE_LOCK)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    branches = data.get("branches") if isinstance(data, dict) else None
    if not isinstance(branches, list) or not branches:
        return None
    return data


def branch_of(directory):
    try:
        proc = subprocess.run(["git", "-C", directory, "symbolic-ref", "--short", "HEAD"],  # noqa: S603,S607 - fixed argv, a read
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def deliverable_targets(toks, cwd):
    """(what, branch) pairs a statement aims new work at."""
    if not toks:
        return []
    name = base(toks[0])
    found = []
    if name == "git" and len(toks) > 2:
        args = [t for t in toks[1:] if not t.startswith("-C")]
        if args[0] == "worktree" and args[1] == "add":
            rest = args[2:]
            if "-b" in rest or "-B" in rest:
                flag = "-b" if "-b" in rest else "-B"
                i = rest.index(flag)
                if i + 1 < len(rest):
                    found.append(("git worktree add -b", rest[i + 1]))
            else:
                positional = [t for t in rest if not t.startswith("-")]
                if len(positional) >= 2:
                    found.append(("git worktree add", positional[1]))
                elif positional:
                    found.append(("git worktree add", os.path.basename(positional[0].rstrip("/"))))
        elif args[0] in ("checkout", "switch") and any(f in args for f in ("-b", "-B", "-c", "-C")):
            flag = next(f for f in ("-b", "-B", "-c", "-C") if f in args)
            i = args.index(flag)
            if i + 1 < len(args):
                found.append((f"git {args[0]} {flag}", args[i + 1]))
    elif name == "gh" and len(toks) > 2 and toks[1] == "pr" and toks[2] == "create":
        if "--head" in toks:
            i = toks.index("--head")
            branch = toks[i + 1] if i + 1 < len(toks) else None
        else:
            branch = branch_of(cwd)
        found.append(("gh pr create for", branch or "an unreadable branch"))
    elif name in DISPATCHERS:
        if "--branch" in toks:
            i = toks.index("--branch")
            found.append((f"{name} --branch", toks[i + 1] if i + 1 < len(toks) else ""))
        elif "--worktree" in toks:
            i = toks.index("--worktree")
            directory = toks[i + 1] if i + 1 < len(toks) else cwd
            found.append((f"{name} --worktree", branch_of(os.path.join(cwd, os.path.expanduser(directory))) or directory))
    elif name == DRIVER and len(toks) > 2 and toks[1] != "resume":
        positional = [t for t in toks[1:] if not t.startswith("-")]
        if len(positional) >= 2:
            found.append(("ladder start for", positional[1]))
    return found


def deliverable(toks, cwd, lane):
    """Owner, 2026-09-11: "i want an immediate FIX to stop that". While the
    lock names the deliverable branches, new work aimed anywhere else
    refuses. Findings found on the way are evidence for the ledger, not
    work; the lock is lifted by its lever, never by a grant."""
    lock = deliverable_lock(lane)
    if lock is None:
        return None
    branches = [str(b) for b in lock["branches"]]
    for what, branch in deliverable_targets(toks, cwd):
        if branch not in branches:
            return deny("deliverable",
                        f"work on the deliverable branches {', '.join(branches)} "
                        f"(locked {lock.get('at', '?')} by {lock.get('by', '?')}: {lock.get('why', '')})",
                        f"{what} {branch}",
                        "finish the deliverable; record what you found in the ledger; "
                        "python3 ops/conductor/tools/deliverable.py add <branch> names a new "
                        "session's branch beside these, and remove lifts it when its work lands")
    return None


PROOF_HEADING = re.compile(r"^##\s+Proof\b.*$", re.MULTILINE)
# A row id is P-<family>-<leaf>. A bare family in prose ("all seven P-278 rows
# PASS") names no row; read as one, it refused #271, #278 and #274 as absent.
# The id runs to its last word character or hyphen, so "P-FIX-1\u00e9" or "P-FIX-1-"
# is that token, named absent, never the row P-FIX-1 (A-539 #1).
PROOF_ID = re.compile(r"\bP-[^\W_]+-[\w-]*")
# A token read by PROOF_ID is a row only when its leaf ends in a word
# character. The corpus readers take any string id, so a row named "P-FIX-1-"
# made that token present (A-544 #2); such a token is named absent whatever
# the corpus carries.
PROOF_ROW = re.compile(r"P-[^\W_]+(?:-\w+)+")
PROBES_DIR = "ops/conductor/handoff/probes/"
CI_WORKFLOW = "ci"
AUDIT_JOB = "security-audit"
AUDIT_MODEL_KEY = "MODEL_CODEX_SECURITY_AUDIT"
AUDIT_HARNESS = "codex"
DISPATCH_RECORDS = ".dev/records/dispatches/"
FINDINGS_EXPORT = "ops/conductor/findings/findings.jsonl"
# The disposition vocabulary is findings.py's, read at the head: one
# definition, never a copy here that drifts from the ledger's.
DISPOSITIONS_SOURCE = "ops/conductor/tools/findings.py"
DISPOSITIONS_LINE = re.compile(r"^DISPOSITIONS = (\([^)]*\))", re.MULTILINE)
DECLINE = "declined: not practical — "
JUDGEMENT_MARKUP = " \t*_`\"'“”:;,.—\u2013-()"
# A dispatch record id as launch.py mints it, the shape ladder.py pins.
RECORD_ID = re.compile(r"\d{8}T\d{6}Z-[a-z-]+-[0-9a-f]{6}")


def proof_section(body):
    """The text under the PR body's ## Proof heading, or None when there is
    no such heading."""
    match = PROOF_HEADING.search(body or "")
    if match is None:
        return None
    section = body[match.end():]
    nxt = re.search(r"^##\s", section, re.MULTILINE)
    if nxt:
        section = section[:nxt.start()]
    return section


def proof_ids(body):
    """The corpus row ids named under the PR body's ## Proof heading."""
    section = proof_section(body)
    if section is None:
        return None
    ids = []
    for found in PROOF_ID.findall(section):
        if found not in ids:
            ids.append(found)
    return ids


def corpus_ids_at(root, head):
    """Every row id in the probes corpus at a commit; None when it cannot be read."""
    listing = subprocess.run(["git", "-C", root, "ls-tree", "--name-only", head, "--", PROBES_DIR],  # noqa: S603,S607 - fixed argv, a read
                             capture_output=True, text=True, timeout=30, check=False)
    if listing.returncode != 0:
        return None
    ids = set()
    for path in listing.stdout.split():
        if not path.endswith(".jsonl"):
            continue
        shown = subprocess.run(["git", "-C", root, "show", f"{head}:{path}"],  # noqa: S603,S607 - fixed argv, a read
                               capture_output=True, text=True, timeout=30, check=False)
        if shown.returncode != 0:
            return None
        for line in shown.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                ids.add(row["id"])
    return ids


def pr_number(toks):
    for tok in toks[3:]:
        if tok.isdigit():
            return tok
        if tok.startswith("https://") and tok.rstrip("/").rsplit("/", 1)[-1].isdigit():
            return tok.rstrip("/").rsplit("/", 1)[-1]
    return None


def gh_repo(toks):
    """The repository a gh command selects with -R/--repo, or None."""
    for i, tok in enumerate(toks):
        if tok in ("-R", "--repo") and i + 1 < len(toks):
            return toks[i + 1]
        if tok.startswith("--repo="):
            return tok.split("=", 1)[1]
        if tok.startswith("-R") and len(tok) > 2:
            return tok[2:]
    return None


def field(record, path):
    """The value at a dotted path of a record, or None."""
    node = record
    for key in path.split("."):
        node = node.get(key) if isinstance(node, dict) else None
    return node


def agreed(record, paths, *, sha=False):
    """(value, "") when every named field is present and they carry one value
    between them, else (None, what was found). Two fields that disagree are
    two answers, and an audit is never read as met on two answers: the
    refusal names both. One present member is not agreement either: a partial
    record missing `model.ran` or `result.head` was read as its pair agreeing
    (20260916T122524Z-review-codex-05a9f5#7), so the absent ones are named.
    Measured 2026-09-16 over the 35 closed security-audit records under
    .dev/records/dispatches: 35 of 35 carry both members of both pairs."""
    seen = {path: field(record, path) for path in paths}
    absent = sorted(path for path, value in seen.items()
                    if not (isinstance(value, str) and value))
    if absent:
        return None, "absent: " + ", ".join(absent)
    if len(set(seen.values())) > 1:
        return None, ", ".join(f"{path}={value[:8] if sha else value}"
                               for path, value in sorted(seen.items()))
    return next(iter(seen.values())), ""


def json_at(root, head, path):
    """A JSON file as a commit holds it, or None when it is not there."""
    shown = subprocess.run(["git", "-C", root, "show", f"{head}:{path}"],  # noqa: S603,S607 - fixed argv, a read
                           capture_output=True, text=True, timeout=30, check=False)
    if shown.returncode != 0:
        return None
    try:
        return json.loads(shown.stdout)
    except ValueError:
        return None


def dispositions_at(root, head):
    """{key: disposition} from the findings export as the head holds it.
    The export at the head, never the host's store: a disposition nobody
    can read at the head is not evidence the head carries, and an absent
    export answers `undisposed`, which refuses rather than passes."""
    shown = subprocess.run(["git", "-C", root, "show", f"{head}:{FINDINGS_EXPORT}"],  # noqa: S603,S607 - fixed argv, a read
                           capture_output=True, text=True, timeout=30, check=False)
    if shown.returncode != 0:
        return {}
    out = {}
    for line in shown.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("key"), str):
            out[row["key"]] = row.get("disposition")
    return out


def disposition_tokens_at(root, head):
    """findings.py's DISPOSITIONS at the head, less `open`; None when the
    file or the tuple cannot be read there."""
    shown = subprocess.run(["git", "-C", root, "show", f"{head}:{DISPOSITIONS_SOURCE}"],  # noqa: S603,S607 - fixed argv, a read
                           capture_output=True, text=True, timeout=30, check=False)
    if shown.returncode != 0:
        return None
    match = DISPOSITIONS_LINE.search(shown.stdout)
    if match is None:
        return None
    try:
        value = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, tuple) or not all(isinstance(v, str) and v for v in value):
        return None
    return tuple(v for v in value if v != "open")


def finding_dispositions(root, head, section, rid, findings):
    """The finding-disposition row. Every finding of the audit that holds is
    named in `## Proof`, by `<record>#<n>` or the record's suffix `#<n>`,
    with a disposition token equal to the export's at the head and one
    line of judgement after it. A finding whose record carries `practical`
    and is ruled is a decline: its judgement begins DECLINE. A finding
    without the field, as every record before this row, may be ruled on
    any ground."""
    expected = (f"every finding of {rid} named in `## Proof` with the export's "
                "disposition token and one line of judgement")
    needed = ("name each finding on its own line in `## Proof`: `<record>#<n> <token>: "
              "<judgement>`, the token as findings.py disposition set it")
    tokens = disposition_tokens_at(root, head)
    if not tokens:
        return deny("finding-disposition", expected,
                    f"{DISPOSITIONS_SOURCE} DISPOSITIONS unreadable at {head[:8]}",
                    "could not look is not a pass; restore the tuple and retry")
    held = dispositions_at(root, head)
    token_word = re.compile(r"(?<![\w-])(" + "|".join(
        re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + r")(?![\w-])")
    suffix = rid.rsplit("-", 1)[1]
    lines = section.splitlines()
    for n, finding in enumerate(findings, 1):
        key = f"{rid}#{n}"
        name = re.compile(r"(?<![\w-])(?:" + re.escape(rid) + "|" + re.escape(suffix)
                          + r")#" + str(n) + r"(?!\d)")
        named = next(((line, m) for line in lines for m in (name.search(line),) if m), None)
        if named is None:
            return deny("finding-disposition", expected, f"{key} not named in `## Proof`", needed)
        line, m = named
        rest = line[m.end():]
        token = token_word.search(rest)
        if token is None:
            return deny("finding-disposition", expected,
                        f"{key} named with no disposition token (one of {', '.join(tokens)})",
                        needed)
        if token.group(1) != held.get(key):
            return deny("finding-disposition", expected,
                        (f"{key} reads {token.group(1)} in `## Proof` and "
                         f"{held.get(key)} in the export at {head[:8]}"),
                        "make the body say what findings.py disposition set, or dispose it again and export")
        judgement = rest[token.end():].strip(JUDGEMENT_MARKUP)
        if not re.search(r"\w", judgement):
            return deny("finding-disposition", expected, f"{key} {token.group(1)} with no judgement", needed)
        practical = isinstance(finding, dict) and finding.get("practical") in ("yes", "no")
        if practical and token.group(1) == "ruled" and not judgement.startswith(DECLINE):
            return deny("finding-disposition", expected,
                        (f"{key} carries `practical` and is ruled, with a judgement not "
                         f"beginning \"{DECLINE}\""),
                        f"begin the judgement \"{DECLINE}\" and say why, or dispose it otherwise")
    return None


def is_a_fix_for(message, rid, findings, paths):
    """A fix commit in CONTRIB.md's shape (### Fix commit) that cites a
    finding of this audit and touches a path that finding named: a
    ``Finding:`` line carrying the ledger key ``<record id>#<n>``, the
    ``Verified:`` line the shape requires, and at least one of ``paths``
    among the paths the cited finding's ``where`` names. Owner, 2026-09-16:
    "you can't keep re-auditing after fixes - draw a line".

    The line is drawn at the place the finding named. The two text lines
    alone exempted every other path riding in the same commit, so unaudited
    code could arrive beside a nominal fix
    (20260916T122524Z-review-codex-05a9f5#8). One touched path is enough and
    not every one: an honest fix commit carries its test file and its record
    beside the code, and no finding names those."""
    lines = [line.strip() for line in (message or "").splitlines()]
    if not any(line.startswith("Verified:") for line in lines):
        return False
    touched = set(paths)
    for n, finding in enumerate(findings, 1):
        key = f"{rid}#{n}"
        if not any(line.startswith("Finding:") and key in line for line in lines):
            continue
        where = finding.get("where") if isinstance(finding, dict) else None
        if touched & where_paths(where):
            return True
    return False


COMMIT_ID = re.compile(r"\A[0-9a-f]{7,40}\Z")


def a_commit_id(value):
    """True when this is a commit id and nothing else. A record's sha reaches
    `git log` as a range operand, so a value beginning with a dash is read as
    an option instead: `--output=<path>` made git write that file, print
    nothing, and the empty range read as an audit nothing had voided
    (20260916T122524Z-review-codex-05a9f5#4)."""
    return isinstance(value, str) and bool(COMMIT_ID.match(value))


def where_paths(where):
    """The repository paths a finding's `where` names. A `where` reads
    `<path>:<lines>` joined by prose, so a token is a path when it carries a
    separator and a suffix on its last segment; the lines after it are not
    part of it."""
    named = set()
    for token in re.split(r"[\s,;()]+", where if isinstance(where, str) else ""):
        token = token.strip().rstrip(".,;:")
        if ":" in token:
            token = token.split(":", 1)[0]
        if "/" in token and "." in token.rsplit("/", 1)[-1]:
            named.add(token)
    return named


def ref_known(root, ref):
    """True when this clone can resolve ``ref`` to a commit. A ref it does
    not have is absent, never an empty exclusion read as one: the walk below
    judges every commit in the range instead, which is the stricter reading."""
    probe = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],  # noqa: S603,S607 - fixed argv, a read
                           capture_output=True, text=True, timeout=30, check=False)
    return probe.returncode == 0 and bool(probe.stdout.strip())


def branch_commits(root, sha, head, trunk):
    """The branch's own commits after ``sha``, oldest first, or None when git
    could not read the range.

    A forward merge of the trunk never voids the audit: the dispatcher merges
    the trunk into the branch before every launch, so a rule that judged
    every commit in the range voided every branch that had dispatched
    anything (2026-09-16, measured on #242; the ruling and who made it are
    in CONTRIB.md's audit row). ``--not origin/<trunk>`` drops what the
    trunk already carries, and nothing else is dropped. ``--first-parent``
    with ``--no-merges`` dropped every merge rather than the trunk's, so a
    branch could merge a topic carrying unaudited code and keep its audit
    (#5 of the same audit); the merge commits are judged here too, by their
    combined diff.

    None when the range cannot be read, the audited sha included: it reaches
    git as a range operand, so a value that is not a commit id is refused
    before git sees it (#4 of the same audit)."""
    if not (a_commit_id(sha) and a_commit_id(head)):
        return None
    argv = ["git", "-C", root, "log", "--reverse", "--format=%H", f"{sha}..{head}"]
    if trunk and ref_known(root, f"origin/{trunk}"):
        argv += ["--not", f"origin/{trunk}"]
    listing = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 - an argv this function built, a read
    if listing.returncode != 0:
        return None
    return listing.stdout.split()


def edits(root, commit, path):
    """True when this commit changed or removed a file that was already there.
    An audit's record lands after the audit closes, so the commit that adds it
    is the evidence arriving; only a later one changes what it says."""
    changed = subprocess.run(["git", "-C", root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-c", "--diff-filter=MD", commit, "--", path],  # noqa: S603,S607 - fixed argv, a read
                             capture_output=True, text=True, timeout=30, check=False)
    return changed.returncode == 0 and bool(changed.stdout.strip())


def voided_by(root, sha, head, rid, findings, records_set, trunk):
    """An empty string when every commit this branch carries after the audited
    sha leaves the audit standing, the reading of the first one that voids it
    when one does, or None when git could not read the range. A commit stands
    when everything it touched is inside the records set, or when it is a fix
    commit citing a finding of this audit at a path that finding named.

    This audit's own record is its evidence and never a record it may edit.
    The `.dev/records/` prefix exempts the record's landing, which is right,
    and exempted its later rewriting with it, which is not: closed audit
    evidence could be edited after the fact and the gate would still read the
    audit as standing (20260916T122524Z-review-codex-05a9f5#6). The commit
    that adds the record stands; one that changes or removes it voids the
    audit, and a citation does not lift that."""
    commits = branch_commits(root, sha, head, trunk)
    if commits is None:
        return None
    evidence = f"{DISPATCH_RECORDS}{rid}.json"
    for commit in commits:
        # `-c` answers the ordinary diff on an ordinary commit and the
        # combined diff on a merge: what a merge resolved differently from
        # every parent is content no parent carried and no audit read.
        touched = subprocess.run(["git", "-C", root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-c", commit],  # noqa: S603,S607 - fixed argv, a read
                                 capture_output=True, text=True, timeout=30, check=False)
        if touched.returncode != 0:
            return None
        paths = [path for path in touched.stdout.splitlines() if path]
        if evidence in paths and edits(root, commit, evidence):
            return (f"{commit[:8]} changed {evidence} after it, which is this audit's "
                    "own record and is never a record a commit may edit")
        outside = [path for path in paths if not path.startswith(records_set)]
        if not outside:
            continue
        message = subprocess.run(["git", "-C", root, "log", "-1", "--format=%B", commit],  # noqa: S603,S607 - fixed argv, a read
                                 capture_output=True, text=True, timeout=30, check=False)
        if message.returncode != 0:
            return None
        if is_a_fix_for(message.stdout, rid, findings, paths):
            continue
        return (f"{commit[:8]} changed {outside[0]} after it, neither inside the records "
                f"set nor a fix commit citing a finding of {rid} at a path it names")
    return ""


def audit_of(root, head, rid, record, conf, records_set):
    """None when this record is a security audit closed at ``head`` with
    every finding disposed, else the (found, needed) halves of the refusal
    that say which part is short. It never refuses itself: one function
    emits the class, so the registry closes over it exactly."""
    status = field(record, "status")
    if status != "closed":
        return (f"{rid} is status={status}",
                "wait for the audit to close, then commit its record")
    harness = field(record, "harness.name")
    if harness != AUDIT_HARNESS:
        return (f"{rid} ran on harness {harness}, not {AUDIT_HARNESS}",
                f"re-run it on the {AUDIT_HARNESS} harness at the model ops/lane.conf names")
    # The conf names the model, read at check time; the gate never carries a
    # model string of its own (2026-09-16).
    wanted = conf.get(AUDIT_MODEL_KEY)
    if not wanted:
        return (f"ops/lane.conf names no {AUDIT_MODEL_KEY}",
                (f"name the audit model in ops/lane.conf as {AUDIT_MODEL_KEY}; "
                 "could not look is not a pass"))
    model, disagreement = agreed(record, ("model.requested", "model.ran"))
    if model is None:
        return f"{rid} model {disagreement}", f"re-run it on {AUDIT_HARNESS} at {wanted}"
    if model != wanted:
        return (f"{rid} ran model {model}, not {wanted} (ops/lane.conf {AUDIT_MODEL_KEY})",
                f"re-run it on {AUDIT_HARNESS} at {wanted}")
    # A read-role job commits nothing, so the ref it read and the head it
    # reports are one sha. lineage.base_sha is the base, not the head.
    sha, disagreement = agreed(record, ("snapshot.ref_sha", "result.head"), sha=True)
    if sha is None:
        return f"{rid} audited sha {disagreement}", f"re-run the {AUDIT_JOB} at the head"
    if not a_commit_id(sha):
        return (f"{rid} audited sha {sha[:24]!r} is not a commit id",
                (f"re-run the {AUDIT_JOB} at the head; the record's sha reaches git as "
                 "a range operand and is 7 to 40 hex characters, never an option"))
    findings = field(record, "result.envelope.findings")
    if not isinstance(findings, list):
        return (f"{rid} carries no result.envelope.findings array",
                (f"commit the closed record of the {AUDIT_JOB} with its envelope; an "
                 "absent findings array is not an audit with nothing left to dispose"))
    if sha != head:
        if not records_set:
            return ("the audit row declares no records set",
                    "add `records` to the audit row of ops/devlane/hooks/boundaries.json")
        void = voided_by(root, sha, head, rid, findings, records_set,
                         conf.get("TRUNK_BRANCH"))
        if void is None:
            return (f"git could not read {sha[:8]}..{head[:8]}",
                    "could not look is not a pass; fetch the audited commit and retry")
        if void:
            return (f"{rid} audited {sha[:8]}; {void}",
                    (f"re-run the {AUDIT_JOB} at {head[:8]}, or apply it as a fix "
                     f"commit citing `Finding: {rid}#<n>` with its `Verified:` line, "
                     "at least one of whose touched paths that finding's `where` names"))
    disposed = dispositions_at(root, head)
    undisposed = [f"{rid}#{n}" for n in range(1, len(findings) + 1)
                  if (disposed.get(f"{rid}#{n}") or "open") == "open"]
    if undisposed:
        return ((f"{rid} at {sha[:8]}, {len(undisposed)} of {len(findings)} findings "
                 f"undisposed: {', '.join(undisposed)}"),
                ("findings.py disposition <key> <how> <why> for each, then "
                 f"findings.py export --write {FINDINGS_EXPORT} and commit it"))
    return None


def audit_at_head(root, head, body):
    """The audit row. Owner, 2026-09-16: "i'd like if you have daybreak
    security audits made required for draft pr's - and resolution required
    before they can be made ready for review". The rule text is the audit
    row of CONTRIB.md's ready table (PR #245); this is its gate, carried by
    the pr-proof boundary because the same command crosses it."""
    row = declared_boundaries().get("audit") or {}
    records_set = tuple(row.get("records") or ())
    expected = f"a closed {AUDIT_JOB} record at {head[:8]} with every finding disposed"
    needed = f"dispatch {AUDIT_JOB} at the head and dispose each finding"
    section = proof_section(body)
    if section is None:
        return deny("audit", expected, "no `## Proof` section in the PR body", needed)
    ids = []
    for found in RECORD_ID.findall(section):
        if found not in ids:
            ids.append(found)
    if not ids:
        return deny("audit", expected,
                    f"a `## Proof` section naming no {AUDIT_JOB} record", needed)
    records = {}
    for rid in ids:
        record = json_at(root, head, DISPATCH_RECORDS + rid + ".json")
        if record is None:
            return deny("audit", expected,
                        f"{rid} absent from {DISPATCH_RECORDS} at {head[:8]}",
                        f"commit the record of the {AUDIT_JOB} at the head")
        records[rid] = record
    audits = [(rid, r) for rid, r in records.items() if field(r, "job") == AUDIT_JOB]
    if not audits:
        return deny("audit", expected,
                    ", ".join(f"{rid} is job={field(r, 'job')}" for rid, r in records.items()),
                    needed)
    # A body may name an audit and its re-run; one that holds is enough, and
    # when none holds the first one's reading is the refusal.
    short = []
    for rid, record in audits:
        reading = audit_of(root, head, rid, record, lane_conf(root), records_set)
        if reading is None:
            return finding_dispositions(root, head, section, rid,
                                        field(record, "result.envelope.findings"))
        short.append(reading)
    return deny("audit", expected, short[0][0], short[0][1])
def pr_proof(toks, cwd):
    """Owner, 2026-09-12: "it has to be impossible to not do the work".
    A PR is marked ready or merged only against proof the system verifies
    here: the rows the body names exist at the head, and a green ci run
    on that head executed them. No grant escape."""
    if not toks or base(toks[0]) != "gh" or len(toks) < 3 or toks[1] != "pr" or toks[2] not in ("ready", "merge"):
        return None
    verb = f"gh pr {toks[2]}"
    needed_proof = ("add rows under ops/conductor/handoff/probes/ on the branch, name their ids under "
                    "a `## Proof` heading in the PR body, push, and wait for the ci run on that head")
    number = pr_number(toks)
    if number is None:
        return deny("pr-proof", f"{verb} naming the PR number", "no number", "name the PR number")
    # -R/--repo selects another repository; the proof is read there (Codex
    # review of PR #158, 2026-09-12), never from this repository's PR N.
    selected = gh_repo(toks)
    at = ["-R", selected] if selected else []
    view = subprocess.run(["gh", "pr", "view", number, *at, "--json", "body,headRefOid,headRefName"],  # noqa: S603,S607 - PATH gh; a read
                          cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    if view.returncode != 0:
        return deny("pr-proof", f"a readable PR #{number}",
                    f"gh pr view failed: {(view.stderr or view.stdout).strip().splitlines()[:1]}",
                    "could not look is not a pass; fix gh and retry")
    try:
        data = json.loads(view.stdout)
    except ValueError:
        return deny("pr-proof", f"JSON from gh pr view {number}", "unparseable output", "fix gh and retry")
    head = str(data.get("headRefOid") or "")
    branch = str(data.get("headRefName") or "")
    ids = proof_ids(str(data.get("body") or ""))
    if not ids:
        found = "no `## Proof` section in the PR body" if ids is None else "a `## Proof` section naming no P- row id"
        return deny("pr-proof", f"a `## Proof` section naming corpus rows (P-... ids) that pass at head {head[:8]}",
                    found, needed_proof)
    root = repo_root(cwd) or cwd
    present = subprocess.run(["git", "-C", root, "cat-file", "-e", f"{head}^{{commit}}"],  # noqa: S603,S607 - fixed argv, a read
                             capture_output=True, text=True, timeout=30, check=False)
    if present.returncode != 0:
        return deny("pr-proof", f"the PR head {head[:8]} fetched here", "not a local object",
                    f"git fetch origin {branch or '<branch>'} and retry")
    corpus = corpus_ids_at(root, head)
    if corpus is None:
        return deny("pr-proof", f"the probes corpus readable at {head[:8]}", "git could not read it",
                    "could not look is not a pass; check the head")
    missing = [i for i in ids if i not in corpus or not PROOF_ROW.fullmatch(i)]
    if missing:
        return deny("pr-proof", f"every Proof row present in {PROBES_DIR} at {head[:8]}",
                    f"absent: {', '.join(missing)}", needed_proof)
    runs = subprocess.run(["gh", "run", "list", *at, "--commit", head, "--json", "name,workflowName,status,conclusion,headSha"],  # noqa: S603,S607 - PATH gh; a read
                          cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    if runs.returncode != 0:
        return deny("pr-proof", f"the ci runs on {head[:8]}",
                    f"gh run list failed: {(runs.stderr or runs.stdout).strip().splitlines()[:1]}",
                    "could not look is not a pass; fix gh and retry")
    try:
        rows = json.loads(runs.stdout)
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        return deny("pr-proof", f"JSON from gh run list --commit {head[:8]}", "unparseable output", "fix gh and retry")
    ci = [r for r in rows if isinstance(r, dict) and CI_WORKFLOW in (r.get("workflowName"), r.get("name"))
          and str(r.get("headSha") or head) == head]
    done = [r for r in ci if r.get("status") == "completed"]
    if not done:
        state = ", ".join(str(r.get("status")) for r in ci) or "no ci run"
        return deny("pr-proof", f"a completed ci run on {head[:8]} (it executes the Proof rows)",
                    state, f"wait for it: gh run list --commit {head}")
    if not any(r.get("conclusion") == "success" for r in done):
        return deny("pr-proof", f"a green ci run on {head[:8]}",
                    f"conclusion {', '.join(str(r.get('conclusion')) for r in done)}",
                    "fix the branch until the run is green; the Proof rows are in it")
    # Ready only (2026-09-16, on the owner's words): GitHub refuses to merge
    # a draft, so the ready flip binds first, and the rows above bind the
    # lineage after it.
    if toks[2] == "ready":
        refused = audit_at_head(root, head, str(data.get("body") or ""))
        if refused is not None:
            return refused
    return None


CLAIM_WORDS = re.compile(
    r"\b(enforced|in force|done|landed|merged|green|complete|completed|"
    r"impossible|verified|proven|proved|passes|passing|works|wired|consumed|settled|holds|fixed)\b",
    re.IGNORECASE)
READING_TOKENS = re.compile(
    r"\b[0-9a-f]{7,40}\b|\d{8}T\d{6}Z-[a-z-]+-[0-9a-f]{6}|\bP-[A-Z0-9]+-\d{8}-[\w-]+|#\d+|\brun \d+|"
    r"\bRan \d+ tests|\b\d+ of \d+\b|\b\d+ (?:OK|red|failures?|errors?)\b|\bexit(?:=| )\d|"
    r"\bnot measured\b|\bunmeasured\b|\bunresolved\b|\bnot verified\b|\bnot enforced\b|\bnot yet\b",
    re.IGNORECASE)
PUBLISH_PATHS = re.compile(r"(^|/)(ops/conductor/|\.claude/skills/|docs/)|(^|/)(AGENTS|CONTRIB|ops/README)\.md$")
HEREDOC_WRITE = re.compile(
    r"(?:cat|tee)(?:\s+-a)?\s*>{1,2}\s*(\S+)\s*<<-?\s*(['\"]?)(\w+)\2[^\n]*\n(.*?)\n\3(?:\n|$)", re.DOTALL)
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”')


def unmeasured_claim(text):
    """The first line that claims without a reading in its paragraph, or None.
    Quoted spans are somebody else's words and are not the writer's claim;
    a paragraph opening with `>` is a quotation."""
    for paragraph in re.split(r"\n\s*\n", text or ""):
        lines = [line for line in paragraph.splitlines() if line.strip()]
        if not lines or lines[0].lstrip().startswith(">"):
            continue
        stripped = QUOTED.sub("", paragraph)
        if READING_TOKENS.search(stripped):
            continue
        for line in lines:
            if CLAIM_WORDS.search(QUOTED.sub("", line)):
                return line.strip()
    return None


def published_texts(toks, text, cwd):
    """(surface, text) pairs a statement publishes: a commit message, a PR
    body or comment, a record or law page written by heredoc."""
    found = []
    if toks and base(toks[0]) == "git":
        sub, rest, _ = _git_subcommand(toks, cwd)
        if sub == "commit":
            for i, tok in enumerate(rest):
                if tok in ("-m", "--message") and i + 1 < len(rest):
                    found.append(("commit message", rest[i + 1]))
                elif tok.startswith("-m") and len(tok) > 2:
                    found.append(("commit message", tok[2:]))
                elif tok in ("-F", "--file") and i + 1 < len(rest):
                    body = read_text(os.path.normpath(os.path.join(cwd, os.path.expanduser(rest[i + 1]))))
                    if body is not None:
                        found.append((f"commit message {rest[i + 1]}", body))
    if toks and base(toks[0]) == "gh" and len(toks) > 2 and toks[1] in ("pr", "issue"):
        for i, tok in enumerate(toks):
            if tok == "--body" and i + 1 < len(toks):
                found.append((f"gh {toks[1]} {toks[2]} --body", toks[i + 1]))
            elif tok == "--body-file" and i + 1 < len(toks):
                body = read_text(os.path.normpath(os.path.join(cwd, os.path.expanduser(toks[i + 1]))))
                if body is not None:
                    found.append((f"gh {toks[1]} {toks[2]} --body-file {toks[i + 1]}", body))
    return found


def heredoc_records(text, cwd):
    """(path, body) for every heredoc written onto a publishing surface."""
    found = []
    for match in HEREDOC_WRITE.finditer(text or ""):
        target = match.group(1).strip("'\"")
        _, rel = relative(os.path.normpath(os.path.join(cwd, os.path.expanduser(target))), cwd)
        if PUBLISH_PATHS.search(rel or target):
            found.append((target, match.group(4)))
    return found


TRUNK_WRITES = ("commit", "merge", "cherry-pick", "rebase", "revert", "am")


def _git_subcommand(toks, cwd):
    """(subcommand, rest, directory) for a git statement, honouring -C and -c."""
    args = toks[1:]
    directory = cwd
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "-C" and i + 1 < len(args):
            directory = os.path.normpath(os.path.join(cwd, os.path.expanduser(args[i + 1])))
            i += 2
            continue
        if tok.startswith("-C") and len(tok) > 2:
            directory = os.path.normpath(os.path.join(cwd, os.path.expanduser(tok[2:])))
            i += 1
            continue
        if tok in ("-c", "--git-dir", "--work-tree") and i + 1 < len(args):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    rest = args[i:]
    return (rest[0] if rest else None), rest[1:], directory


def check_text(text, cwd, depth=0):
    root = repo_root(cwd)
    conf = lane_conf(root)
    lane = conf.get("LANE_NAME")
    for statement in commands(text):
        toks = tokens_of(statement)
        if not toks:
            continue
        if depth == 0 and GRAFT_LEVER.rsplit("/", 1)[-1] in " ".join(toks) and "query" in toks:
            graft_log(lane, "query", " ".join(t for t in toks if t.startswith("graft_")) or "query")
        refusal = graft_first(toks, cwd, root, lane)
        if refusal:
            return refusal
        refusal = deliverable(toks, cwd, lane)
        if refusal:
            return refusal
        refusal = pr_proof(toks, cwd)
        if refusal:
            return refusal
        kind, flags = dispatch_args(toks)
        if kind:
            refusal = check_dispatch(kind, flags, cwd, text)
            if refusal:
                return refusal
        if depth == 0 and base(toks[0]) in ("bash", "sh", "source", ".") and len(toks) > 1:
            if toks[1] == "-c" and len(toks) > 2:
                body = toks[2]
                refusal = check_text(body, cwd, depth + 1)
                if refusal:
                    return refusal
            else:
                script = os.path.normpath(os.path.join(cwd, os.path.expanduser(toks[1])))
                _, rel = relative(script, cwd)
                if os.path.isfile(script) and not is_code(rel):
                    body = read_text(script)
                    if body is not None:
                        refusal = check_text(expand(body, cwd), cwd, depth + 1)
                        if refusal:
                            return refusal
    return None


def decide(payload):
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool == "Bash":
        command = tool_input.get("command", "")
        return check_text(command, cwd) if command else None
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    try:
        refusal = decide(payload)
    except Exception as error:  # noqa: BLE001 - a hook that dies takes the session with it
        print(json.dumps(guidance.respond({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": f"[matrix:hook-error] hook-error: expected a decision; found {type(error).__name__}: {error}; needed inspect the diagnostic failure",
        }})))
        return 0
    if refusal:
        print(json.dumps(guidance.respond(refusal)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
