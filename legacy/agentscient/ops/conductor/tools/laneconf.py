#!/usr/bin/env python3
"""Read and check the lane's shared, literal-only configuration.

Worker configuration amendment (2026-09-08):
ops/devlane/dispatch/CONTRACT.md owns execution and capability restrictions.
This loader owns two additional settings in the existing literal grammar:

* WORKER_HARNESSES is whitespace-separated unique names matching
  [a-z][a-z0-9]*. Absence means 'claude codex grok' for compatibility;
  an explicitly empty/whitespace-only value means no workers. Duplicate or
  malformed names refuse worker. Listing a name never implements its adapter.
* EXECUTABLE_<UPPERCASE_NAME>, when present, is one nonempty absolute path
  to a regular executable file. Spaces are literal; no shell splitting,
  expansion, flags or relative-path interpretation. Absence resolves the
  selected name on PATH. For claude/codex/grok without an override, preserve
  shutil.which(name) or name, including its existing PATH-miss behavior.
  Overrides and agy require absolute resolution including symlink targets;
  failure refuses executable, never falls back from an invalid override.

For each key, presence resolves environment > lane.local.conf beside the
LANE_CONF/default file > that file > the compatibility default. Environment
keys work even when absent from both files. Blank overrides do not fall
through. Existing lane identity restrictions and model/effort keys remain.

Implementation must preserve closure(): declare WORKER_HARNESSES in tracked
ops/lane.conf or allow it in EXTERNAL_ENV_KEYS before adding its reader.
Add an EXECUTABLE_ prefix provision beside the MODEL_/EFFORT_ provision,
crediting the worker resolver so declared executable keys have a reader.
Pin declared/read closure in the existing test_laneconf.py suite.

The small new seam is worker(name: str) -> dict, raising ConfigError; it
validates list membership and executable resolution, not adapter capability.
Its exact result is {name: str, executable: str, sources: {workers: str,
executable: str}}. Source strings are 'env:KEY', 'file:ABSOLUTE_PATH#KEY',
'default' for the absent worker list, or 'PATH' for executable lookup.
CLI `laneconf.py worker NAME` prints that object as JSON plus one newline,
empty stderr, exit 0; ConfigError prints its refusal line on stderr, empty
stdout, exit 2. Wrapper and direct launch consume this same resolution;
launch owns the implemented-adapter check and records this object. No new
loader, executor, arbitrary argv settings or provider/model-role roster.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, emit
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

DEFAULT_CONF = Path(__file__).resolve().parents[2] / "lane.conf"
LOCAL_NAME = "lane.local.conf"
NAME_KEYS = {
    "LANE_NAME",
    "TRUNK_BRANCH",
    "RELEASE_BRANCH",
    "REPO_SLUG",
    "CHECK_NAMES",
}
REQUIRED_KEYS = {
    "LANE_NAME",
    "TRUNK_BRANCH",
    "RELEASE_BRANCH",
    "REPO_SLUG",
    "CHECK_NAMES",
    "WF_AGENT_DEFAULT",
    "LANE_AUTHOR",
    "MODEL_GROK",
    "MODEL_CODEX",
    "MODEL_CLAUDE_READ",
    "MODEL_CLAUDE_PLAN",
    "EFFORT_GROK",
    "EFFORT_CODEX",
    "EFFORT_CLAUDE",
    "WALL_DEFAULT",
    "SCOPE_CAP",
}
DECLARATION = re.compile(r"^([A-Z][A-Z0-9_]*)='([^']*)'\s*$")
SOURCE_SUFFIXES = {".py", ".sh"}
EXTERNAL_ENV_KEYS = {
    # Supplied by the finite controller to children and the read-only doctor.
    "CONTROL_STATE",
    "AGENTSCIENT_HOOK_PROFILE",
    "AGENTSCIENT_LAUNCH_SESSION",
    "COORDINATION_CAPABILITY",
    "AGENTSCIENT_ORG",
    "AGENTSCIENT_ROOT",
    "AGENTSCIENT_SIBLINGS",
    # The sessions lever's tmux socket and the attach-inside-tmux test
    # (sessions-lever.md: the socket and attach sections).
    "AGENTSCIENT_TMUX_SOCKET",
    "BASH_SOURCE",
    "BASH_REMATCH",
    "BOUNDARIES_PATH",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_PRECHECK_NO_FETCH",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_SESSION_ID",
    "CODEX_HOME",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "DETACH",
    "DETACH_WAIT",
    "DISPATCH_CONF",
    "DISPATCH_JOBS",
    "DISPATCH_STREAM_GRACE",
    "DISPATCH_TICK",
    "DISPATCH_TIMEOUT",
    "DISPATCH_TURNS",
    "FIXTURE_COPY_LIST",
    "EFFORT",
    "EVGREP_LIMIT",
    "GATE_LOG_DIR",
    "GATE_TAIL",
    "GITHUB_EVENT_PATH",
    # GitHub supplies these only to the CI adapter; none reach test children.
    "GITHUB_OUTPUT", "GITHUB_REPOSITORY", "GITHUB_RUN_ATTEMPT", "GITHUB_RUN_ID",
    "GITHUB_SHA", "GITHUB_STEP_SUMMARY", "GITHUB_TOKEN",
    "RUNNER_NAME", "RUNNER_TEMP", "RUNNER_TOOL_CACHE",
    "CI_RUNNER_MODE", "CI_RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion",
    "GROK_HOME",
    "GROK_SESSION_ID",
    "HARNESS",
    "HOME",
    "ISO_ENV",
    "ISO_ENV_CODEX_HOME",
    "ISO_FLAGS",
    "ISO_STORE",
    "LANE_CONF",
    "LAUNCHER_PIN",
    "PATH",
    "PROBES_TIMEOUT",
    "REPO",
    "REPOS",
    "SIBLINGS",
    "SUITES_SERIAL",
    "TERM_WALL",
    "THREADS_OUT",
    "TMPDIR",
    "TMUX",
    "WATCH_TICK_SCALE",
    "WATCHDOG_TICK",
    "WF_AGENT",
    "WF_AGENT_OVERRIDE",
    "XDG_STATE_HOME",
}

# Unit 1A deliberately leaves the other consumers for run B. Naming the
# temporary exclusions makes that incomplete frontier visible and prevents a
# blanket directory exemption from hiding new spread.
DEFERRED_LITERAL_PATHS = {
    ".claude/skills/pr-overview/pr_overview.py",
    "ops/bin/board.sh",
    "ops/bin/link-ops.sh",
    "ops/conductor/tools/branches.py",
    "ops/conductor/tools/ci.py",
    "ops/conductor/tools/co_doc.py",
    "ops/conductor/tools/decisions.py",
    "ops/conductor/tools/findings.py",
    "ops/conductor/tools/gate.sh",
    "ops/conductor/tools/hand.py",
    "ops/conductor/tools/land.py",
    "ops/conductor/tools/onboard.py",
    "ops/conductor/tools/skills.py",
    "ops/devlane/dispatch/levers/codex/codex-dispatch.sh",
    "ops/devlane/dispatch/levers/grok-dispatch.sh",
    "ops/devlane/hooks/claude/context-stream.sh",
    "ops/devlane/hooks/claude/unsafe-command.py",
    "ops/devlane/telemetry/worth.py",
    "ops/devlane/workflow/checks/commit_trailers.py",
}


class ConfigError(Refusal):
    """The shared refusal, under the name its seven consumers catch.

    `line(kind)` projects the same refusal under a caller's class token
    (the launcher and the spend meter rename `key` for their own wire);
    the format string lives in instrument.py alone."""

    def line(self, kind: str | None = None) -> str:
        if kind is None or kind == self.kind:
            return super().line()
        return Refusal(kind, self.expected, self.found, self.needed).line()


def conf_path() -> Path:
    """Return the explicit conf or the tracked file beside the lane."""
    return Path(os.environ.get("LANE_CONF", DEFAULT_CONF)).resolve()


def parse_file(path: Path) -> dict[str, str]:
    """Parse comments, blank lines, and literal single-quoted assignments."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(
            "conf-file",
            f"a readable lane conf at {path}",
            str(exc),
            "restore the file or set LANE_CONF",
        ) from exc
    values: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = DECLARATION.fullmatch(line)
        if match is None:
            raise ConfigError(
                "syntax",
                "a KEY='literal' assignment or comment",
                f"line {number} in {path}",
                "replace the line with a literal single-quoted assignment",
            )
        values[match.group(1)] = match.group(2)
    return values


