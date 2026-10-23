#!/usr/bin/env python3
"""Before anything consequential: get up to date, then decide.

Reads a PreToolUse payload on stdin. Warns when a command with outward effect
is about to run against refs that moved. Silent otherwise.

The distinction that keeps this bearable is CONSEQUENCE, not risk. Reading, building,
committing locally — all reversible, all silent here. Pushing, opening or editing a PR,
publishing, deploying: those leave the machine and cannot be taken back, and those are the
ones where a timely reading can inform the decision.

Fetch before every such command, then compare the refs the command depends on: the
pushed branch's own remote ref, and for a PR its base. If none moved, the command proceeds
silently. If one did, it warns because every distance, merge-base and
ahead/behind computed earlier may be stale, and the reason says exactly which ref
changed and by how much. Movement on other refs is another lineage's business (LD-10).

Native harness permissions remain separate from this advice.

Which repository gets fetched is the command's to say, not the hook's. `git -C other
push`, `git --git-dir=other/.git push` and `GIT_DIR=other/.git git push` all leave the
machine from `other`; a diagnostic that fetches and compares in its own process cwd
would report on the wrong repository.

`CLAUDE_PRECHECK_NO_FETCH=1` skips the fetch, so the hook stays inert offline.

The corpus lives in `ops/devlane/hooks/tests/`.
"""

import json
import logging
import os
import re
import subprocess
import sys

# `.claude/settings.json` runs every hook in this directory by path, so a sibling import is
# free — but only once the directory is on the path, which it is not when python reads the
# script from stdin.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from command_shape import commands, statements, tokens

CONSEQUENTIAL = re.compile(
    r"^git\s+push\b"
    r"|^gh\s+(?:pr|release|issue)\s+(?:create|merge|edit|close|reopen|comment|ready)\b"
    r"|^gh\s+api\b.*-X\s*(?:POST|PATCH|PUT|DELETE)"
    r"|^(?:npm|yarn|pnpm)\s+publish\b"
    r"|^(?:twine|cargo)\s+(?:upload|publish)\b"
    r"|^docker\s+push\b"
    r"|^(?:kubectl|terraform|serverless|flyctl|vercel|netlify)\s+(?:apply|deploy|destroy|promote)\b",
    re.IGNORECASE,
)
def is_consequential(cmd):
    """Does any command position in this text invoke something that leaves the machine?

    Where the command positions ARE is `command_shape`'s question, not this file's. It used
    to be answered here, in a copy that missed `git -C . push` entirely and read a `git
    push` written inside a heredoc note as a real one.
    """
    return any(CONSEQUENTIAL.search(c) for c in commands(cmd))


ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: git's own global options that take a SEPARATE operand. Only the ones that
#: select a repository are read; the rest are here so the operand is stepped
#: over rather than mistaken for the subcommand. `-c` is why this list exists:
#: a walk that ate `-C other` and then read `-c push.default=simple` as the
#: subcommand never saw the `push` behind it.
GIT_OPTS_WITH_OPERAND = frozenset((
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env",
))


