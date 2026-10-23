"""Load the hyphenated hook scripts and build throwaway git / PATH fixtures.

The real repository is never the fixture: every git directory these helpers
create lives under the caller's tempdir, and every stub binary is written
there too. A plant that did not land raises ``PlantFailed`` — that is
INVALID, not a verdict on the hook.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

from ops.testkit import PlantFailed, Result, Timeout, hermetic_env, make_executable, run
from ops.testkit import plant_text as _kit_plant_text
from ops.testkit import require as _kit_require
from ops.testkit.world import FIXED_DATE

TESTS_DIR = Path(__file__).resolve().parent
HOOKS_DIR = TESTS_DIR.parent
CLAUDE_DIR = HOOKS_DIR / "claude"
WORKTREE_ROOT = TESTS_DIR.parents[3]
# The counterparty of the `dispatcher` row: a case that needs a Dispatcher
# named drives the real lever rather than writing the engine's rows itself,
# so the store shape has one author (docs/verified-couplings.md, shape).
DISPATCHER_LEVER = WORKTREE_ROOT / "ops" / "conductor" / "tools" / "dispatcher.py"

RUN_CAP = 120  # the one timeout of this suite's subprocesses (docs/tests.md rule 5)

# PlantFailed is the kit's: INVALID, not a verdict (docs/tests.md).


def load_claude(filename: str, modname: str):
    """Load a script from ``claude/`` as a module without running ``__main__``."""
    path = CLAUDE_DIR / filename
    if not path.is_file():
        raise PlantFailed(
            f"INVALID: hook script does not exist yet: {path}"
        )
    if modname in sys.modules:
        return sys.modules[modname]
    # The hyphenated scripts import ``command_shape`` from this directory.
    claude = str(CLAUDE_DIR)
    if claude not in sys.path:
        sys.path.insert(0, claude)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise PlantFailed(f"INVALID: cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def isolated_env(home: Path, extra_path=None, extra=None):
    """Env that cannot see the real repo's git config or hook escape hatches:
    the kit's hermetic environment for ``home``, ``extra_path`` first on
    PATH. The offline escape CLAUDE_PRECHECK_NO_FETCH never reaches it
    (a D2 case with it set is INVALID); the kit carries no host value."""
    return hermetic_env(home, bin=extra_path, extra={"TESTKIT_DIR": str(WORKTREE_ROOT / "ops" / "testkit"), **(extra or {})})


def run_cmd(argv, cwd, env, *, expect=None, stdin=None):
    """Run in the fixture environment and retain the kit's complete result."""
    try:
        result = run(argv, cwd=cwd, env=env, stdin=stdin, timeout=RUN_CAP, expect=expect)
    except Timeout as exc:
        raise PlantFailed(f"INVALID: {exc}") from exc
    return result


def git(cwd, env, *args, expect=0):
    return run_cmd(["git", *args], cwd, env, expect=expect)


def configure_identity(repo: Path, env):
    git(repo, env, "config", "user.name", "Test User")
    git(repo, env, "config", "user.email", "test@example.invalid")


def plant_text(path: Path, content: str, *, recognisable: str | None = None) -> str:
    """Write ``content`` and prove it landed intact (the kit's plant)."""
    return _kit_plant_text(path, content, recognisable=recognisable).read_text(encoding="utf-8")


def plant_executable(path: Path, content: str) -> Path:
    """Write a stub binary, prove it is executable, prove it is the file we wrote."""
    path = Path(path)
    _kit_plant_text(path, content, recognisable=content.splitlines()[0][:20])
    make_executable(path)
    if not path.is_file():
        raise PlantFailed(f"INVALID: {path} is not a file")
    return path


def bash_payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def write_payload(file_path=None, relative_path=None, cwd=None) -> str:
    tool_input = {}
    if file_path is not None:
        tool_input["file_path"] = file_path
    if relative_path is not None:
        tool_input["relative_path"] = relative_path
    payload = {"tool_name": "Edit", "tool_input": tool_input}
    if cwd is not None:
        payload["cwd"] = cwd
    return json.dumps(payload)