def _configuration() -> tuple[dict[str, str], dict[str, str]]:
    """Resolve literal files and retain each winning declaration's source."""
    path = conf_path()
    resolved = parse_file(path)
    sources = {key: f"file:{path}#{key}" for key in resolved}
    local = path.with_name(LOCAL_NAME)
    if local.is_file():
        overrides = parse_file(local)
        forbidden = sorted(NAME_KEYS.intersection(overrides))
        if forbidden:
            key = forbidden[0]
            raise ConfigError(
                "local-key",
                "lane identity keys only in the tracked conf",
                f"{key} in {local}",
                f"remove {key} from {local}",
            )
        resolved.update(overrides)
        sources.update({key: f"file:{local.resolve()}#{key}" for key in overrides})
    for key in tuple(resolved):
        if key in os.environ:
            resolved[key] = os.environ[key]
            sources[key] = f"env:{key}"
    return resolved, sources


def values() -> dict[str, str]:
    """Resolve environment, local file, then tracked-file precedence."""
    return _configuration()[0]


def worker(name: str) -> dict:
    """Resolve configured membership and one literal executable, before launch."""
    resolved, sources = _configuration()
    executable_key = f"EXECUTABLE_{name.upper()}"
    for key in ("WORKER_HARNESSES", executable_key):
        if key in os.environ:
            resolved[key] = os.environ[key]
            sources[key] = f"env:{key}"
    names = resolved.get("WORKER_HARNESSES", "claude codex grok").split()
    malformed = [item for item in names if not re.fullmatch(r"[a-z][a-z0-9]*", item)]
    if malformed or len(names) != len(set(names)):
        raise ConfigError(
            "worker", "unique worker names matching [a-z][a-z0-9]*",
            repr(names), "correct WORKER_HARNESSES",
        )
    if not re.fullmatch(r"[a-z][a-z0-9]*", name) or name not in names:
        raise ConfigError(
            "worker", "a worker selected by WORKER_HARNESSES",
            f"{name!r} with workers {names!r}",
            "select a listed worker or correct WORKER_HARNESSES",
        )
    override = executable_key in resolved
    lookup = None if override else shutil.which(name)
    executable = resolved[executable_key] if override else lookup or name
    if override or name not in {"claude", "codex", "grok"}:
        try:
            path = Path(executable)
            if not executable or (override and not path.is_absolute()):
                raise ValueError("empty or relative executable override")
            if not override and lookup is None:
                raise ValueError("worker not found on PATH")
            path = path.resolve(strict=True)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError("not a regular executable file")
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(
                "executable", "one absolute path to a regular executable file",
                f"{executable!r}: {exc}",
                f"correct {executable_key} or install {name} on PATH",
            ) from exc
        executable = str(path)
    return {
        "name": name,
        "executable": executable,
        "sources": {
            "workers": sources.get("WORKER_HARNESSES", "default"),
            "executable": sources[executable_key] if override else "PATH",
        },
    }