def _unquote(token):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def selected_repo(cmd):
    """The git global options naming the repository a push will run against.

    Returns a list of arguments to pass to git (`["-C", path]` or
    `["--git-dir", path]`, possibly both), or `[]` when the command names no
    repository — then the hook's own cwd is the repository, which is what it
    always was.

    Where the words are is `command_shape`'s question; this reads them.
    """
    for stmt in statements(cmd):
        words = tokens(stmt)
        env_git_dir = None
        i = 0
        while i < len(words):
            word = _unquote(words[i])
            if word.startswith("GIT_DIR="):
                env_git_dir = _unquote(word.split("=", 1)[1])
                i += 1
                continue
            if ASSIGNMENT.match(word):
                i += 1
                continue
            if word.rsplit("/", 1)[-1] != "git":
                env_git_dir = None          # an assignment binds one command
                i += 1
                continue
            i += 1
            work = git_dir = None
            while i < len(words) and words[i].startswith("-"):
                opt = words[i]
                if opt.startswith("--git-dir="):
                    git_dir = _unquote(opt.split("=", 1)[1])
                elif opt.startswith("-C") and opt != "-C":
                    work = _unquote(opt[2:])
                elif opt in ("-C", "--git-dir") and i + 1 < len(words):
                    value = _unquote(words[i + 1])
                    if opt == "-C":
                        work = value
                    else:
                        git_dir = value
                    i += 1
                elif opt in GIT_OPTS_WITH_OPERAND:
                    i += 1
                i += 1
            subcommand = _unquote(words[i]) if i < len(words) else ""
            if subcommand == "push":
                selected = []
                if work:
                    selected += ["-C", work]
                if git_dir or env_git_dir:
                    selected += ["--git-dir", git_dir or env_git_dir]
                return selected
            env_git_dir = None
    return []


def clone_git_dir(selected, *, strict=False):
    """The SHARED git directory of the repository `selected` names.

    Not a linked worktree's private one. `FETCH_HEAD` is a per-worktree file:
    a fetch run at `.git/worktrees/wt` leaves the clone's own `FETCH_HEAD`
    untouched, so the next reader still sees a repository that has never
    fetched. Remote-tracking refs live in the common dir either way, so
    resolving it once is also what makes the comparison the right one.

    Returns None when nothing there is a repository — then there is nothing to
    compare and the command proceeds.
    """
    base = os.getcwd()
    if "-C" in selected:
        base = os.path.abspath(
            os.path.join(base, selected[selected.index("-C") + 1])
        )
    try:
        proc = subprocess.run(["git", *selected, "rev-parse", "--git-common-dir"],  # noqa: S603, S607 — PATH git; the arguments are options this hook just read out of the command
                              capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        if strict:
            raise
        logging.exception("could not resolve the selected repository")
        return None
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        if strict:
            raise OSError("selected repository unavailable")
        return None
    return os.path.abspath(os.path.join(base, out))


def tracked_refs(git_dir, *, strict=False):
    """Remote-tracking refs and where they point, so movement can be named exactly."""
    try:
        proc = subprocess.run(["git", "--git-dir", git_dir,  # noqa: S603, S607 — PATH git; git_dir is a path git itself resolved
                              "for-each-ref", "--format=%(refname:short) %(objectname)",
                              "refs/remotes/"], capture_output=True, text=True, timeout=10,
                             check=False)
        if strict and proc.returncode:
            raise OSError("required remote-ref reading failed")
        out = proc.stdout
    except Exception:
        if strict:
            raise
        logging.exception("could not list remote-tracking refs")
        return {}
    refs = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[0]] = parts[1]
    return refs


PR_BASE = re.compile(r"--base(?:=|\s+)(\S+)")