def run_script(script: Path, payload: str, cwd: Path, env) -> Result:
    if not script.is_file():
        raise PlantFailed(f"INVALID: script missing: {script}")
    argv = (["python3", str(script)] if script.suffix == ".py"
            else ["bash", str(script)])
    return run_cmd(argv, cwd, env, stdin=payload, expect=None)


def has_additional_context(stdout: str) -> bool:
    if not stdout or not stdout.strip():
        return False
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return "additionalContext" in stdout
    hook = data.get("hookSpecificOutput") or {}
    return bool(hook.get("additionalContext"))


def permission_decision(stdout: str):
    if not stdout or not stdout.strip():
        return None, ""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None, stdout
    hook = data.get("hookSpecificOutput") or {}
    return hook.get("permissionDecision"), hook.get("permissionDecisionReason") or ""


class StaleClone:
    """Bare remote + clone whose FETCH_HEAD is gone and whose origin is behind."""

    def __init__(self, tmp: Path, env):
        self.tmp = tmp
        self.env = env
        self.seed = tmp / "seed"
        self.bare = tmp / "remote.git"
        self.clone = tmp / "clone"
        self.old_sha = None
        self.new_sha = None

    def build(self):
        self.seed.mkdir()
        git(self.seed, self.env, "-c", "init.defaultBranch=main", "init", "-q")
        configure_identity(self.seed, self.env)
        plant_text(self.seed / "file.txt", "one\n", recognisable="one")
        git(self.seed, self.env, "add", "file.txt")
        git(self.seed, self.env, "commit", "-q", "-m", "A", "--date", FIXED_DATE)
        self.old_sha = git(
            self.seed, self.env, "rev-parse", "HEAD"
        ).stdout.strip()
        if len(self.old_sha) < 7:
            raise PlantFailed("INVALID: seed commit SHA missing")

        git(self.tmp, self.env, "clone", "--bare", "-q",
            str(self.seed), str(self.bare))
        git(self.tmp, self.env, "clone", "-q", str(self.bare), str(self.clone))
        configure_identity(self.clone, self.env)

        fetch_head = self.clone / ".git" / "FETCH_HEAD"
        # git 2.43's clone from a local path does not write FETCH_HEAD.
        # That is already D2's missing-FETCH_HEAD state. Older gits do
        # write one; delete it so both land on the same fixture.
        if fetch_head.is_file():
            before = fetch_head.read_bytes()
            if not before:
                raise PlantFailed("INVALID: FETCH_HEAD was already empty")
            fetch_head.unlink()
        if fetch_head.exists():
            raise PlantFailed("INVALID: FETCH_HEAD is still present")

        origin_main = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if origin_main != self.old_sha:
            raise PlantFailed(
                f"INVALID: clone origin/main is {origin_main}, not seed {self.old_sha}"
            )

        plant_text(self.seed / "file.txt", "two\n", recognisable="two")
        landed = (self.seed / "file.txt").read_text(encoding="utf-8")
        if landed == "one\n":
            raise PlantFailed("INVALID: seed file was not advanced")
        git(self.seed, self.env, "add", "file.txt")
        git(self.seed, self.env, "commit", "-q", "-m", "B", "--date", FIXED_DATE)
        self.new_sha = git(
            self.seed, self.env, "rev-parse", "HEAD"
        ).stdout.strip()
        if self.new_sha == self.old_sha:
            raise PlantFailed("INVALID: seed HEAD did not move")
        git(self.seed, self.env, "push", "-q", str(self.bare), "main")
        bare_head = git(
            self.bare, self.env, "rev-parse", "HEAD"
        ).stdout.strip()
        if bare_head != self.new_sha:
            raise PlantFailed(
                f"INVALID: bare remote still at {bare_head}, not {self.new_sha}"
            )

        still = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if still != self.old_sha:
            raise PlantFailed(
                "INVALID: clone origin/main moved before the hook ran"
            )
        if (self.clone / ".git" / "FETCH_HEAD").exists():
            raise PlantFailed(
                "INVALID: FETCH_HEAD reappeared before the hook ran"
            )
        return self

    def restale(self):
        """Put origin/main back behind the remote and drop FETCH_HEAD again."""
        git(self.clone, self.env, "update-ref", "refs/remotes/origin/main",
            self.old_sha)
        fetch_head = self.clone / ".git" / "FETCH_HEAD"
        if fetch_head.exists():
            fetch_head.unlink()
        now = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if now != self.old_sha:
            raise PlantFailed("INVALID: restale did not rewind origin/main")
        if fetch_head.exists():
            raise PlantFailed("INVALID: restale left FETCH_HEAD in place")

    def refresh(self):
        """Fetch until origin/main matches the remote. Prove it landed."""
        git(self.clone, self.env, "fetch", "--all", "-q")
        now = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if now != self.new_sha:
            raise PlantFailed(
                f"INVALID: refresh left origin/main at {now}, "
                f"not remote {self.new_sha}"
            )
        return self