def get(name: str) -> str:
    """Read one key, refusing rather than inventing a missing value."""
    resolved = values()
    if name in os.environ:
        return os.environ[name]
    if name not in resolved:
        path = conf_path()
        raise ConfigError(
            "key",
            f"{name} declared in the lane conf",
            f"no {name} in {path}",
            f"define {name} in {path}",
        )
    return resolved[name]


def _repo_root(path: Path) -> Path:
    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git", "-C", str(path.parent), "rev-parse", "--show-toplevel"
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode:
        detail = proc.stderr.strip() or f"git exited {proc.returncode}"
        raise ConfigError(
            "repo",
            f"{path} inside a git worktree",
            detail,
            "run the check against a lane checkout",
        )
    return Path(proc.stdout.strip()).resolve()


def _source_files(root: Path):
    for top in (root / "ops", root / ".claude"):
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*")):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            rel = path.relative_to(root)
            if any(part in {"tests", "fixtures", "archive", "infra"} for part in rel.parts):
                continue
            yield rel, path


def _reads(text: str) -> set[str]:
    patterns = (
        r"\bget\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]",
        r"\bos\.environ\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]",
        r"\bos\.environ\.get\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]",
        r"\$\{([A-Z][A-Z0-9_]*)",
    )
    reads = {
        match.group(1)
        for pattern in patterns
        for match in re.finditer(pattern, text)
    }
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return reads
    iterates_conf_keys = any(
        isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "CONF_KEYS"
        for node in ast.walk(tree)
    )
    if not iterates_conf_keys:
        return reads
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "CONF_KEYS"
            for target in node.targets
        ):
            continue
        with contextlib.suppress(ValueError, TypeError):
            reads.update(
                key for key in ast.literal_eval(node.value)
                if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            )
    return reads