def current_branch(selected):
    """The checked-out branch of the selected repository, or None when detached."""
    try:
        proc = subprocess.run(["git", *selected, "symbolic-ref", "--short", "-q", "HEAD"],  # noqa: S603, S607 — PATH git; the arguments are options this hook read out of the command
                              capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        logging.exception("could not read the current branch")
        return None
    return proc.stdout.strip() or None


def default_branch(selected):
    """`origin/main`-style name the remote's HEAD points at, or None."""
    try:
        proc = subprocess.run(["git", *selected, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],  # noqa: S603, S607 — PATH git; the arguments are options this hook read out of the command
                              capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        logging.exception("could not read the remote default branch")
        return None
    return proc.stdout.strip() or None


def push_targets(cmd):
    """`remote/branch` for every explicit push destination in the command."""
    targets = []
    for stmt in statements(cmd):
        words = [_unquote(w) for w in tokens(stmt)]
        if "push" not in words:
            continue
        rest = [w for w in words[words.index("push") + 1:] if not w.startswith("-")]
        if len(rest) >= 2:
            remote, *refspecs = rest
            for spec in refspecs:
                dst = spec.lstrip("+").split(":")[-1]
                if dst:
                    targets.append(f"{remote}/{dst.rsplit('/', 1)[-1]}")
    return targets


def relevant_refs(cmd, selected, refs):
    """The remote-tracking refs this command's outcome depends on.

    A push depends on the pushed branch's own remote ref; opening or editing
    a PR depends on its base too, the named one or the remote's default
    branch. Movement elsewhere is another lineage's business: two worktrees
    on two branches must not refuse each other's merges. Detached HEAD keeps the
    old breadth, every ref, whatever the command names.
    """
    branch = current_branch(selected)
    if branch is None:
        return set(refs)
    targets = push_targets(cmd)
    wanted = {r for r in refs if r.endswith("/" + branch)}
    for target in targets:
        tail = target.split("/", 1)[-1]
        wanted |= {r for r in refs if r == target or r.endswith("/" + tail)}
    if any(re.match(r"gh\b.*\bpr\b", c) for c in commands(cmd)):
        base = PR_BASE.search(cmd)
        if base:
            wanted |= {r for r in refs if r.endswith("/" + base.group(1))}
        else:
            default = default_branch(selected)
            if default is None:
                return set(refs)
            # for-each-ref shortens the symbolic origin/HEAD to plain "origin".
            wanted |= {r for r in refs if r == default or r == default.split("/", 1)[0]}
    return wanted


def decide(payload, *, strict=False):
    """Existing context check, callable by the shared guard."""
    cmd = payload["tool_input"]["command"]
    if not is_consequential(cmd):
        return None

    git_dir = clone_git_dir(selected_repo(cmd), strict=strict)
    if git_dir is None:
        return None              # the command names nothing this hook can compare

    before = tracked_refs(git_dir, strict=strict)
    if strict or os.environ.get("CLAUDE_PRECHECK_NO_FETCH") != "1":
        try:
            fetched = subprocess.run(["git", "--git-dir", git_dir, "fetch", "--all", "--quiet"],  # noqa: S603, S607 — PATH git; git_dir is a path git itself resolved
                           capture_output=True, timeout=45, check=False)
            if strict and fetched.returncode:
                raise OSError("required fetch failed")
        except Exception:
            if strict:
                raise
            logging.exception("git fetch failed")
            return None          # offline or slow: never block work over it
    after = tracked_refs(git_dir, strict=strict)

    relevant = relevant_refs(cmd, selected_repo(cmd), set(before) & set(after))
    moved = [(r, before[r], after[r]) for r in sorted(relevant)
             if before[r] != after[r]]
    if not moved:
        return None              # fetched, nothing changed, carry on — no interruption

    lines = []
    for ref, a, b in moved:
        try:
            n = subprocess.run(["git", "--git-dir", git_dir, "rev-list", "--count", f"{a}..{b}"],  # noqa: S603, S607 — PATH git; range is SHAs this hook just read
                               capture_output=True, text=True, timeout=10, check=False).stdout.strip()
        except Exception:
            logging.exception("could not count commits on moved ref")
            n = "?"
        lines.append(f"  {ref}: {a[:8]} -> {b[:8]} ({n} commits)")

    reason = (
        "Not up to date — so I fetched, and things moved:\n"
        + "\n".join(lines)
        + "\n\nThe fetch is done. But this command has outward "
        "effect, and every ahead/behind, merge-base, commit count and PR diff you measured "
        "before now was computed against the older refs. Re-take whatever this command "
        "depends on, then run it again — it will pass."
    )
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _advice(value, event="PreToolUse"):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import guidance
    return guidance.respond(value, event)


def main():
    try:
        payload = json.load(sys.stdin)
        result = decide(payload)
    except (ValueError, TypeError, KeyError):
        return
    if result:
        print(json.dumps(_advice(result)))


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("corpus moved to ops/devlane/hooks/tests/", file=sys.stderr)
        sys.exit(2)
    main()