def require_jq():
    try:
        _kit_require("jq")
    except PlantFailed as exc:
        raise PlantFailed("INVALID: jq is not on PATH; ruff-after-edit.sh cannot emit") from exc


def rev_parse_path(cwd, env, flag: str, sandbox: Path) -> Path:
    """Resolve ``git rev-parse <flag>`` to an absolute path inside *sandbox*.

    Used to tell a linked worktree's private git-dir from the clone's
    common dir without touching the real repository.
    """
    out = git(cwd, env, "rev-parse", flag).stdout.strip()
    if not out:
        raise PlantFailed(f"INVALID: git rev-parse {flag} was empty in {cwd}")
    path = Path(os.path.abspath(os.path.join(str(cwd), out)))
    sand = os.path.realpath(str(sandbox)) + os.sep
    real = os.path.realpath(str(path))
    if not real.startswith(sand):
        raise PlantFailed(
            f"INVALID: git rev-parse {flag} resolved to {path}, "
            f"outside sandbox {sandbox}"
        )
    if not path.exists():
        raise PlantFailed(f"INVALID: git rev-parse {flag} path missing: {path}")
    return path


def prove_linked_worktree(main: Path, linked: Path, env, sandbox: Path):
    """Prove this pair is a linked worktree of *main*, not a second clone.

    The one-stream finding is invisible when git-dir equals common-dir
    (the primary checkout). A fixture that failed to diverge is INVALID
    — later assertions would be answering a question about nothing.
    Also proves ``.git`` is a directory in the primary and a gitfile in
    the linked tree: that is the input a script which uses the common
    dir only when cwd looks like a main worktree gets wrong.
    """
    main_git = rev_parse_path(main, env, "--git-dir", sandbox)
    main_common = rev_parse_path(main, env, "--git-common-dir", sandbox)
    link_git = rev_parse_path(linked, env, "--git-dir", sandbox)
    link_common = rev_parse_path(linked, env, "--git-common-dir", sandbox)

    if os.path.realpath(str(main_common)) != os.path.realpath(str(link_common)):
        raise PlantFailed(
            "INVALID: linked worktree does not share the clone's common dir"
        )
    if os.path.realpath(str(main_git)) != os.path.realpath(str(main_common)):
        raise PlantFailed(
            "INVALID: primary git-dir is not the common dir; "
            "the fixture is not a normal checkout"
        )
    if os.path.realpath(str(link_git)) == os.path.realpath(str(link_common)):
        raise PlantFailed(
            "INVALID: linked worktree git-dir equals common-dir; "
            "this fixture cannot expose a split stream"
        )
    if not (main / ".git").is_dir():
        raise PlantFailed("INVALID: primary .git is not a directory")
    gitfile = linked / ".git"
    if gitfile.is_dir():
        raise PlantFailed(
            "INVALID: linked worktree .git is a directory, not a gitfile"
        )
    if not gitfile.is_file():
        raise PlantFailed("INVALID: linked worktree has no .git gitfile")
    return link_git, link_common