def _workflow_jobs(text: str) -> set[str]:
    """The workflow uses JSON syntax within YAML (CONTRACT-ci.md)."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError("workflow", "the workflow in JSON syntax", "unreadable workflow",
                          "restore the shared CI format") from exc
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        raise ConfigError("workflow", "a nonempty workflow jobs mapping", repr(jobs),
                          "declare the required jobs")
    # The final acceptance job covers its selected dependencies. Requiring
    # every conditional job would make an intentionally skipped lane block work.
    covered = jobs.get("lane", {}).get("needs", [])
    if isinstance(covered, str):
        covered = [covered]
    return set(jobs).difference(covered)


def closure() -> int:
    """Check declarations, readers, and workflow-job closure."""
    path = conf_path()
    declared = parse_file(path)
    root = _repo_root(path)
    readers: dict[str, set[str]] = {}
    laneconf_in_tree = root / "ops/conductor/tools/laneconf.py"
    if laneconf_in_tree.is_file():
        for key in REQUIRED_KEYS:
            readers.setdefault(key, set()).add(
                "ops/conductor/tools/laneconf.py"
            )
        for key in declared:
            if key.startswith("EXECUTABLE_"):
                readers.setdefault(key, set()).add("ops/conductor/tools/laneconf.py")
    for rel, source in _source_files(root):
        text = source.read_text(encoding="utf-8", errors="replace")
        for key in _reads(text):
            readers.setdefault(key, set()).add(rel.as_posix())
    dispatch = root / "ops/conductor/tools/dispatch.sh"
    if dispatch.is_file() and "model_job" in dispatch.read_text(
        encoding="utf-8", errors="replace"
    ):
        for key in declared:
            if key.startswith(("MODEL_", "EFFORT_")):
                readers.setdefault(key, set()).add(dispatch.relative_to(root).as_posix())

    findings: list[str] = []
    for key in sorted(set(declared).difference(readers)):
        findings.append(f"unread declaration: {key} in {path}")
    for key in sorted(set(readers).difference(declared, EXTERNAL_ENV_KEYS)):
        locations = ", ".join(sorted(readers[key]))
        findings.append(f"undeclared read: {key} in {locations}")

    workflow = root / ".github/workflows/ci.yml"
    if workflow.is_file() and "CHECK_NAMES" in declared:
        jobs = _workflow_jobs(
            workflow.read_text(encoding="utf-8", errors="replace")
        )
        names = set(declared["CHECK_NAMES"].split())
        for job in sorted(jobs.difference(names)):
            findings.append(
                f"workflow job missing from CHECK_NAMES: {job} in "
                f"{workflow.relative_to(root)}"
            )
        for name in sorted(names.difference(jobs)):
            findings.append(
                f"CHECK_NAMES entry missing from workflow jobs: {name} in "
                f"{workflow.relative_to(root)}"
            )
    if findings:
        print("\n".join(findings))
        return 1
    return 0


def _literal_exempt(rel: str, line: str, needle: str) -> bool:
    stripped = line.strip()
    if needle == "agentscient":
        return (rel, stripped) in {
            ("ops/devlane/hooks/enforcement.py", 'if server in {"agentscient-lane", "agentscient_lane"}:'),
            ("ops/devlane/hooks/enforcement.py", 'if registry.get("schema") != "agentscient.boundaries/2" or not isinstance(rows, list) or not rows:'),
            ("ops/devlane/hooks/profiles.py", 'configs[native_home / ".gemini/config/hooks.json"] = canonical({"agentscient-shared": {"enabled": True, **event_groups}})'),
            ("ops/devlane/hooks/profiles.py", 'configs[native_home / "hooks/agentscient.json"] = canonical({"hooks": event_groups})'),
            (
                "ops/devlane/dispatch/record.py",
                'POLICY_SCHEMA = "agentscient.lane-policy/v1"',
            ),
            (
                "ops/conductor/tools/lane_read.py",
                'SCHEMA = "agentscient.lane/v1"',
            ),
            # The sessions lever's contract fixes its team schema and its
            # tmux owner option by name (sessions-lever.md).
            (
                "ops/conductor/tools/sessions.py",
                'SCHEMA_NAME = "agentscient-team/1"',
            ),
            (
                "ops/conductor/tools/sessions.py",
                'OWNER_OPTION = "@agentscient_owner"',
            ),
            (
                "ops/conductor/tools/lane_mcp.py",
                '"serverInfo": {"name": "agentscient-lane", "version": "1"},',
            ),
            # The launcher hands workers that same fixed protocol identity
            # (grant 158, 2026-09-12): the server name and its TOML key.
            (
                "ops/devlane/dispatch/launch.py",
                'LANE_MCP_SERVER = "agentscient-lane"',
            ),
            (
                "ops/devlane/dispatch/launch.py",
                'LANE_MCP_KEY = "agentscient_lane"',
            ),
        }
    if rel == "ops/devlane/dispatch/record.py" and needle == '"dev"':
        return stripped in {
            'lane = "dev"',
            'source["lane"] = "dev"',
            '_need(rec["lane"] == "dev", "lane", "must be dev")',
        }
    if rel == "ops/devlane/dispatch/launch.py" and needle == '"dev"':
        return '"WF_LANE": "dev"' in line or '"lane": "dev"' in line
    # Symfony's standard APP_ENV names are framework identifiers, not
    # branch or lane configuration. Keep this exception at their owner.
    if rel == "ops/conductor/tools/project_providers/symfony.py" and needle == '"dev"':
        return stripped in {
            'self.env = config.get("environment", "dev")',
            'if self.env not in {"dev", "test"}:',
        }
    return False


def literals() -> int:
    """Find project and branch literals that escaped the shared conf."""
    path = conf_path()
    configured = parse_file(path)
    root = _repo_root(path)
    tokens = {'"dev"', "origin/dev", "'agentscient'", "uscient/agentscient", "/home/work/"}
    # Job names are schema identifiers, not project/branch literals. In
    # particular a sole check named "lane" must not ban that ordinary word.
    # closure() separately checks CHECK_NAMES against the workflow's jobs.
    for key in NAME_KEYS - {"CHECK_NAMES"}:
        value = configured.get(key, "")
        if len(value) >= 4:
            tokens.add(value)

    findings: list[str] = []
    for rel_path, source in _source_files(root):
        rel = rel_path.as_posix()
        if rel in DEFERRED_LITERAL_PATHS or source.resolve() == Path(__file__).resolve():
            continue
        for number, line in enumerate(
            source.read_text(encoding="utf-8", errors="replace").splitlines(),
            1,
        ):
            for token in sorted(tokens, key=lambda value: (-len(value), value)):
                if token in line and not _literal_exempt(rel, line, token):
                    findings.append(f"{rel}:{number}: forbidden literal {token}")
    print("skipped: ops/devlane/infra")
    if findings:
        print("\n".join(findings))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 2 and args[0] == "get":
            print(get(args[1]))
            return 0
        if len(args) == 2 and args[0] == "worker":
            print(json.dumps(worker(args[1])))
            return 0
        if args == ["closure"]:
            return closure()
        if args == ["literals"]:
            return literals()
        raise ConfigError(
            "arguments",
            "get KEY, worker NAME, closure, or literals",
            " ".join(args) or "no arguments",
            "choose one documented verb",
        )
    except ConfigError as exc:
        return emit(exc)


if __name__ == "__main__":
    raise SystemExit(main())