# -- the security audit a ready flip is measured against ---------------------

AUDIT_RECORD_ID = "20260916T120000Z-review-codex-a1b2c3"
AUDIT_MODEL = "gpt-daybreak-blue-latest"
DISPATCH_RECORDS = ".dev/records/dispatches"
FINDINGS_EXPORT = "ops/conductor/findings/findings.jsonl"


AUDIT_WHERE = "ops/devlane/hooks/claude/new.py"


def security_audit_record(*, audited, findings=2, job="security-audit",
                          status="closed", harness="codex", ran=AUDIT_MODEL,
                          requested=AUDIT_MODEL, ref=None, result_head=None,
                          base=None, where=AUDIT_WHERE):
    """A security-audit dispatch record shaped as launch.py writes one.
    The head is snapshot.ref_sha and result.head, which agree on a read-role
    job because it commits nothing; lineage.base_sha is the base."""
    return {
        "id": AUDIT_RECORD_ID, "lane": "dev", "stage": "review",
        "unit": "lane/fixture",
        "lineage": {"branch": "lane/fixture",
                    "base_sha": audited if base is None else base},
        "job": job, "role": "read",
        "snapshot": {"mode": "whole", "ref_sha": audited if ref is None else ref},
        "harness": {"name": harness},
        "model": {"requested": requested, "ran": ran},
        "result": {
            "head": audited if result_head is None else result_head,
            "envelope": {
                "job": job, "status": "ok", "verdict": "changes",
                "findings": [
                    {"severity": "p1", "where": f"{where}:{n}", "claim": f"finding {n}"}
                    for n in range(1, findings + 1)],
            },
        },
        "status": status,
    }


def findings_export(*rows):
    """The findings export, the rows `findings.py export --write` writes."""
    return "".join(
        json.dumps({"key": key, "record": AUDIT_RECORD_ID, "job": "security-audit",
                    "disposition": how, "n": n}, sort_keys=True) + "\n"
        for n, (key, how) in enumerate(rows, 1))


def audit_disposed(n=2):
    """Every finding of the fixture's audit, disposed."""
    return tuple((f"{AUDIT_RECORD_ID}#{i}", "fixed") for i in range(1, n + 1))


FINDINGS_TOOL = "ops/conductor/tools/findings.py"


def findings_vocabulary():
    """findings.py's DISPOSITIONS line as this tree carries it. A world plants
    it at FINDINGS_TOOL so its head carries the vocabulary the
    finding-disposition row reads there, and the rows follow the ledger's
    words rather than a copy of them."""
    source = Path(__file__).resolve().parents[4] / FINDINGS_TOOL
    lines = [line for line in source.read_text(encoding="utf-8").splitlines()
             if line.startswith("DISPOSITIONS = (")]
    assert len(lines) == 1, f"INVALID: {FINDINGS_TOOL} carries {len(lines)} DISPOSITIONS lines"
    return lines[0] + "\n"


def proof_findings(*rows):
    """The `## Proof` lines the finding-disposition row reads: each finding
    named, its disposition token, and one line of judgement."""
    return "".join(f"- {key} {how}: the judgement on {key}.\n" for key, how in rows)


def diagnostic(stdout):
    if not stdout.strip():
        return None, ""
    out = json.loads(stdout)
    spec = out.get("hookSpecificOutput", {})
    assert "permissionDecision" not in spec, out
    assert "decision" not in out and out.get("continue") is not False, out
    text = spec.get("additionalContext") or out.get("systemMessage", "")
    return ("warn", text.removeprefix("Diagnostic (advisory): ")) if text else (None, "")
