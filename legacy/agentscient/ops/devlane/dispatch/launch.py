#!/usr/bin/env python3
"""Policy launcher for dev-lane task dispatches."""

from __future__ import annotations

import argparse
import contextlib
import copy
import faulthandler
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import record

APP = Path(__file__).resolve().parent
# jobs.json stays in the task app (it is data, read by path, not an import);
# dispatch composes task's job registry with harness's isolation/wires.
JOBS_PATH = APP.parent / "task" / "jobs.json"
HARNESS_APP = APP.parent / "harness"
TELEMETRY_APP = APP.parent / "telemetry"
# The conductor lane's coordination core, reached as a lever run as a
# subprocess and never imported. The dev lane already names ops/conductor
# only as paths it points a worker at; an import would put the core's
# module state and the lane's state store inside every case of this app's
# suite, and would make a conductor refusal raise here instead of arriving
# on the wire. The edge is the lever's CLI and its refusal wire, which the
# conductor's conformance registry already asserts.
COORDINATION = APP.parents[1] / "conductor" / "tools" / "coordination.py"
BIND_TIMEOUT = 60
REFUSAL = 3
STAGES = ("plan", "tests", "check-tests", "code", "review", "adjudicate")
OWNER_NAME = "xormania"
OWNER_EMAIL = "127287135+xormania@users.noreply.github.com"
monotonic = time.monotonic
# The Python stack on a crash lands in the job's stderr; a stderr with no
# file descriptor (a test loading the module) is not a place to write it.
with contextlib.suppress(Exception):
    faulthandler.enable()
sleep = time.sleep
# How often the wait loop reads the session stream (the expensive part:
# a glob of the job home and a regex over a multi-megabyte file). The
# cheap checks, the child's exit, the wall and the grace, run every 50 ms.
TICK = float(os.environ.get("DISPATCH_TICK", "1"))


class Refused(Exception):
    pass


def _module(name, path):
    spec = importlib.util.spec_from_file_location(f"task_launch_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LANECONF = _module(
    "laneconf",
    APP.parent.parent / "conductor" / "tools" / "laneconf.py",
)
CORRECTION = _module(
    "correction",
    APP.parent.parent / "conductor" / "tools" / "correction.py",
)
ENVELOPE = _module("envelope", APP.parent / "task" / "envelope.py")
WRITE_PATH = _module("write_path", APP.parent / "task" / "write_path.py")
ENVELOPE_SCHEMA_JSON = json.dumps(ENVELOPE.ENVELOPE_SCHEMA, separators=(",", ":"))


def _graft_call(name, *args):
    module = _module("graft", HARNESS_APP / "graft.py")
    try:
        return getattr(module, name)(*args)
    except module.Refused as exc:
        raise Refused(str(exc)) from exc


def _verify_graft(rec, job_dir, snapshot):
    metadata = rec["brief"].get("graft")
    if metadata is None:
        return
    _graft_call("verify_worker", job_dir, snapshot, metadata)
    if metadata.get("state") == "ready":
        descriptor = json.loads((job_dir / "context/graft.json").read_text())
        if descriptor["role"] != rec["role"] or rec["job"] in {"author-tests", "check-tests"}:
            _refuse("graft-context", "the recorded job's source grant", "foreign role",
                    "create a fresh dispatch")
    elif rec["job"] not in {"author-tests", "check-tests"}:
        _refuse("graft-context", "a ready context for this job", "foreign withheld metadata",
                "create a fresh dispatch")


def _lane_values(*names):
    resolved = {}
    for name in names:
        try:
            resolved[name] = LANECONF.get(name)
        except LANECONF.ConfigError as exc:
            kind = "conf-key" if exc.kind == "key" else exc.kind
            raise Refused(exc.line(kind)) from exc
    return resolved


def _worker(name):
    try:
        worker = LANECONF.worker(name)
    except LANECONF.ConfigError as exc:
        raise Refused(exc.line()) from exc
    if name not in {"claude", "codex", "grok", "agy"}:
        _refuse("worker", "an implemented worker adapter", name,
                "select claude, codex or grok until this adapter is implemented")
    return worker


def _git(repo, *args, check=True, env=None, input=None, text=True):
    e = dict(os.environ if env is None else env)
    e.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
              "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=e,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
    )
    if check and p.returncode:
        error = p.stderr.strip()
        if isinstance(error, bytes):
            error = error.decode(errors="replace")
        raise RuntimeError(error or f"git exited {p.returncode}")
    return p


def _refuse(ident, expected, found, satisfy):
    raise Refused(
        f"{ident}: expected {expected}; found {found}; "
        f"needed {satisfy}; satisfy by {satisfy}"
    )


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(text):
    """A UTC stamp `_now` wrote, back to an aware datetime.

    Records from 2026-08 carry fractional seconds and records since do not,
    so both are accepted; anything else raises and its caller keeps the job.
    """
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError(f"not a UTC stamp: {text!r}")
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)


def mint_id(stage, harness):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{stage}-{harness}-{os.urandom(3).hex()}"


def _portable_paths(rec):
    """A deep copy of the record with every host-absolute path rewritten to
    the two encodings the harnesses derive from the snapshot path, Claude's
    project slug and Grok's URL-encoded cwd, rewritten as
    ``<snapshot-slug>`` and ``<snapshot-encoded>``. Plain ``/home/<user>/``
    paths stay as they are: the term wall exempts that one form on purpose,
    and every reader of a record treats them as real paths. The two
    encodings are what the wall refuses (2026-09-03: a branch refused in 5 s
    over eight committed records). ``_absolute_paths`` inverts it on
    load."""
    snapshot = str((rec.get("snapshot") or {}).get("root") or "")
    subs = []
    if snapshot.startswith("/"):
        subs.append((re.sub(r"[/.]", "-", snapshot), "<snapshot-slug>"))
        subs.append((urllib.parse.quote(snapshot, safe=""), "<snapshot-encoded>"))

    def walk(value):
        if isinstance(value, str):
            for old, new in subs:
                if old in value:
                    value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [walk(x) for x in value]
        if isinstance(value, dict):
            return {k: walk(x) for k, x in value.items()}
        return value

    return walk(rec)


def _absolute_paths(rec):
    """The inverse of ``_portable_paths``: resolve the two placeholders from
    the record's own snapshot root, so readers see the real stream paths."""
    def walk(value, subs):
        if isinstance(value, str):
            for old, new in subs:
                if old in value:
                    value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [walk(x, subs) for x in value]
        if isinstance(value, dict):
            return {k: walk(x, subs) for k, x in value.items()}
        return value

    snapshot = str((rec.get("snapshot") or {}).get("root") or "")
    if snapshot.startswith("/"):
        rec = walk(rec, [("<snapshot-slug>", re.sub(r"[/.]", "-", snapshot)),
                         ("<snapshot-encoded>", urllib.parse.quote(snapshot, safe=""))])
    return rec


def _jobs_root():
    if os.environ.get("DISPATCH_JOBS"):
        return Path(os.environ["DISPATCH_JOBS"])
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path(os.environ.get("HOME", "")) / ".local/state"
    return base / _lane_values("LANE_NAME")["LANE_NAME"] / "dispatch"


def _job_paths(job_id):
    """Return the only job directory and snapshot a selected id may name."""
    if (not isinstance(job_id, str) or not job_id or job_id in {".", ".."}
            or "/" in job_id or "\\" in job_id or "\0" in job_id):
        _refuse("record-target", "one dispatch id path component", repr(job_id),
                "name the exact dispatch id")
    root = _jobs_root().resolve(strict=False)
    job_dir = root / job_id
    resolved_job = job_dir.resolve(strict=False)
    if resolved_job != job_dir:
        _refuse("record-target", f"the canonical job directory {job_dir}",
                str(resolved_job), "restore the job below the current dispatch store")
    snapshot = job_dir / "snapshot"
    resolved_snapshot = snapshot.resolve(strict=False)
    if resolved_snapshot != snapshot:
        _refuse("record-target", f"the exact snapshot {snapshot}",
                str(resolved_snapshot), "replace the escaped snapshot with the job snapshot")
    return job_dir, snapshot


def _bind_record(rec, selected_id):
    """Validate record identity, then rebase a portable record to this store."""
    job_dir, snapshot = _job_paths(selected_id)
    record.validate_context(rec, expected_id=selected_id)
    stored = Path(rec["snapshot"]["root"])
    if (".." in stored.parts or stored.name != "snapshot"
            or stored.parent.name != selected_id):
        _refuse("record-target", f"a snapshot ending in {selected_id}/snapshot",
                str(stored), "restore the dispatch record's snapshot path")
    if (stored.exists() or stored.is_symlink()) and stored.resolve(strict=False) != snapshot:
        _refuse("record-target", f"the current dispatch snapshot {snapshot}",
                str(stored.resolve(strict=False)),
                "move the job into the current dispatch store without an escaped symlink")
    stored_job = stored.parent
    isolation_data = (rec.get("harness") or {}).get("isolation") or {}
    harness = (rec.get("harness") or {}).get("name")
    isolation = _module("isolation_rebind", HARNESS_APP / "isolation.py")
    spec = isolation.HARNESSES.get(harness)
    if spec is None:
        _refuse("isolation", "a harness isolation entry", str(harness),
                "restore the recorded harness name")
    if spec["mechanism"] == "home":
        home = job_dir / "home" / harness
        store = home / spec["sessions"]["path"]
        for label, value, expected in (
                ("home", isolation_data.get("home"), home),
                ("store", isolation_data.get("store"), store),
                *((f"env.{key}", value, home)
                  for key, value in (isolation_data.get("env") or {}).items()
                  if key in {spec.get("home_env"), *spec.get("also_env", ())}),
        ):
            if not isinstance(value, str):
                continue
            found = Path(value)
            if ((found.exists() or found.is_symlink())
                    and found.resolve(strict=False) != expected):
                _refuse("record-target", f"{label} at {expected}",
                        str(found.resolve(strict=False)),
                        "move the complete job without retaining a live foreign home")
        for label, expected in (("home", home), ("store", store)):
            if expected.resolve(strict=False) != expected:
                _refuse("record-target", f"the canonical isolation {label} {expected}",
                        str(expected.resolve(strict=False)),
                        "replace the escaped isolation path inside the current job")

    def rebase(value):
        if isinstance(value, str) and Path(value).is_absolute():
            try:
                return str(job_dir / Path(value).relative_to(stored_job))
            except ValueError:
                return value
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        return value

    rec = rebase(rec)
    rec["snapshot"]["root"] = str(snapshot)
    rec = _absolute_paths(rec)
    isolation_data = rec["harness"]["isolation"]
    if spec["mechanism"] == "home":
        home = job_dir / "home" / harness
        isolation_data["env"] = isolation.dispatch_env(harness, home, {})
        isolation_data["home"] = str(home)
        isolation_data["store"] = str(home / spec["sessions"]["path"])
        isolation_data.pop("flags", None)
    else:
        isolation_data["flags"] = isolation.dispatch_flags(harness)
        isolation_data.pop("env", None)
    record.validate_context(
        rec, expected_id=selected_id, expected_snapshot=snapshot,
    )
    if stored_job != job_dir:
        rec["_relocated_from"] = str(stored_job)
    return job_dir, snapshot, rec


def _bound_snapshot(rec):
    job_dir, snapshot = _job_paths(rec.get("id"))
    record.validate_context(
        rec, expected_id=rec["id"], expected_snapshot=snapshot,
    )
    return job_dir, snapshot


def _verify_record_ref(snapshot, ref_sha):
    verified = _git(
        snapshot, "rev-parse", "--verify", "--end-of-options",
        f"{ref_sha}^{{commit}}", check=False,
    )
    if verified.returncode or verified.stdout.strip() != ref_sha:
        _refuse("record-ref", "snapshot.ref_sha to name its exact commit",
                ref_sha,
                "restore the immutable dispatched commit in the bound snapshot")


def _repo():
    p = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(p.stdout.strip()).resolve()


def _branch(repo):
    p = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return p.stdout.strip() if p.returncode == 0 else "detached"


def _identity():
    value = os.environ.get("WF_AGENT")
    if not value or not re.fullmatch(r"[^<>\n]+ <[^<>\s@]+@[^<>\s]+>", value):
        _refuse("identity", "an identity in the Name <address> form",
                value or "unset", "export WF_AGENT='Your Model Name <noreply@vendor>'")
    return value


def _lane_author():
    value = _lane_values("LANE_AUTHOR")["LANE_AUTHOR"]
    match = re.fullmatch(r"([^<>\n]+) <([^<>\s@]+@[^<>\s]+)>", value)
    if match is None:
        _refuse("conf-value", "LANE_AUTHOR in the Name <address> form",
                repr(value), "correct LANE_AUTHOR in the lane conf")
    return match.groups()


def _inside(path, root):
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _dotdot_input(path, jobs_root):
    """A `..` component in an input that lies under the jobs root once
    collapsed: the gate classifies the collapsed path, so the spelling
    is refused rather than trusted (hand review of 7b7979b, 2026-09-11)."""
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
    if ".." not in candidate.parts:
        return False
    collapsed = Path(os.path.normpath(str(candidate)))
    root = Path(jobs_root).absolute()
    return collapsed == root or root in collapsed.parents


def _symlinked_input(path, jobs_root):
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
    if candidate.is_symlink():
        return True
    try:
        relative = candidate.relative_to(Path(jobs_root).absolute())
    except ValueError:
        return False
    current = Path(jobs_root).absolute()
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _parse_override(items, agent):
    out = []
    for item in items:
        ident, sep, reason = item.partition(":")
        if ident != "stale-base" or not sep or not reason.strip():
            _refuse("override", "stale-base with a non-empty reason", item,
                    "drop the override or pass stale-base:REASON")
        out.append({"refusal": ident, "reason": reason, "by": agent})
    return out


def _snapshot(repo, lineage, sha, root):
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "core.logAllRefUpdates", "false")
    _git(root, "fetch", "--quiet", str(repo),
         f"refs/heads/{lineage}:refs/heads/{lineage}")
    # An explicitly overridden off-lineage commit need not be reachable
    # from the lineage ref fetched above.
    _git(root, "fetch", "--quiet", str(repo), sha)
    (root / ".git" / "FETCH_HEAD").unlink(missing_ok=True)
    shutil.rmtree(root / ".git" / "logs", ignore_errors=True)
    _git(root, "checkout", "--quiet", "--detach", sha)
    shutil.rmtree(root / ".git" / "logs", ignore_errors=True)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _render(template, values):
    fields = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template)
    missing = [name for name in fields if name not in values]
    if missing:
        _refuse("render", "every template value supplied", missing[0],
                f"supply {missing[0]} before rendering")
    rendered = template
    for name in fields:
        rendered = rendered.replace(f"{{{name}}}", str(values[name]))
    return rendered


def _harness_meta(name, role, job_dir, env):
    isolation = _module("isolation", HARNESS_APP / "isolation.py")
    try:
        iso_env, flags = isolation.isolated(name, job_dir / "home" / name, env)
    except (isolation.NotIsolated, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        _refuse("isolation", "an isolated harness with credentials", str(exc),
                "add the isolation entry and required credential")
    spec = isolation.HARNESSES[name]
    sandbox = {"claude": "acceptEdits", "codex": "workspace-write",
               "grok": "always-approve", "agy": "always-proceed"}[name]
    if name == "codex" and role == "read":
        sandbox = "read-only"
    containment = "os" if name == "codex" else "policy"
    home = iso_env.get(spec.get("home_env", ""))
    store_base = Path(home) if spec["sessions"]["under"] == "minimal" else Path(env["HOME"]) / ".claude"
    store = store_base / spec["sessions"]["path"]
    data = {"mechanism": spec["mechanism"],
            "flags" if spec["mechanism"] == "flags" else "env": flags if spec["mechanism"] == "flags" else iso_env,
            "store": str(store), "observed": {"unresolved": "behavioural probe has not run"}}
    if home:
        data.update(home=home, auth_files=list(spec["auth_files"]))
    return iso_env, flags, sandbox, containment, data


def _preflight_isolation(name, target, env):
    isolation = _module("isolation_preflight", HARNESS_APP / "isolation.py")
    spec = isolation.HARNESSES.get(name)
    if spec is None:
        _refuse("isolation", "a harness isolation entry", name,
                "add an isolation entry with a measurement")
    if target.exists() and any(target.iterdir()):
        found = ", ".join(p.name for p in target.iterdir())
        _refuse("isolation", "an empty minimal home", found,
                "use a fresh job id and empty home")
    if spec["mechanism"] == "home":
        if name == "agy":
            try:
                source = isolation._real_home(name, env)
                missing = [f for f in spec["auth_files"]
                           if not (source / f).is_file()]
            except isolation.NotIsolated as exc:
                _refuse("isolation", "an explicit agy credential HOME", str(exc),
                        "set HOME to the measured credential source")
            if missing:
                _refuse("isolation", "the required agy credential file",
                        str(source / missing[0]), "install the measured auth leaf")
            return
        named = spec.get("home_env")
        source = Path(env.get(named) or Path(env.get("HOME", "")) / f".{name}")
        missing = [f for f in spec["auth_files"] if not (source / f).exists()]
        if missing:
            _refuse("isolation", "the required credential", str(source / missing[0]),
                    "install the credential in the harness home")
        # LD-18 (2026-09-11): an aged credential is refused here, before the
        # mint, so the refusal leaves no job directory behind.
        for leaf in spec["auth_files"]:
            try:
                isolation.check_credential_age(name, spec, leaf, source / leaf)
            except isolation.NotIsolated as exc:
                rule = spec.get("credential_age") or {}
                _refuse("isolation", "a credential within its measured refresh lifetime",
                        str(exc), f"{rule.get('remedy', 'log the harness in again')} on the host")


# A read role must be able to execute to judge: a skeptic that cannot run the
# suite or plant a mutant is structural (2026-08-29: four check-tests/review
# dispatches on claude reported "This command requires approval" for
# `python3 -c 'print(2+2)'` under --print acceptEdits). These rules are
# what the claude child may run without a prompt. Honest limits, measured
# by the first review round (ba0d93, ffaf14): claude has no home isolation
# (CONTRACT.md §isolation: mechanism `flags`), so anything a rule allows
# runs with the operator's uid. The list therefore names verification
# commands, never an interpreter (`python3 *`, `env *`, `find *`, `sed *`
# were arbitrary code and are gone); `python3 ops/*` runs the snapshot's
# own scripts, the same trust as running its suite. A write into the
# snapshot is recorded as residual and preserved (residual.patch), not
# refused. Real containment is Claude Code's OS sandbox — plan U2, gate
# G-CLI-1 — not this list.
CLAUDE_TOOL_RULES = (
    "Bash(python3 -m unittest *)", "Bash(python3 -m pytest *)",
    "Bash(python3 -m ruff *)", "Bash(python3 ops/*)",
    "Bash(ruff check *)", "Bash(ruff format --check *)",
    "Bash(cue vet *)", "Bash(cue export *)", "Bash(cue eval *)", "Bash(cue version)",
    "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
    "Bash(git status *)", "Bash(git rev-parse *)", "Bash(git ls-files *)",
    "Bash(git grep *)", "Bash(git blame *)",
    "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
    "Bash(grep *)", "Bash(rg *)", "Bash(wc *)", "Bash(uniq *)", "Bash(diff *)",
    "Bash(sha256sum *)", "Bash(jq *)", "Bash(which *)", "Bash(test *)", "Bash(cd *)",
)
# Tools no dispatch may use: the network (jobs say "no network"),
# project-supplied interpreters or scripts, and sub-agents (the owner's hard
# rule, 2026-08-29).
CLAUDE_DENIED_TOOLS = ("WebFetch", "WebSearch", "Agent", "Task")
CLAUDE_UNSAFE_RULE_PREFIXES = (
    "Bash(gh ", "Bash(composer ", "Bash(git ", "Bash(php ",
    "Bash(bash ops/bin/",
)


def _project_permissions(snapshot):
    """Carry trusted project rules on argv without changing operator config."""
    path = Path(snapshot) / ".claude" / "settings.json"
    if not path.is_file():
        return ({"unresolved": "project settings absent: .claude/settings.json"},
                (), ())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ({"unresolved": f"project settings unreadable: {exc}"}, (), ())
    permissions = data.get("permissions") if isinstance(data, dict) else None
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    if not isinstance(allow, list) or not isinstance(deny, list):
        return ({"unresolved": "permissions.allow and permissions.deny must be lists"},
                (), ())
    if not all(isinstance(rule, str) for rule in [*allow, *deny]):
        return ({"unresolved": "permission rules must be strings"}, (), ())
    dropped = [rule for rule in allow
               if rule.startswith(CLAUDE_UNSAFE_RULE_PREFIXES)]
    applied = [rule for rule in allow if rule not in dropped]
    return ({"source": ".claude/settings.json", "sha256": _sha(path),
             "allow": len(applied), "deny": len(deny), "dropped": dropped,
             "applied": "argv"}, tuple(applied), tuple(deny))


def _grok_permission(sandbox):
    """`--always-approve` is grok's own flag, not a --permission-mode value;
    web search and fetch are switched off beside it because every job says
    "no network" and always-approve would otherwise approve them too."""
    if sandbox == "always-approve":
        return ["--always-approve", "--disable-web-search"]
    return ["--permission-mode", sandbox, "--disable-web-search"]


def _effort_argv(harness, effort):
    """Return the exact argv tokens that carry the requested effort."""
    if not effort:
        return []
    if harness == "agy":
        if effort not in {"low", "medium", "high"}:
            _refuse("model", "agy effort low, medium or high", effort,
                    "select a supported effort")
        return ["--effort", effort]
    if harness == "claude":
        return ["--effort", effort]
    if harness == "codex":
        return ["-c", f'model_reasoning_effort="{effort}"']
    return ["--reasoning-effort", effort]


LANE_MCP_SERVER = "agentscient-lane"
LANE_MCP_KEY = "agentscient_lane"
LANE_MCP_TOOLS = ("lane_brief", "lane_jobs", "lane_changes", "lane_findings",
                  "lane_record", "lane_alerts", "lane_usage")
LANE_MCP_REL = Path("ops/conductor/tools/lane_mcp.py")
LANE_MCP_ABSENT = {"unresolved": "snapshot has no ops/conductor/tools/lane_mcp.py"}
COORD_MCP_SERVER = "coordination"
# The launcher's frozen runtime, never the worker's snapshot: this script's
# `import coordination` resolves from `sys.path[0]`, its own directory, so
# running it from a path the worker can write would let a worker-rewritten
# core answer with the dispatcher's own authority (finding 1 of
# 20260916T151749Z-review-codex-ef6db7).
COORD_MCP_REL = Path("ops/conductor/tools/coordination_mcp.py")
COORD_MCP_CONFIG_VERSION = 1
# The capability list is declared here and proved against the server's own
# catalogue by a case, rather than read out of the server at launch: the
# launcher reaches ops/conductor as a lever and never as an import, so a
# drift between the two is caught by a test and not by a worker.
COORD_MCP_TOOLS = (
    "coord_accept", "coord_ack", "coord_assignments", "coord_brief",
    "coord_claim", "coord_draft", "coord_inbox", "coord_observe",
    "coord_offer", "coord_outstanding", "coord_release", "coord_resolve",
    "coord_return", "coord_send", "coord_show", "coord_submit",
    "coord_verify",
)
# Codex today. The A-87 trial is a codex worker and the Lead scoped this
# slice to that launch; claude and grok take their servers by other routes
# and are named here rather than left to read as "nothing to wire".
COORD_MCP_HARNESSES = ("codex",)
# Shared core context, carried by each supported adapter's MCP transport.
# The legacy session key names the issued lane binding, not a vendor role.
COORD_MCP_ENV = ("CLAUDE_SESSION_ID", "XDG_STATE_HOME", "LANE_CONF",
                 "AGENTSCIENT_HOOK_PROFILE")
COORD_MCP_ABSENT = {
    "unresolved": "the issued worker runtime has no "
                  "ops/conductor/tools/coordination_mcp.py"}
COORD_MCP_UNBOUND = {
    "unresolved": "the dispatch named no assignment, so the core bound no "
                  "participant for this worker to act as"}
BRIEF_MARK = "== what exists (measured at launch) =="
BRIEF_CAP = 8000
REUSE_RE = re.compile(r"reused:\s*(.+)", re.DOTALL)
REUSE_CLAUSE = (
    "Before building anything, look up what exists: the list above, and the lane "
    f"MCP server `{LANE_MCP_SERVER}` (tools: {', '.join(LANE_MCP_TOOLS)}; read "
    "lane_brief first, then lane_jobs, lane_changes and lane_findings for the "
    "records this work follows). A capability that exists is reused, never "
    "rebuilt. In the envelope's `note` write `reused: <what you reused>` or "
    "`reused: nothing applied`; a write envelope without that line is invalid "
    "(owner, 2026-09-12: \"stop you or other agents from going outside the "
    "architecture, duplicating, or worse - ignoring\")."
)


def _acting_clause(meta):
    """What a wired worker is told it holds.

    A registered server the brief never names is what the A-87 trial
    already proved insufficient: the worker reached for a shell it could
    not write from and the assignment stayed unclaimed.
    """
    return (
        f"The acting MCP server `{COORD_MCP_SERVER}` carries this lane's "
        f"coordination verbs (tools: {', '.join(COORD_MCP_TOOLS)}). You are "
        f"bound to it as participant {meta['participant']}, dispatched for "
        f"assignment {meta['assignment']}. Read `coord_brief` first, then "
        f"`coord_claim` the assignment before you work it and `coord_submit` "
        f"the evidence when you are done; `coord_release` it instead if you "
        f"stop short. These are the only verbs that move the assignment: a "
        f"shell cannot, and work reported anywhere else is work the lane "
        f"cannot see."
    )


def _same_unit_records(repo, unit, exclude):
    """Newest-first record ids on the same unit, from the repo's own records."""
    found = []
    root = repo / ".dev/records/dispatches"
    if not unit or not root.is_dir():
        return found
    for path in sorted(root.glob("*.json"), reverse=True):
        if path.stem == exclude:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("unit") == unit and isinstance(data.get("id"), str):
            found.append(data["id"])
    return found


def _lane_mcp(job_dir, snapshot, job_id, repo, unit, follows):
    """Write the lane MCP v2 config for this dispatch; return (record meta, server argv)."""
    tool = Path(snapshot) / LANE_MCP_REL
    if not tool.is_file():
        return dict(LANE_MCP_ABSENT), None
    ids = []
    named = follows if isinstance(follows, (list, tuple)) else ([follows] if follows else [])
    for candidate in [str(item) for item in named if item] + _same_unit_records(repo, unit, job_id):
        if candidate and candidate not in ids and len(ids) < 20:
            ids.append(candidate)
    cfg = Path(job_dir) / "lane-mcp.json"
    cfg.write_text(json.dumps({
        "version": 2, "dispatch_store": str(Path(job_dir).resolve().parent),
        "job_ids": ids, "monitor_state": None, "consumer": job_id,
        "usage_rollout": None, "job_registry": str(JOBS_PATH),
    }, indent=1) + "\n", encoding="utf-8")
    server = ["python3", "-B", str(tool.resolve()), "--config", str(cfg.resolve()), "serve"]
    return {"config": str(cfg.resolve()), "sha256": _sha(cfg), "job_ids": ids,
            "server": server, "tools": list(LANE_MCP_TOOLS)}, server


def _coordination_mcp(job_dir, snapshot, harness, job_id, participant,
                      assignment, branch, stamp):
    """Write the acting config for this dispatch; return (meta, server argv).

    Every absence is declared with the reason, because the three that can
    happen here read alike from the record otherwise: a harness this slice
    does not wire, a snapshot without the tool, and a dispatch that bound
    no participant.
    """
    if harness not in COORD_MCP_HARNESSES:
        return {"unresolved": f"the acting server is wired for "
                              f"{', '.join(COORD_MCP_HARNESSES)} today; this "
                              f"dispatch is {harness}"}, None
    tool = Path(job_dir) / "runtime" / COORD_MCP_REL
    if not tool.is_file():
        return dict(COORD_MCP_ABSENT), None
    if not participant:
        return dict(COORD_MCP_UNBOUND), None
    cfg = Path(job_dir) / "coordination-mcp.json"
    cfg.write_text(json.dumps({
        "version": COORD_MCP_CONFIG_VERSION,
        "participant": participant,
        "assignment": assignment,
        "branch": branch,
        "worktree": str(Path(snapshot).resolve()),
        "record": job_id,
        "capabilities": list(COORD_MCP_TOOLS),
        "issued_at": stamp,
    }, indent=1) + "\n", encoding="utf-8")
    server = ["python3", "-B", str(tool.resolve()),
              "--config", str(cfg.resolve()), "serve"]
    return {"config": str(cfg.resolve()), "sha256": _sha(cfg),
            "participant": participant, "assignment": assignment,
            "server": server, "tools": list(COORD_MCP_TOOLS)}, server


def _codex_mcp_argv(key, server, env_keys=(), *, capabilities=None):
    """Project native transport permissions from the issued capability list.

    Required acting-server startup and per-tool approval admit the client call
    under approval_policy=never (native M-233). They grant no domain authority:
    the same issued config, participant, role and generation checks still run.
    """
    tokens = ["-c", f'mcp_servers.{key}.command="{server[0]}"',
              "-c", f"mcp_servers.{key}.args={json.dumps(server[1:])}"]
    if env_keys:
        tokens += ["-c", f"mcp_servers.{key}.env_vars={json.dumps(env_keys)}"]
    if capabilities is not None:
        tokens += ["-c", f"mcp_servers.{key}.required=true",
                   "-c", f"mcp_servers.{key}.enabled_tools={json.dumps(list(capabilities))}"]
        for name in capabilities:
            tokens += ["-c", f'mcp_servers.{key}.tools.{name}.approval_mode="approve"']
    return tokens


def _mcp_argv(harness, server, job_dir, coord=None):
    """The argv tokens that hand a harness its MCP servers; [] for none."""
    if not server:
        return []
    if harness == "claude":
        path = Path(job_dir) / "mcp.json"
        path.write_text(json.dumps({"mcpServers": {LANE_MCP_SERVER: {
            "command": server[0], "args": server[1:]}}}, indent=1) + "\n", encoding="utf-8")
        return ["--mcp-config", str(path.resolve())]
    if harness == "codex":
        tokens = _codex_mcp_argv(LANE_MCP_KEY, server)
        if coord:
            tokens += _codex_mcp_argv(COORD_MCP_SERVER, coord, COORD_MCP_ENV,
                                    capabilities=COORD_MCP_TOOLS)
        return tokens
    return []


def _grok_mcp_config(home, server):
    """Grok reads MCP servers from $GROK_HOME/config.toml (measured 2026-09-12)."""
    if not server or not home:
        return None
    path = Path(home) / "config.toml"
    args = ",\n".join("    " + json.dumps(token) for token in server[1:])
    block = (f"[mcp_servers.{LANE_MCP_KEY}]\ncommand = {json.dumps(server[0])}\n"
             f"args = [\n{args},\n]\nenabled = true\n")
    prior = path.read_text(encoding="utf-8") if path.is_file() else ""
    if f"[mcp_servers.{LANE_MCP_KEY}]" not in prior:
        path.write_text(prior + ("\n" if prior and not prior.endswith("\n") else "") + block,
                        encoding="utf-8")
    return str(path)


def _relocate_coordination(rec, job_dir, relocated_from):
    """The acting config, rebased onto the bound job.

    Same defect class as the lane config's (Codex review of PR #158,
    2026-09-12): the config names the store it was written in, and the
    server argv names the config, so a resumed worker would be handed a
    path that moved. `record` is the job id and does not move with it.
    """
    coord = (rec.get("brief") or {}).get("coordination")
    if not isinstance(coord, dict) or not coord.get("config"):
        return
    job_dir = Path(job_dir)
    cfg = job_dir / "coordination-mcp.json"
    prior = Path(coord["config"])
    # A whole job directory moved by shutil already carries this file to
    # `cfg`; `prior`, the path the record wrote at dispatch time, is gone.
    # Only a config that reached the new job by some other route is copied
    # here — either way the JSON below is read from `cfg`, never `prior`,
    # so its `worktree` field is rebased whichever way it arrived.
    if not cfg.is_file() and prior != cfg and prior.is_file():
        cfg.write_text(prior.read_text(encoding="utf-8"), encoding="utf-8")
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if (isinstance(data, dict)
                and isinstance(data.get("worktree"), str) and relocated_from):
            data["worktree"] = data["worktree"].replace(
                relocated_from, str(job_dir))
            cfg.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        coord["config"] = str(cfg.resolve())
        coord["sha256"] = _sha(cfg)
    server = coord.get("server")
    if isinstance(server, list) and relocated_from:
        coord["server"] = [
            token.replace(relocated_from, str(job_dir))
            if isinstance(token, str) else token for token in server]


def _relocate_mcp(rec, job_dir, relocated_from):
    """A retained job moved to another dispatch store keeps an MCP config on
    disk that names the old store, so a resumed server reports prior records
    missing while any call still counts as proof (Codex review of PR #158,
    2026-09-12). Rewrite lane-mcp.json, the record's server argv and grok's
    config.toml to the bound job; the record's own paths were rebased by
    _bind_record."""
    _relocate_coordination(rec, job_dir, relocated_from)
    mcp = (rec.get("brief") or {}).get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("config"):
        return
    job_dir = Path(job_dir)
    cfg = job_dir / "lane-mcp.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict):
            data["dispatch_store"] = str(job_dir.resolve().parent)
            data["job_registry"] = str(JOBS_PATH)
            cfg.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        mcp["config"] = str(cfg.resolve())
        mcp["sha256"] = _sha(cfg)
    server = mcp.get("server")
    if isinstance(server, list):
        mcp["server"] = [tok.replace(relocated_from, str(job_dir)) if isinstance(tok, str) else tok
                         for tok in server]
    grok_config = mcp.get("grok_config")
    if isinstance(grok_config, str):
        toml = Path(grok_config.replace(relocated_from, str(job_dir)))
        if toml.is_file():
            toml.write_text(toml.read_text(encoding="utf-8").replace(relocated_from, str(job_dir)),
                            encoding="utf-8")
        mcp["grok_config"] = str(toml)


def _mcp_calls(stream, harness):
    """Count the lane MCP calls a worker made, from its own store; the
    proof at the dispatch boundary (owner, 2026-09-12: "not just proof at
    PR - proof for every dispatch"). Shapes measured on 2026-09-12 from
    live probes: claude `tool_use` named `mcp__<server>__<tool>`; codex
    `event_msg` items of type `McpToolCall` on the server's TOML key; grok
    assistant `tool_calls` whose arguments name `<key>__<tool>` (the
    server name and key are LANE_MCP_SERVER and LANE_MCP_KEY). None when
    the store cannot be read."""
    if not stream:
        return None
    path = Path(stream)
    if path.name == "summary.json":
        path = path.parent / "chat_history.jsonl"
    if not path.is_file():
        return None
    tools = []
    prefix_dash = f"mcp__{LANE_MCP_SERVER}__"
    prefix_us = f"{LANE_MCP_KEY}__"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        if LANE_MCP_KEY not in line and LANE_MCP_SERVER not in line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if harness == "claude":
            content = ((item.get("message") or {}).get("content")
                       if isinstance(item.get("message"), dict) else None)
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get("type") == "tool_use" \
                        and str(part.get("name", "")).startswith(prefix_dash):
                    tools.append(str(part["name"])[len(prefix_dash):])
        elif harness == "codex":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            call = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if call.get("type") == "McpToolCall" and call.get("server") == LANE_MCP_KEY:
                tools.append(str(call.get("tool")))
        elif harness == "grok":
            for call in item.get("tool_calls") if isinstance(item.get("tool_calls"), list) else []:
                args = call.get("arguments") if isinstance(call, dict) else None
                if isinstance(args, str) and prefix_us in args:
                    try:
                        name = json.loads(args).get("tool_name", "")
                    except ValueError:
                        name = ""
                    if str(name).startswith(prefix_us):
                        tools.append(str(name)[len(prefix_us):])
    return tools


def _first_doc_line(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for quote in ('"""', "\'\'\'"):
        start = text.find(quote)
        if 0 <= start < 400:
            body = text[start + 3:]
            line = body.split("\n", 1)[0].strip().strip(quote).strip()
            return line
    return ""


def _what_exists(snapshot, jobs, mcp_meta):
    """A bounded, measured list of what the tree already has; absence declared."""
    root = Path(snapshot)
    lines = [BRIEF_MARK]
    tools = sorted(root.glob("ops/conductor/tools/*.py"))
    lines.append("levers: absent" if not tools else "levers (ops/conductor/tools):")
    for tool in tools[:80]:
        doc = _first_doc_line(tool)
        lines.append(f"  {tool.name} — {doc}" if doc else f"  {tool.name}")
    skills = sorted(p.name for p in root.glob(".claude/skills/*") if p.is_dir())
    lines.append("skills (.claude/skills): " + ", ".join(skills) if skills else "skills: absent")
    conf = root / "ops/lane.conf"
    if conf.is_file():
        keys = [line.split("=", 1)[0] for line in conf.read_text(encoding="utf-8", errors="replace").splitlines()
                if line and not line.startswith("#") and "=" in line]
        lines.append("lane.conf keys: " + ", ".join(keys))
    else:
        lines.append("lane.conf: absent")
    names = [f"{name} ({spec.get('role') or 'direct'})" for name, spec in (jobs or {}).items()
             if isinstance(spec, dict)]
    lines.append("jobs (the catalogue): " + ", ".join(names) if names else "jobs: absent")
    contract = root / "docs" / "tests.md"
    if contract.is_file():
        tail = contract.read_text(encoding="utf-8", errors="replace").split("## Invariants", 1)
        invariants = re.findall(r"^- `([\w.-]+)`", tail[1], re.MULTILINE) if len(tail) > 1 else []
        lines.append("test kit (ops/testkit): World, run, Tools, cases; a tests job's note joins one invariant with "
                     "reused: <name>; invariants: " + (", ".join(invariants) if invariants else "none named in docs/tests.md"))
    else:
        lines.append("test kit: absent")
    ideas = sorted(p.stem for p in root.glob("ops/conductor/ideas/I-*.md"))
    lines.append("ideas (ops/conductor/ideas): " + ", ".join(ideas[-40:]) if ideas else "ideas: absent")
    handoffs = sorted(root.glob("ops/conductor/handoff/HANDOFF-*.md"))
    corrections = []
    if handoffs:
        text = handoffs[-1].read_text(encoding="utf-8", errors="replace")
        marker = "## Corrections to retain"
        if marker in text:
            section = text.split(marker, 1)[1]
            section = section.split("\n## ", 1)[0]
            corrections = [line for line in section.splitlines() if line.strip()]
    if corrections:
        lines.append(f"corrections to retain ({handoffs[-1].name}):")
        lines.extend("  " + line for line in corrections[:30])
    else:
        lines.append("corrections: absent")
    if mcp_meta and mcp_meta.get("server"):
        lines.append(f"lane MCP: server `{LANE_MCP_SERVER}`, tools {', '.join(LANE_MCP_TOOLS)}; "
                     f"records in reach: {', '.join(mcp_meta.get('job_ids') or []) or 'none'}")
    else:
        lines.append(f"lane MCP: absent ({LANE_MCP_ABSENT['unresolved']}); tools would be "
                     + ", ".join(LANE_MCP_TOOLS))
    text = "\n".join(lines)
    if len(text) > BRIEF_CAP:
        text = text[:BRIEF_CAP] + "\n  … (brief cut at the cap; the rest is in the tree)"
    return text + "\n\n" + REUSE_CLAUSE


def _turns_argv(harness, turns):
    """The exact argv tokens that carry a turn cap; [] where the harness
    has no such flag (codex exec, agy: measured 2026-09-12, `--help` names
    none) or no cap is set."""
    if turns is None:
        return []
    if harness in {"claude", "grok"}:
        return ["--max-turns", str(turns)]
    return []


def _turns_note(harness, turns):
    if turns is None or harness in {"claude", "grok"}:
        return None
    return (f"{harness} offers no turn-cap flag (measured 2026-09-12: "
            f"its --help names none); caps.turns is recorded, the wall cap governs")


def _turns_budget(turns):
    """The turn budget as words in the brief. A harness with no turn flag
    (codex, agy) cannot be capped by argv; every worker can be told the
    number and what to do on reaching it. Empty where no cap is declared —
    a budget is never guessed."""
    if turns is None:
        return ""
    return (f"\n\nTurn budget: {turns} turns for this job, and the brief is "
            f"the only place a harness without a turn flag is told.\n"
            f"On reaching it, stop and report status short, naming in note "
            f"what you finished and what is left; do not continue past it.")


def _resolve_turns(job_caps):
    """caps.turns resolves like caps.timeout: DISPATCH_TURNS from the
    environment, else the job's own caps.turns, else none — declared."""
    raw = os.environ.get("DISPATCH_TURNS")
    source = "DISPATCH_TURNS"
    if raw is None:
        raw = (job_caps or {}).get("turns")
        source = "job"
    if raw is None:
        return None, "none"
    try:
        turns = int(raw)
    except (TypeError, ValueError):
        turns = 0
    if isinstance(raw, bool) or turns <= 0:
        _refuse("caps", "a positive integer turn cap", f"{source}={raw!r}",
                "set caps.turns in jobs.json or DISPATCH_TURNS to a positive integer")
    return turns, source


def _argv(name, model, effort, session, prompt, flags, sandbox, resume=False,
          project_allow=(), project_deny=(), writable_out=False, executable=None,
          turns=None, mcp=None, coord=None):
    if mcp and name == "claude":
        project_allow = (*project_allow, f"mcp__{LANE_MCP_SERVER}")
    argv = _argv_base(name, model, effort, session, prompt, flags, sandbox, resume,
                      project_allow, project_deny, writable_out, executable)
    extra = _mcp_argv(name, mcp, Path(prompt).parent, coord)
    if name == "codex" and argv and argv[-1] == "-":
        argv = [*argv[:-1], *extra, "-"]
    else:
        argv = [*argv, *extra]
    return [*argv, *_turns_argv(name, turns)]


def _argv_base(name, model, effort, session, prompt, flags, sandbox, resume=False,
               project_allow=(), project_deny=(), writable_out=False, executable=None):
    if executable is None:
        executable = shutil.which(name) or name
    out = str((Path(prompt).parent / "out").resolve())
    last_message = str((Path(out) / "last-message.json").resolve())
    if name == "agy":
        # 20260908T073056Z-code-grok-0e4417 (stdin) and
        # 20260908T074530Z-code-grok-5395b3 (permissions/cwd).
        argv = [executable, *flags, "--print=", "--model", model,
                "--input-format", "stream-json", "--output-format", "stream-json",
                "--disable-slash-commands", "--dangerously-skip-permissions",
                "--add-dir", str((Path(prompt).parent / "snapshot").resolve()),
                *_effort_argv(name, effort)]
        if resume:
            argv += ["--conversation", session]
        return argv
    if resume:
        if name == "codex":
            argv = [executable, "exec", "resume", session, "--model", model,
                    *_effort_argv(name, effort), "--json", "-o", last_message]
            if writable_out:
                argv += ["-c", f'sandbox_workspace_write.writable_roots=["{out}"]']
            return argv
        if name == "claude":
            incoming = str((Path(prompt).parent / "in").resolve())
            context = str((Path(prompt).parent / "context").resolve())
            return [executable, *flags, "-r", session, "--print",
                    "--permission-mode", sandbox, "--add-dir", out,
                    "--add-dir", incoming, "--add-dir", context,
                    "--allowedTools", *CLAUDE_TOOL_RULES, *project_allow,
                    "--disallowedTools", *CLAUDE_DENIED_TOOLS, *project_deny,
                    "--model", model, *_effort_argv(name, effort),
                    "--output-format", "json",
                    "--json-schema", ENVELOPE_SCHEMA_JSON]
        # grok resume must carry the same flags as a fresh launch, or B1
        # returns on every resumed dispatch (review ffaf14).
        argv = [executable, *flags, "-r", session, "--model", model,
                "--output-format", "plain"]
        argv += _grok_permission(sandbox)
        argv += _effort_argv(name, effort)
        argv += ["--prompt-file", str(prompt)]
        return argv
    if name == "claude":
        incoming = str((Path(prompt).parent / "in").resolve())
        context = str((Path(prompt).parent / "context").resolve())
        return [executable, *flags, "--session-id", session, "--print",
                "--permission-mode", sandbox, "--add-dir", out,
                "--add-dir", incoming, "--add-dir", context,
                "--allowedTools", *CLAUDE_TOOL_RULES, *project_allow,
                "--disallowedTools", *CLAUDE_DENIED_TOOLS, *project_deny,
                "--model", model, *_effort_argv(name, effort),
                "--output-format", "json",
                "--json-schema", ENVELOPE_SCHEMA_JSON]
    if name == "codex":
        # codex exec takes the brief on stdin; "-" says so explicitly. With
        # DEVNULL it exits 1 "No prompt provided via stdin" before any work.
        # Check-tests uses workspace-write for out/; mode hardening plus the
        # read-role settlement checks require its snapshot to stay unchanged.
        argv = [executable, "exec", "--sandbox", sandbox]
        if sandbox == "workspace-write" or writable_out:
            argv += ["-c", f'sandbox_workspace_write.writable_roots=["{out}"]']
        return [*argv, "--model", model, *_effort_argv(name, effort),
                "--json", "-o", last_message, "-"]
    # grok: the record states `sandbox`; the argv must carry it, or the
    # record claims a permission mode the child never had. plain output
    # keeps stdout parseable as the envelope. `always-approve` is grok's
    # own flag, not a --permission-mode value: under `auto` a session
    # raised 111 permission prompts and the last one timed out after
    # 30 s on a non-interactive stdin, ending the turn with no envelope
    # (2026-08-29, record 20260829T170852Z-tests-grok-abfc3b).
    # grok 1.0.5 with `--json-schema` answers after ONE model call with a
    # schema-valid but empty envelope and ends the turn (measured
    # 2026-08-29, record 20260829T234838Z-review-grok-66ccb2: 213 output
    # tokens, "note": "starting: reading briefs and review contract",
    # zero findings, $0.0067) — the structured-output mode short-circuits
    # the agentic loop. Until the probe matrix finds a grok mode that both
    # works and yields the object, grok keeps plain output and the scan.
    argv = [executable, *flags, "-s", session, "--model", model,
            "--output-format", "plain"]
    argv += _grok_permission(sandbox)
    argv += _effort_argv(name, effort)
    argv += ["--prompt-file", str(prompt)]
    return argv


def _child_env(base, additions, job_id, session=None):
    """The environment a dispatched child runs under.

    `session` is the uuid this dispatch minted. It goes in as
    CLAUDE_SESSION_ID, the key instrument.session() already admits, so a
    worker of any harness can resolve the participant the launcher bound
    for it. A claude child also gets it on its argv as --session-id; the
    two are the same value on purpose, because a worker acting under one
    id and recorded under the other would leave the store pointing at a
    session that never ran.
    """
    env = dict(base)
    for key in ("CLICOLOR_FORCE", "FORCE_COLOR", "CLAUDE_CONFIG_DIR"):
        env.pop(key, None)
    env.update({"NO_COLOR": "1", "CLICOLOR": "0", "TERM": "dumb",
                "PAGER": "cat", "GH_PAGER": "cat", "GIT_PAGER": "cat",
                "LESS": "FRX", "CI": "true", "GIT_TERMINAL_PROMPT": "0",
                "GIT_EDITOR": "true", "EDITOR": "true", "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8", "LC_ALL": "C.UTF-8",
                "WF_LANE": "dev", "DISPATCH_JOB": job_id})
    env.update(additions)
    # The child is initialized by dispatch, separately from interactive
    # terminal registration. No parent/native alias may select its actor.
    for key in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COORDINATION_CAPABILITY"):
        env.pop(key, None)
    # A dispatch that minted no session id leaves the key absent rather
    # than setting it empty: the server refuses class `session` on unset,
    # and an empty string would be a caller naming a session that is not
    # one.
    if session:
        env["CLAUDE_SESSION_ID"] = session
    else:
        env.pop("CLAUDE_SESSION_ID", None)
    return env


def _binding_session(rec):
    """Lane identity survives native conversation discovery and resume.

    Old bound records retained the issuer's exact receipt even when their
    session.id was replaced. Recover that declared identity, never guess
    from a native rollout or another participant with the same model.
    """
    saved = rec.get("session") or {}
    binding = saved.get("binding_id")
    if binding:
        if not isinstance(binding, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", binding):
            _refuse("binding", "a valid recorded lane session id", "invalid binding_id",
                    "restore the original dispatch binding")
        return binding
    receipt = saved.get("bound")
    if receipt:
        match = re.fullmatch(
            r"participant PT-[1-9][0-9]* bound for session ([A-Za-z0-9][A-Za-z0-9._-]{0,127}) "
            r"by PT-[1-9][0-9]* for A-[1-9][0-9]*", str(receipt).strip())
        if not match:
            _refuse("binding", "the original bind-for receipt for this retained job",
                    "an unreadable receipt", "restore the issued binding before resuming")
        return match.group(1)
    return saved.get("id")


def _bound_participant(line):
    """The participant id out of bind-for's line, or None.

    The lever's line is prose around the id; the acting config takes the
    id alone, and the server refuses class `config` on anything that is
    not PT-n, so a line this cannot read is a wiring fault and not a
    participant.
    """
    found = re.search(r"\bPT-[1-9][0-9]*\b", line or "")
    return found.group(0) if found else None


def _bind_worker(session, assignment, harness, agent, *, capability_digest):
    """Mint the worker's participant before the worker exists.

    A dispatched worker has no harness session id of its own, so without
    this it holds no participant and every coordination mutation it makes
    refuses class `participant`. `bind-for` is the only verb that mints one
    for a session that cannot bind itself, and it records who issued it and
    for which assignment, so a participant nobody dispatched is visible as
    such.

    It runs before the child starts, never after: a bind that failed while
    the worker ran would leave exactly the unbound worker this exists to
    prevent. A refusal from the core is handed on with its own class and
    its own three parts, because a launcher that renamed a conductor
    refusal would make one fault read as two different things depending on
    which surface met it.
    """
    env = dict(os.environ)
    env["WF_AGENT"] = agent
    # The lever runs as the dispatcher, which is this process's own
    # session, so its session id is inherited and never set here. The
    # worker's id is the --session argument: it is the subject of the verb,
    # not the caller. Setting it in the environment made the lever resolve
    # the caller as the worker's own unbound session and refuse class
    # `participant` naming the uuid it had just minted.
    try:
        done = subprocess.run(
            [sys.executable, str(COORDINATION), "bind-for",
             "--session", session, "--roles", "worker",
             "--for", assignment, "--harness", harness,
             "--capability-digest", capability_digest],
            capture_output=True, text=True, timeout=BIND_TIMEOUT,
            cwd=str(COORDINATION.parents[3]), env=env, check=False)
    except subprocess.TimeoutExpired:
        _refuse("binding", f"the coordination lever to answer within "
                           f"{BIND_TIMEOUT}s",
                "no answer at all",
                "the lane's state store is held by another writer; retry once "
                "it is free")
    if done.returncode == 0:
        return (done.stdout or "").strip()
    line = (done.stderr or "").strip().splitlines()
    wire = line[-1] if line else ""
    match = re.match(r"(\w[\w-]*): expected (.*); found (.*); needed (.*)",
                     wire)
    if match:
        # The core's own class and its own three parts, unrenamed.
        _refuse(match.group(1), match.group(2), match.group(3), match.group(4))
    _refuse("binding",
            f"a wire refusal from {COORDINATION.name} bind-for, or exit 0",
            f"exit {done.returncode} saying {wire!r}" if wire
            else f"exit {done.returncode} and nothing on stderr",
            "run the lever by hand with the same arguments and read what it "
            "says; a worker is never launched unbound")


def _attempt_state(job_dir, *, required=False):
    """Read the attempt boundary; old states without an offset start at zero."""
    path = job_dir / "state.json"
    state = json.loads(path.read_text()) if required or path.exists() else {}
    if not isinstance(state, dict):
        raise ValueError("state.json must be an object")
    offset = state.get("raw_offset", 0)
    raw = job_dir / "raw.out"
    size = raw.stat().st_size if raw.exists() else 0
    if type(offset) is not int or not 0 <= offset <= size:
        raise ValueError("raw_offset must be a nonnegative integer within raw.out")
    return state


def _agy_stream(job_dir, session, snapshot):
    """Bind native stdout init to this attempt; never choose newest brain."""
    raw = job_dir / "raw.out"
    if not raw.is_file():
        if session and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", session):
            path = (job_dir / "home/agy/.gemini/antigravity-cli/brain" /
                    session / ".system_generated/logs/transcript.jsonl")
            if path.is_file() and _inside(path.resolve(), job_dir.resolve()):
                return path, None, session, "agy init.cwd unavailable"
        return None, None, None, "agy native stdout unavailable"
    try:
        offset = _attempt_state(job_dir).get("raw_offset", 0)
    except (OSError, ValueError, TypeError) as exc:
        return raw, None, None, (f"agy state: expected valid attempt state; found {exc}; "
                                 "needed repair state.json from retained attempt evidence")
    try:
        usage = _module("agy_usage", TELEMETRY_APP / "usage.py")
        observed = usage.agy_read(raw, offset)
        sid = observed["session"]
        if not sid:
            return None, None, None, "agy native session unavailable"
        if str(uuid.UUID(sid)) != sid:
            raise ValueError("malformed native conversation id")
        issues = [issue for issue in observed["issues"]
                  if issue not in {"terminal usage unavailable", "truncated native frame"}]
        inits = [f for f in observed["frames"] if f["event"] == "init"]
        if len(inits) != 1:
            issues.append("expected exactly one native init for this attempt")
        elif inits[0]["init"].get("permission_mode") != "always-proceed":
            issues.append("permission mismatch: expected native always-proceed")
        for frame in observed["frames"]:
            if frame["event"] == "result" and frame["result"].get("status") != "SUCCESS":
                issues.append(f"vendor status: {frame['result'].get('status')}")
        if session and sid != session:
            issues.append(f"session mismatch: expected {session}, found {sid}")
        if observed["cwd"] != str(snapshot.resolve()):
            issues.append(f"cwd mismatch: expected {snapshot.resolve()}, found {observed['cwd']}")
    except (OSError, ValueError, TypeError) as exc:
        return None, None, None, f"agy stream parse: {exc}"
    try:
        transcript = (job_dir / "home/agy/.gemini/antigravity-cli/brain" /
                      sid / ".system_generated/logs/transcript.jsonl")
        if transcript.exists():
            if not _inside(transcript.resolve(), job_dir.resolve()):
                issues.append("native transcript outside job")
            else:
                lines = transcript.read_bytes().splitlines(keepends=True)
                while lines and not lines[-1].strip():
                    lines.pop()
                for index, line in enumerate(lines):
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if index == len(lines) - 1 and not line.endswith(b"\n"):
                            break  # The native writer has not finished this row.
                        raise
                    if not isinstance(row, dict) or not {"source", "type"} <= row.keys():
                        issues.append("malformed native transcript")
                        break
                    # Native smoke 20260908T083944Z-review-agy-e49b81:
                    # planner thinking/tool_calls can omit content entirely.
                    planner = (row["source"] == "MODEL"
                               and row["type"] == "PLANNER_RESPONSE"
                               and isinstance(row.get("thinking"), str)
                               and isinstance(row.get("tool_calls"), list))
                    if "content" not in row and not planner:
                        issues.append("malformed native transcript")
                        break
    except (OSError, ValueError, TypeError) as exc:
        issues.append(f"malformed native transcript: {exc}")
    # stdout carries native init.model/cwd and usage; brain lacks them.
    return raw, observed["model"], sid, "; ".join(issues) or None


def _record_stream_model(rec, ran, stream):
    """Keep agy's historical measurement distinct from this attempt's reading."""
    if rec["harness"]["name"] == "agy":
        rec["model"]["current_attempt"] = {
            "ran": ran, "read_from": str(stream) if ran is not None else None,
            "unresolved": None if ran is not None else "native model unavailable in current attempt",
        }
        if ran is None:
            return
    if stream:
        rec["model"].update(ran=ran, read_from=str(stream))
        rec["model"].pop("note", None)


def _stream(job_dir, harness, session, snapshot, ref_sha, role="read"):
    if harness == "agy":
        return _agy_stream(job_dir, session, snapshot)
    if harness == "codex":
        paths = list((job_dir / "home/codex/sessions").rglob("rollout-*.jsonl"))
    elif harness == "grok":
        paths = list((job_dir / "home/grok/sessions").rglob("summary.json"))
    else:
        slug = str(snapshot).replace("/", "-").replace(".", "-")
        paths = list((Path(os.environ["HOME"]) / ".claude/projects" / slug).glob("*.jsonl"))
    if not paths:
        return None, None, None, None
    path = max(paths, key=lambda p: p.stat().st_mtime)
    ran = None
    stream_id = None
    mismatch = None
    try:
        if path.name == "summary.json":
            data = json.loads(path.read_text())
            ran = data.get("current_model_id")
            stream_id = (data.get("info") or {}).get("id")
            reported_head = data.get("head_commit")
            expected_heads = [ref_sha]
            if role == "write":
                local_head = _git(
                    snapshot, "rev-parse", "--verify", "HEAD^{commit}",
                    check=False,
                )
                if (local_head.returncode
                        or _git(snapshot, "merge-base", "--is-ancestor",
                                ref_sha, local_head.stdout.strip(),
                                check=False).returncode):
                    mismatch = (
                        f"head_commit unverified against expected ref {ref_sha}"
                    )
                else:
                    expected_heads.append(local_head.stdout.strip())
            if (reported_head and mismatch is None
                    and reported_head not in expected_heads):
                mismatch = (
                    f"head_commit mismatch: expected {' or '.join(expected_heads)}, "
                    f"found {reported_head}; dispatched ref {ref_sha}"
                )
        else:
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row.get("type") == "assistant":
                    ran = (row.get("message") or {}).get("model") or ran
                    stream_id = row.get("sessionId") or stream_id
                payload = row.get("payload") or {}
                if row.get("type") == "session_meta":
                    stream_id = payload.get("id")
                if row.get("type") == "turn_context":
                    ran = payload.get("model")
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        mismatch = f"stream parse: {exc}"
    if stream_id and harness != "codex" and stream_id != session:
        mismatch = f"session mismatch: minted {session}, found {stream_id}"
    return path, ran, stream_id, mismatch


ENVELOPE_KEYS = ENVELOPE.FIELDS


def _invalid(job, ref_sha, note):
    return ENVELOPE.invalid(job, note, stamp={"ref": ref_sha})


def _invalidate_envelope(data, note):
    data["status"] = "invalid"
    data["verdict"] = None
    prior = data.get("note")
    data["note"] = f"{prior}; {note}" if prior else note
    return data


def _retain_findings(data, prior):
    """A resumed result may add evidence, but cannot erase earlier findings."""
    prior_findings = prior.get("findings")
    prior_findings = prior_findings if isinstance(prior_findings, list) else []
    resumed = data.get("findings")
    resumed = resumed if isinstance(resumed, list) else []
    data["findings"] = prior_findings + [
        item for item in resumed if item not in prior_findings
    ]
    counts = data.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    prior_counts = prior.get("counts")
    prior_counts = prior_counts if isinstance(prior_counts, dict) else {}

    def count(value):
        return value if type(value) is int and value >= 0 else 0

    for severity in ("p1", "p2", "p3"):
        found = sum(
            item.get("severity") == severity
            for item in data["findings"] if isinstance(item, dict)
        )
        counts[severity] = max(
            count(counts.get(severity)), count(prior_counts.get(severity)), found,
        )
    counts["opinions"] = max(
        count(counts.get("opinions")), count(prior_counts.get("opinions")),
    )
    data["counts"] = counts
    if (data.get("status") == "ok" and data.get("verdict") == "approve"
            and any(counts[severity] for severity in ("p1", "p2", "p3"))):
        data["verdict"] = "changes"
    return data


def _json_objects(text):
    decoder = json.JSONDecoder()
    found = []
    for match in re.finditer(r"\{", text):
        try:
            candidate, _end = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            found.append(candidate)
    return found


def _most_envelope_shaped(objects, *, last=False):
    if not objects:
        return None
    score = max(sum(key in item for key in ENVELOPE_KEYS) for item in objects)
    matches = [item for item in objects
               if sum(key in item for key in ENVELOPE_KEYS) == score]
    return matches[-1] if last else matches[0]


def _wrapper_spend(wrapper):
    usage = wrapper.get("usage")
    if not isinstance(usage, dict):
        return None
    cached = (usage.get("cache_read_input_tokens") or 0) + (
        usage.get("cache_creation_input_tokens") or 0)
    spend = {
        "input": usage.get("input_tokens"),
        "cached": cached or None,
        "output": usage.get("output_tokens"),
        "source": "result.usage",
    }
    if wrapper.get("total_cost_usd") is not None:
        spend["cost_usd"] = wrapper["total_cost_usd"]
    spend = {key: value for key, value in spend.items() if value is not None}
    if not any(value for key, value in spend.items() if key != "source"):
        return None
    spend["total"] = sum(spend.get(key, 0) for key in ("input", "cached", "output"))
    return spend


def _merge_spend(prior, wrapper):
    if not wrapper:
        return prior
    merged = dict(prior) if isinstance(prior, dict) and "unresolved" not in prior else {}
    merged.update(wrapper)
    merged["total"] = sum(merged.get(key, 0) for key in ("input", "cached", "output"))
    return merged


def _agy_envelope(raw):
    """Read one published stream-json result, never scan nested objects.

    Carrier fields follow the supplied agy wire evidence
    20260908T064029Z-plan-codex-161211. Native observation
    20260908T073056Z-code-grok-0e4417 also establishes status CANCELED.
    Only SUCCESS certifies completion; explicit result errors refuse, while
    step/tool errors and denied_actions do not imply terminal failure.
    """
    results = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        frame = json.loads(line)
        if not isinstance(frame, dict):
            raise ValueError("malformed stream frame: expected an object")
        if frame.get("event") == "result":
            results.append(frame.get("result"))
    if not results:
        raise ValueError("missing terminal result event")
    if len(results) != 1:
        raise ValueError("ambiguous terminal result: multiple result events")
    result = results[0]
    if not isinstance(result, dict):
        raise ValueError("malformed terminal result: expected an object")
    for field, kinds in (
        ("conversation_id", (str,)), ("status", (str,)),
        ("response", (str,)), ("duration_seconds", (int, float)),
        ("num_turns", (int, float)), ("usage", (dict,)),
    ):
        if field not in result:
            raise ValueError(f"terminal result missing {field}")
        if (not isinstance(result[field], kinds)
                or isinstance(result[field], bool)):
            raise ValueError(f"malformed terminal result {field}")
    if not result["status"].strip():
        raise ValueError("malformed terminal result status: empty string")
    if "error" in result:
        if not isinstance(result["error"], str):
            raise ValueError("malformed terminal result error")
        if result["error"]:
            raise ValueError(f"vendor error: {result['error']}")
    if result["status"] != "SUCCESS":
        raise ValueError(f"vendor status: {result['status']}")
    if "structured_output" in result:
        data = result["structured_output"]
        if not isinstance(data, dict):
            raise ValueError("malformed terminal result structured_output")
    else:
        data = json.loads(result["response"])
    return data


def _envelope(raw, job, ref_sha, note=None, *, harness=None, job_dir=None,
              rec=None):
    """The last JSON object on stdout, or an invalid envelope that says why.

    Narration before or after the object is tolerated (a harness talks
    while it works); a missing key is a refusal, not a default; a `stamp`
    that is not an object is replaced rather than mislabeled as a model id
    (codex once returned the SHA as a string).
    """
    text = raw.decode("utf-8", "replace")
    objects = [] if harness == "agy" else _json_objects(text)
    data = None
    cause = None
    if harness == "agy":
        try:
            data = _agy_envelope(raw)
        except ValueError as exc:
            cause = f"agy-result: {exc}"
    elif harness == "claude" and objects:
        wrapper = next((item for item in objects if item.get("type") == "result"), None)
        if wrapper is not None:
            if rec is not None:
                spend = _wrapper_spend(wrapper)
                if spend:
                    rec["session"]["spend"] = _merge_spend(
                        rec["session"].get("spend"), spend)
            if wrapper.get("is_error"):
                cause = f"claude-result:{wrapper.get('subtype') or 'unknown'}"
            elif isinstance(wrapper.get("structured_output"), dict):
                data = wrapper["structured_output"]
            elif isinstance(wrapper.get("result"), str):
                data = _most_envelope_shaped(_json_objects(wrapper["result"]),
                                             last=True)
            if data is None and cause is None:
                cause = f"claude-result:{wrapper.get('subtype') or 'unknown'}"
    elif harness == "codex":
        last = Path(job_dir) / "out" / "last-message.json" if job_dir else None
        if last is not None and last.is_file():
            try:
                candidate = json.loads(last.read_text(encoding="utf-8"))
                data = candidate if isinstance(candidate, dict) else None
            except (OSError, json.JSONDecodeError):
                data = None
        if data is None:
            for item in objects:
                payload = item.get("item")
                if isinstance(payload, dict) and payload.get("type") == "agent_message":
                    try:
                        candidate = json.loads(payload.get("text", ""))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        data = candidate
        if data is None:
            data = next((item for item in reversed(objects)
                         if ENVELOPE.schema_valid(item)), None)
        if data is None:
            cause = "no-last-message"
    elif harness == "grok" and objects:
        # JSON output is the final top-level object; an earlier object can be
        # narration or a diagnostic and is not the requested result.
        data = next((item for item in reversed(objects)
                     if ENVELOPE.schema_valid(item)), None)
        if data is None:
            data = _most_envelope_shaped(objects, last=True)
    if data is None and cause is None:
        # Compatibility for pre-U10 raw.out: choose the most envelope-shaped
        # complete object, preserving the first on ties.
        data = _most_envelope_shaped(objects)
    if data is None:
        if cause is None:
            cause = "envelope-parse"
        refusal = (f"{cause}: no structured envelope"
                   if cause != "envelope-parse"
                   else "envelope-parse: no JSON object on stdout")
        data = _invalid(job, ref_sha, refusal)
    else:
        try:
            ENVELOPE.validate(data)
        except ENVELOPE.EnvelopeError as exc:
            cause = "envelope-invalid" if ENVELOPE.schema_valid(data) else "schema-invalid"
            refusal = f"{cause}: {exc}"
            # Retain the worker's evidence and claimed tally, never repair it
            # into an approval. All carriers share this acceptance boundary.
            data = (_invalidate_envelope(dict(data), refusal)
                    if isinstance(data, dict) else _invalid(job, ref_sha, refusal))
    stamp = data.get("stamp")
    if not isinstance(stamp, dict):
        data["stamp"] = {}
    data["stamp"]["ref"] = ref_sha
    if note:
        _invalidate_envelope(data, note)
    if rec is not None and cause:
        rec["_envelope_cause"] = cause
    return data


def _write_record(repo, rec):
    rec.pop("_envelope_cause", None)
    rec.pop("_relocated_from", None)
    job_dir, _snapshot_path = _bound_snapshot(rec)
    path = repo / ".dev/records/dispatches" / f"{rec['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record.validate(rec)
    record.validate_context(
        rec, expected_id=rec["id"], expected_snapshot=_snapshot_path,
    )
    body = json.dumps(_portable_paths(rec), indent=2) + "\n"
    path.write_text(body, encoding="utf-8")
    job_path = job_dir / "record.json"
    if job_path.parent.is_dir():
        job_path.write_text(body, encoding="utf-8")
    return path


def _commit_record(repo, path, rec, subject=None):
    rel = path.relative_to(repo)
    owner_name, owner_email = _lane_author()
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME=owner_name, GIT_AUTHOR_EMAIL=owner_email,
               GIT_COMMITTER_NAME=owner_name, GIT_COMMITTER_EMAIL=owner_email)
    msg = (f"{subject or f'dispatch: record {rec['id']}'}\n\n"
           "Source: generated: ops/devlane/dispatch/launch.py\n"
           f"Co-Authored-By: {rec['dispatched_by']}")
    for item in rec.get("overrides", []):
        msg += f"\n\nOverride: {item['refusal']}: {item['reason']}"
    _git(repo, "add", "--intent-to-add", "--", str(rel), env=env)
    _git(repo, "commit", "--only", "-m", msg, "--", str(rel), env=env)


def _requested_timeout():
    """DISPATCH_TIMEOUT from the invoking environment, in seconds, or None
    when it is unset. Read before anything is minted or spawned, so a value
    the launcher cannot use is refused on the wire rather than raised after
    the harness is running (20260923T000804Z-review-codex-da66dd#1). NaN,
    an infinity, zero and a negative parse as floats and bound nothing; NaN
    also serialises as a bare token strict JSON rejects (#2)."""
    raw = os.environ.get("DISPATCH_TIMEOUT")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or value <= 0:
        _refuse("timeout", "DISPATCH_TIMEOUT as a positive finite number of seconds",
                repr(raw), "unset DISPATCH_TIMEOUT or give it a number of seconds")
    return value


def _run_attempt(rec, job_dir, *, job_caps=None, resume=False, reason=None):
    harness = rec["harness"]["name"]
    bound_job_dir, snapshot = _bound_snapshot(rec)
    if Path(job_dir).resolve(strict=False) != bound_job_dir:
        _refuse("record-target", f"the canonical job directory {bound_job_dir}",
                str(Path(job_dir).resolve(strict=False)), "use the record's dispatch id")
    job_dir = bound_job_dir
    _verify_graft(rec, job_dir, snapshot)
    _verify_record_ref(snapshot, rec["snapshot"]["ref_sha"])
    prompt = job_dir / "prompt.txt"
    relocated_from = rec.pop("_relocated_from", None)
    if relocated_from:
        prompt_text = prompt.read_text(encoding="utf-8").replace(
            relocated_from, str(job_dir))
        prompt.write_text(prompt_text, encoding="utf-8")
        rec["brief"].update(sha256=_sha(prompt), bytes=prompt.stat().st_size)
        _relocate_mcp(rec, job_dir, relocated_from)
    iso_env = rec["harness"]["isolation"].get("env", {})
    flags = rec["harness"]["isolation"].get("flags", [])
    project_allow = project_deny = ()
    if harness == "claude":
        permissions, project_allow, project_deny = _project_permissions(snapshot)
        rec["harness"]["permissions"] = permissions
    if resume and harness == "agy":
        rel = ".gemini/antigravity-cli/brain"
        live = job_dir / "home/agy" / rel
        archived = job_dir / "home/agy-stream" / rel
        if not live.exists() and archived.exists():
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archived), str(live))
    if resume and harness in {"codex", "grok"}:
        live = job_dir / "home" / harness / "sessions"
        streamed = job_dir / "home" / f"{harness}-stream" / "sessions"
        if not live.exists() and streamed.exists():
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(streamed), str(live))
        elif not live.exists():
            _refuse(
                "resume-store",
                f"the session store for {rec['session']['id']} under "
                f"{live} or {streamed}",
                "neither",
                "resume a job whose store survived, or launch afresh",
            )
    argv = _argv(harness, rec["model"]["requested"], rec["model"]["effort_requested"],
                 rec["session"]["id"], prompt, flags, rec["harness"]["sandbox"],
                 resume, project_allow, project_deny,
                 writable_out=rec["role"] == "read",
                 executable=rec["harness"].get("worker", {}).get("executable"),
                 turns=(rec.get("caps") or {}).get("turns"),
                 # The run path rebuilds the argv from the record and
                 # overwrites what record.build put there, so a server named
                 # only at build time never reaches the child. Both planes
                 # are taken from the record here for that reason.
                 mcp=((rec.get("brief") or {}).get("mcp") or {}).get("server"),
                 coord=((rec.get("brief") or {}).get("coordination")
                        or {}).get("server"))
    rec["harness"]["argv"] = argv
    rec["model"]["effort_argv"] = _effort_argv(
        harness, rec["model"]["effort_requested"])
    if harness == "codex":
        (job_dir / "out" / "last-message.json").unlink(missing_ok=True)
    env = _child_env(os.environ, iso_env, rec["id"],
                     session=_binding_session(rec))
    if harness == "agy":
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
                    "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
            Path(env[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
    if harness == "codex" and rec["role"] == "read":
        # LD-14: every Codex read-role job, not only check-tests, runs under
        # OS containment with a writable out/tmp; the read-only sandbox
        # label blocked TMPDIR so no audit could execute a suite.
        for root, dirs, files in os.walk(snapshot):
            for name in [*dirs, *files]:
                path = Path(root) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
        snapshot.chmod(snapshot.stat().st_mode & ~0o222)
        tmpdir = job_dir / "out" / "tmp"
        if tmpdir.is_symlink():
            _refuse("tmpdir-symlink", f"a real directory at {tmpdir}",
                    "a symlink", "remove the symlink and resume the job")
        try:
            tmpdir.mkdir(exist_ok=True)
        except OSError as exc:
            _refuse("tmpdir", f"a real directory at {tmpdir}", str(exc),
                    "remove the non-directory entry and resume the job")
        if not tmpdir.is_dir():
            _refuse("tmpdir", f"a real directory at {tmpdir}",
                    "a non-directory entry",
                    "remove the non-directory entry and resume the job")
        env["TMPDIR"] = str(tmpdir.absolute())
    # A-218: install the same policy projection in the existing worker home.
    # This is worker initialization, never interactive registration or dispatch.
    isolation = _module("guard_isolation", HARNESS_APP / "isolation.py")
    try:
        profile, binding = isolation.install_enforcement(
            harness, job_dir / "enforcement", source=APP.parents[2], cwd=snapshot,
            home=rec["harness"]["isolation"].get("home"),
            session=_binding_session(rec), agent=rec.get("agent") or env.get("WF_AGENT"),
            executable=argv[0], servers=[LANE_MCP_SERVER, LANE_MCP_KEY, COORD_MCP_SERVER],
            relocated_from=relocated_from)
        argv, env = profile.native_launch(binding, argv, env)
        # Capture this attempt's directories and checked custody bytes before
        # the worker; resume captures its current (possibly relocated) home.
        custody = profile.capture_custody(binding)
    except (isolation.NotIsolated, OSError, ValueError, TypeError, KeyError) as exc:
        _refuse("enforcement", "a pinned protected worker hook profile", str(exc),
                "inspect co_doc.py and the native capability evidence before resuming")
    rec["harness"]["argv"] = argv
    requested = _requested_timeout()
    for marker in ("exit", "TRIPPED.md"):
        (job_dir / marker).unlink(missing_ok=True)
    launched = _now()
    raw_mode = "ab" if resume else "wb"
    # codex exec and claude --print read the brief from stdin ("Input must
    # be provided either through stdin or as a prompt argument"); grok names
    # the prompt file on argv and gets DEVNULL so nothing waits on a tty.
    feed = prompt.open("rb") if harness in ("codex", "claude") else None
    if harness == "agy":
        # A regular file avoids a blocking pipe write ahead of supervision.
        turn = job_dir / "stdin.ndjson"
        turn.write_text(json.dumps({"event": "user", "message": {
            "content": prompt.read_text(encoding="utf-8")}}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        feed = turn.open("rb")
    raw_offset = (job_dir / "raw.out").stat().st_size if resume and (job_dir / "raw.out").exists() else 0
    with (job_dir / "raw.out").open(raw_mode) as raw, (job_dir / "stderr").open(raw_mode) as err:
        proc = subprocess.Popen(argv, cwd=snapshot, env=env,
                                stdin=feed if feed is not None else subprocess.DEVNULL,
                                stdout=raw, stderr=err, start_new_session=True)
        if feed is not None:
            feed.close()
        state = {"pid": proc.pid, "pgid": proc.pid, "session": {"id": rec["session"]["id"]},
                 "stream": rec["session"].get("stream"), "attempt": len(rec["attempts"]) + 1,
                 "raw_offset": raw_offset}
        (job_dir / "state.json").write_text(json.dumps(state) + "\n")
        job_timeout = (job_caps or {}).get("timeout")
        wall_default = _lane_values("WALL_DEFAULT")["WALL_DEFAULT"]
        # The job's cap bounds the environment (CONTRACT.md, the Lead's
        # ruling 2026-09-22): DISPATCH_TIMEOUT may lower a cap, never raise
        # it, and what it asked for is recorded beside what was applied.
        if requested is not None and (job_timeout is None or requested <= float(job_timeout)):
            timeout, source = requested, "DISPATCH_TIMEOUT"
        elif job_timeout is not None:
            timeout, source = float(job_timeout), "job"
        else:
            timeout, source = float(wall_default), "default"
        rec["caps"]["timeout"] = timeout
        rec["caps"]["timeout_source"] = source
        rec["caps"]["timeout_requested"] = requested
        grace = float(os.environ.get("DISPATCH_STREAM_GRACE", "120"))
        started_clock = monotonic()
        runtime_note = None
        last_read = -TICK
        stream = None
        while proc.poll() is None:
            elapsed = monotonic() - started_clock
            if elapsed - last_read >= TICK:
                last_read = elapsed
                stream, _ran, _sid, _mis = _stream(
                    job_dir, harness, rec["session"]["id"], snapshot,
                    rec["snapshot"]["ref_sha"], rec["role"],
                )
                over = False
                if harness == "agy" and _sid and not _mis:
                    rec["session"]["id"] = _sid
                    state["session"]["id"] = _sid
                    state["stream"] = str(stream)
                    (job_dir / "state.json").write_text(json.dumps(state) + "\n")
                if harness == "agy" and _mis and stream:
                    runtime_note = f"unsupervised: {_mis}"
                    break
                if stream and rec["caps"].get("cap-out") is not None:
                    if harness == "agy":
                        usage = _module("agy_live_usage", TELEMETRY_APP / "usage.py")
                        reading = usage.agy_read(stream, raw_offset)
                        output = reading["live_output"]
                        over = output is not None and output > int(rec["caps"]["cap-out"])
                        if output is None and elapsed >= grace:
                            runtime_note = "unsupervised: agy output usage unavailable within grace"
                            break
                    else:
                        with contextlib.suppress(OSError):
                            over = any(int(n) > int(rec["caps"]["cap-out"])
                                       for n in re.findall(r'"output_tokens"\s*:\s*(\d+)', stream.read_text()))
                if over:
                    runtime_note = "trip: output cap exceeded"
                    break
            if elapsed >= timeout:
                runtime_note = "timeout: harness exceeded runtime"
                break
            if not stream and elapsed >= grace:
                runtime_note = "unsupervised: no session stream within grace"
                break
            sleep(min(0.05, max(0.0, min(timeout, grace) - elapsed)))
        if harness == "agy" and not runtime_note and rec["caps"].get("cap-out") is not None:
            usage = _module("agy_final_usage", TELEMETRY_APP / "usage.py")
            output = usage.agy_read(job_dir / "raw.out", raw_offset)["live_output"]
            if output is not None and output > int(rec["caps"]["cap-out"]):
                runtime_note = "trip: output cap exceeded"
        if runtime_note:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = 137
            (job_dir / "TRIPPED.md").write_text(runtime_note + "\n")
            rec["_runtime_note"] = runtime_note
        else:
            code = proc.returncode
    (job_dir / "exit").write_text(f"{code}\n")
    rec["attempts"].append({"n": len(rec["attempts"]) + 1, "launched": launched,
                            "ended": _now(), "exit": code,
                            "tripped": (job_dir / "TRIPPED.md").exists(),
                            "credential": _carry_back_credentials(
                                harness, binding["home"], profile=profile, binding=binding, custody=custody)})
    if reason:
        rec.setdefault("note", reason)
    return code


def _carry_back_credentials(harness, home, *, profile=None, binding=None, custody=None):
    """LD-41 (2026-09-15): a credential the worker renewed inside its job
    home goes back to the host copy, so the next launch reads the renewal
    and not the login (isolation.carry_back). Never fails the attempt: a
    failure is a reading in the attempt, `unresolved`. The caller must retain
    the worker's pre-child profile and custody capture; never construct a
    post-child capture or import current source as a fallback.
    """
    try:
        if profile is None or not isinstance(binding, dict) or custody is None:
            raise ValueError("worker credential custody requires its admitted profile, binding and pre-child capture")
        profile.validate(binding)
        if binding["mode"] != "worker" or binding["harness"] != harness:
            raise ValueError("credential custody differs from the admitted worker")
        if Path(home).resolve(strict=True) != Path(binding["home"]).resolve(strict=True):
            raise ValueError("credential home differs from the admitted worker")
        return custody(binding, env=os.environ)
    except Exception as exc:  # noqa: BLE001 - retain unresolved reading without loading a fallback owner
        return [{"unresolved": str(exc)}]


def _read_role_write_guard(name, job):
    """Item 31 item 5 (amended 2026-09-11): a read-role prompt may not instruct a
    write to a path it does not declare in its outputs array. Judged on the
    template before any directory, record or home exists; refuses `role` on the
    founding wire through the correction seam."""
    sentence = WRITE_PATH.check_job(name, job)
    if sentence is None:
        return None
    CORRECTION.refuse(
        "role",
        "a read-role prompt with no instruction to write an undeclared path",
        f'job {name}: "{sentence}"',
        "drop the sentence, declare the output in the job's outputs array, or declare the job a write role",
    )
    return None


def _review_base(repo, ref_sha, branch):
    """merge-base(ref, <branch>) over the remote-tracking and local names
    of ``branch``, the nearer one; "" when neither resolves."""
    candidates = []
    for anchor in (f"origin/{branch}", branch):
        probe = _git(repo, "merge-base", ref_sha, anchor, check=False)
        if probe.returncode == 0 and probe.stdout.strip():
            candidate = probe.stdout.strip()
            distance = int(_git(repo, "rev-list", "--count",
                                f"{candidate}..{ref_sha}").stdout)
            candidates.append((distance, candidate))
    return min(candidates)[1] if candidates else ""


def _launch(args):
    worker = _worker(args.harness)
    lane = _lane_values(
        "WALL_DEFAULT",
        "SCOPE_CAP",
        "LANE_NAME",
        "TRUNK_BRANCH",
        "RELEASE_BRANCH",
    )
    agent = _identity()
    repo = _repo()
    branch = _branch(repo)
    lineage = args.lineage or branch
    protected = {
        "detached",
        "main",
        lane["TRUNK_BRANCH"],
        lane["RELEASE_BRANCH"],
    }
    if branch in protected or branch != lineage:
        _refuse("record-target", f"the checkout on {lineage}", branch,
                f"run from a worktree of {lineage}")
    root = _jobs_root().resolve()
    wt = _git(repo, "worktree", "list", "--porcelain").stdout
    for line in wt.splitlines():
        if line.startswith("worktree ") and _inside(root, Path(line[9:])):
            _refuse("live-target", f"a job directory outside every worktree of {repo}",
                    f"{root} inside {line[9:]}", "set DISPATCH_JOBS outside the repository")
    jobs = json.loads(Path(JOBS_PATH).read_text())
    if args.job not in jobs or jobs[args.job].get("adapter") != "harness":
        _refuse("job", "a harness job in jobs.json", args.job, "name a dispatchable job")
    job = jobs[args.job]
    role = job.get("role")
    _read_role_write_guard(args.job, job)
    stage = args.stage or ("code" if role == "write" else "review")
    if not args.model:
        _refuse("model", "a model", "none", "pass --model")
    if args.harness == "agy":
        _effort_argv(args.harness, args.effort)
        if role == "write":
            _refuse("write-role-unadmitted", "measured agy provider attribution",
                    "provider unavailable on native evidence", "use an attributed worker")
    if args.harness == "claude" and role == "write":
        _refuse("write-role-unadmitted", "a write row with a containment entry",
                "none for claude", "run the containment probe or use codex or grok")
    # Every job installs a worker-mode hook profile, and one with no issued
    # participant denies every hook call, so a write job without an assignment
    # would spend its budget refused (LD-116). Refused before anything is minted.
    if role == "write" and not args.assignment:
        CORRECTION.refuse(
            "participant",
            "an assignment (--for A-n) to issue this worker a participant through bind-for",
            "none",
            "draft or name the assignment and relaunch with --for",
        )
    _requested_timeout()
    mode = job.get("snapshot")
    if job.get("withheld") and mode == "whole":
        _refuse("history-vs-withheld", "one of history or withholding", f"both on {args.job}", "declare fileset")
    if mode != "whole":
        _refuse("mode-unavailable", "whole", f"{mode} on {args.job}", "stage it by hand")
    scope_bytes = len((args.scope or "").encode())
    takes_scope = "{scope}" in (job.get("prompt") or "")
    try:
        scope_cap = int(lane["SCOPE_CAP"])
    except ValueError:
        _refuse(
            "conf-key",
            "SCOPE_CAP as a positive whole number",
            lane["SCOPE_CAP"],
            f"correct SCOPE_CAP in {LANECONF.conf_path()}",
        )
    if scope_cap < 1:
        _refuse(
            "conf-key",
            "SCOPE_CAP as a positive whole number",
            lane["SCOPE_CAP"],
            f"correct SCOPE_CAP in {LANECONF.conf_path()}",
        )
    if scope_bytes > scope_cap or (args.scope is not None and not takes_scope):
        _refuse("scope-cap", f"<= {scope_cap} bytes on a job that takes a scope",
                f"{scope_bytes} bytes on {args.job}", "shorten it or drop it")
    resolved = _git(repo, "rev-parse", "--verify", f"{args.ref}^{{commit}}", check=False)
    if resolved.returncode:
        _refuse("ref", f"a ref naming a commit in {repo}", f"{args.ref} ({resolved.stderr.strip()})",
                "commit the work, then name the commit")
    ref_sha = resolved.stdout.strip()
    overrides = _parse_override(args.override, agent)
    ancestor = _git(repo, "merge-base", "--is-ancestor", ref_sha, lineage, check=False).returncode == 0
    if not ancestor and not overrides:
        tip = _git(repo, "rev-parse", lineage).stdout.strip()
        _refuse("stale-base", f"{ref_sha[:8]} reachable from {lineage}",
                f"it is not ({lineage} is at {tip[:8]})", "name a commit on the branch or override stale-base:REASON")
    # The base a review diffs against is where the lineage left the trunk, not
    # the lineage's own tip -- merge-base(ref, lineage) is ref itself when
    # ref is the tip, and a review of an empty comparison reviews nothing.
    # A ref that sits on the trunk (the release review, trunk into release)
    # has the trunk as its own base; it anchors on the release branch when
    # one exists, so the comparison is release...trunk (LD-36, 2026-09-14).
    base_sha = _review_base(repo, ref_sha, lane["TRUNK_BRANCH"])
    if base_sha == ref_sha and lane["RELEASE_BRANCH"]:
        base_sha = _review_base(repo, ref_sha, lane["RELEASE_BRANCH"])
    if not base_sha:
        base_sha = _git(repo, "merge-base", ref_sha, lineage).stdout.strip()
    prompt_template = job.get("prompt") or ""
    takes_comparison = all(slot in prompt_template for slot in ("{base}", "{diff}"))
    # The guard reads the derived stage: the wrapper passes no --stage for a
    # review job, and reading args.stage handed the reviewer an empty patch
    # without a word (LD-36).
    if takes_comparison and stage == "review" and base_sha == ref_sha:
        _refuse("empty-comparison", f"{lineage} to differ from ref {ref_sha}",
                f"an empty comparison at {ref_sha}",
                f"name a ref on {lineage} with changes to review")
    stamp = _now()
    gate = CORRECTION.check(
        role,
        args.follows,
        args.input,
        args.answers,
        stamp,
        repo / ".dev/records/dispatches",
        _jobs_root(),
        repo,
    )
    for source in args.input:
        if _dotdot_input(source, _jobs_root()):
            CORRECTION.refuse(
                "input",
                "an input path without .. components under the jobs root",
                f".. in {source}",
                "spell the input path plainly",
            )
        if _symlinked_input(source, _jobs_root()):
            CORRECTION.refuse(
                "input",
                "an input path without symlinked components under the jobs root",
                f"symlink in {source}",
                "replace it with a regular input path",
            )
    job_id = mint_id(stage, args.harness)
    job_dir, snapshot = _job_paths(job_id)
    record_path = repo / ".dev/records/dispatches" / f"{job_id}.json"
    _preflight_isolation(args.harness, job_dir / "home" / args.harness, os.environ)
    if job_dir.exists() or record_path.exists():
        raise RuntimeError(f"dispatch id collision: {job_id}")
    job_dir.mkdir(parents=True)
    for name in ("in", "out"):
        (job_dir / name).mkdir()
    _snapshot(repo, lineage, ref_sha, snapshot)
    copied = []
    for source in args.input:
        src = Path(source)
        dest = job_dir / "in" / src.name
        if dest.exists():
            raise RuntimeError(f"duplicate input basename: {src.name}")
        shutil.copyfile(src, dest)
        copied.append({"path": str(dest), "sha256": _sha(dest)})
    diff_text = _git(repo, "diff", f"{base_sha}..{ref_sha}").stdout if base_sha != ref_sha else ""
    context_dir = job_dir / "context"
    context_dir.mkdir()
    graft_metadata = _graft_call("prepare_worker", job_dir, snapshot, args.job)
    context_diff = context_dir / "diff.patch"
    context_diff.write_text(diff_text)
    diff_file = job_dir / "diff.patch"
    diff_file.symlink_to(context_diff)
    diff_path = str(diff_file.resolve())
    values = {"ref": ref_sha, "base": base_sha, "diff": diff_path, "into": str(snapshot.resolve()),
              "out": str((job_dir / "out").resolve()),
              "inputs": " ".join(item["path"] for item in copied), "scope": args.scope or ""}
    turns, turns_source = _resolve_turns(job.get("caps"))
    turns_note = _turns_note(args.harness, turns)
    turns_argv = _turns_argv(args.harness, turns)
    prompt_text = _render(job["prompt"], values)
    prompt_text += _turns_budget(turns)
    binding_session = str(uuid.uuid4())
    session = None if args.harness == "agy" else binding_session
    _iso_env, _flags, sandbox, containment, isolation_data = _harness_meta(
        args.harness, role, job_dir, os.environ)
    # The private credential must exist before bind-for enrolls its digest.
    # This is the existing worker profile, reused by _run_attempt and resume;
    # no raw credential enters the acting config, receipt or public record.
    isolation = _module("guard_isolation", HARNESS_APP / "isolation.py")
    try:
        _profile, binding = isolation.install_enforcement(
            args.harness, job_dir / "enforcement", source=APP.parents[2], cwd=snapshot,
            home=isolation_data.get("home"), session=binding_session, agent=agent,
            executable=worker["executable"],
            servers=[LANE_MCP_SERVER, LANE_MCP_KEY, COORD_MCP_SERVER])
        worker_digest = hashlib.sha256(
            binding["coordination_capability"].encode("ascii")).hexdigest()
    except (isolation.NotIsolated, OSError, ValueError, TypeError, KeyError) as exc:
        _refuse("enforcement", "a private authenticated worker hook profile", str(exc),
                "inspect co_doc.py before dispatching this worker")
    bound = None
    if args.assignment:
        try:
            bound = _bind_worker(binding_session, args.assignment,
                                 args.harness, agent, capability_digest=worker_digest)
        except Refused:
            # This launch owns only fresh staging: no record or child exists.
            shutil.rmtree(job_dir)
            raise
    mcp_meta, mcp_server = _lane_mcp(job_dir, snapshot, job_id, repo, args.unit or lineage,
                                     args.follows)
    coord_meta, coord_server = _coordination_mcp(
        job_dir, snapshot, args.harness, job_id,
        _bound_participant(bound), args.assignment, lineage, stamp)
    prompt_text += "\n\n" + _what_exists(snapshot, jobs, mcp_meta)
    if coord_server:
        prompt_text += "\n\n" + _acting_clause(coord_meta)
    # Dispatch names its copies even when the shared template omits inputs.
    if copied:
        prompt_text += "\n\nInput files: " + values["inputs"]
    graft_note = _graft_call("worker_note", graft_metadata)
    if graft_note:
        prompt_text += "\n\n" + graft_note
    if args.harness in {"codex", "grok", "agy"}:
        prompt_text += (
            "\n\nThe envelope shape, as JSON Schema. Every key in `required` "
            "is present; no other top-level key; the types are exact "
            "(findings are objects, counts are integers, artifacts is an "
            "object of strings):\n" + ENVELOPE_SCHEMA_JSON
        )
    prompt = job_dir / "prompt.txt"
    prompt.write_text(prompt_text)
    if args.harness == "grok":
        grok_config = _grok_mcp_config(_iso_env.get("GROK_HOME"), mcp_server)
        if grok_config:
            mcp_meta["grok_config"] = grok_config
    if args.harness == "codex" and role == "read":
        sandbox = "workspace-write"  # LD-14: OS containment, out/ writable
    permissions = None
    project_allow = project_deny = ()
    if args.harness == "claude":
        permissions, project_allow, project_deny = _project_permissions(snapshot)
    wires = _module("wires", HARNESS_APP / "wires.py")
    tip = _git(repo, "rev-parse", lineage).stdout.strip()
    rec = record.build({
        "id": job_id, "lane": "dev", "stage": stage, "unit": args.unit or lineage,
        "lineage": {"branch": lineage, "base_sha": tip}, "follows": args.follows,
        "answers": gate["answers"],
        "job": args.job, "role": role, "dispatched_by": agent,
        "at": {"launched": stamp, "closed": None},
        "snapshot": {"mode": "whole", "ref_name": args.ref, "ref_sha": ref_sha,
                     "behind_tip": (lambda n: n + 1 if n else 0)(int(
                         _git(repo, "rev-list", "--count", f"{ref_sha}..{lineage}").stdout)),
                     "root": str(snapshot.resolve())},
        "harness": {"name": args.harness, "worker": worker,
                    "version": "unknown", "isolation": isolation_data,
                    "sandbox": sandbox, "containment": containment,
                    **({"permissions": permissions} if permissions is not None else {}),
                    "argv": _argv(args.harness, args.model, args.effort, session,
                                  prompt, _flags, sandbox, False,
                                  project_allow, project_deny,
                                  writable_out=role == "read",
                                  executable=worker["executable"],
                                  turns=turns, mcp=mcp_server,
                                  coord=coord_server)},
        "model": {"requested": args.model, "effort_requested": args.effort,
                  "effort_argv": _effort_argv(args.harness, args.effort),
                  "ran": None, "read_from": None, "note": "no stream found"},
        # `bound` is the lever's own line when this dispatch bound a
        # participant for the session above, and None when it named no
        # assignment. A record that merely minted a session id and one
        # that bound a participant to it are different facts.
        "session": {"id": session, "binding_id": binding_session, "stream": None,
                    "stream_sha256_at_close": None, "bound": bound},
        "brief": {"template": {"path": str(JOBS_PATH), "sha256": _sha(JOBS_PATH)},
                  "scope": args.scope, "inputs": copied, "sha256": _sha(prompt), "bytes": prompt.stat().st_size,
                  "mcp": mcp_meta,
                  # The acting plane, beside the read plane. A dispatch
                  # that wired nothing says which of the three reasons
                  # it was, never an empty object.
                  "coordination": coord_meta,
                  **({"graft": graft_metadata} if graft_metadata is not None else {})},
        "caps": {"cap-out": wires.budget(role), "source": "wires.py",
                 "turns": turns, "turns_source": turns_source,
                 "turns_argv": turns_argv,
                 # Declared is not applied, and says which it was: a
                 # harness with no turn flag records the cap it was given
                 # while nothing carried it.
                 "turns_applied": bool(turns_argv),
                 **({"turns_note": turns_note} if turns_note else {})},
        "overrides": overrides, "attempts": [], "result": None, "status": "launched"})
    if args.harness == "agy":
        rec["model"]["provider"] = {
            "id": None, "read_from": None,
            "unresolved": "provider unavailable on measured agy stdout and transcript"}
    _write_record(repo, rec)
    print(f"record: {job_id}", flush=True)
    _run_attempt(rec, job_dir, job_caps=job.get("caps"))
    path, ran, found_id, mismatch = _stream(
        job_dir, args.harness, session, snapshot, ref_sha, role,
    )
    if path:
        if args.harness in {"codex", "grok"}:
            old_store = job_dir / "home" / args.harness / "sessions"
            new_store = job_dir / "home" / f"{args.harness}-stream" / "sessions"
            new_store.parent.mkdir(parents=True, exist_ok=True)
            if old_store.exists():
                shutil.move(str(old_store), str(new_store))
                path = new_store / path.relative_to(old_store)
        rec["session"].update(stream=str(path), stream_sha256_at_close=_sha(path))
        rec["model"].update(ran=ran, read_from=str(path))
        rec["model"].pop("note", None)
        if args.harness in {"codex", "agy"} and found_id:
            rec["session"]["id"] = found_id
    rec["session"]["spend"] = _spend(rec, path, job_dir)
    raw = (job_dir / "raw.out").read_bytes()
    runtime_note = rec.pop("_runtime_note", None)
    attempt_code = rec["attempts"][-1]["exit"]
    if runtime_note:
        env = _envelope(raw, args.job, ref_sha, runtime_note,
                        harness=args.harness, job_dir=job_dir, rec=rec)
        if runtime_note.startswith("trip"):
            env["status"] = "tripped"
    elif attempt_code:
        env = _envelope(raw, args.job, ref_sha,
                        f"harness-cli: exited {attempt_code}",
                        harness=args.harness, job_dir=job_dir, rec=rec)
    else:
        env = _envelope(raw, args.job, ref_sha, mismatch,
                        harness=args.harness, job_dir=job_dir, rec=rec)
    _settle(repo, record_path, rec, env)
    return 2 if runtime_note else 0


VENDOR = {"codex": "noreply@openai.com", "grok": "noreply@x.ai", "claude": "noreply@anthropic.com"}
# Attribution names per CONTRIB.md, keyed by the model id the stream reports.
# An unknown id is credited as itself rather than guessed.
MODEL_NAMES = {"gpt-5.6-sol": "GPT-5.6 Sol", "grok-4.6": "Grok 4.6",
               "claude-opus-5": "Claude Opus 5", "claude-fable-5": "Claude Fable 5",
               "claude-sonnet-5": "Claude Sonnet 5"}


def _commit_message(rec, env=None, job_dir=None, *, collected=False):
    """The message the launcher commits with: the harness's own subject and
    body when it wrote out/COMMIT_MSG or its envelope carries
    `commit: {subject, body}`, else the generic line; either way the
    dispatch id and the attribution the CONTRIB template names (the
    model's display name, never its id)."""
    model = rec["model"].get("ran") or rec["model"]["requested"]
    name = MODEL_NAMES.get(model, model)
    vendor = VENDOR.get(rec["harness"]["name"], "noreply@unknown")
    commit = (env or {}).get("commit")
    msg_file = job_dir / "out" / "COMMIT_MSG" if job_dir else None
    if msg_file is not None and msg_file.is_file():
        text = msg_file.read_text(errors="replace").strip()
        if text:
            first, _, rest = text.partition("\n")
            commit = {"subject": first, "body": rest}
    subject = body = None
    kept_trailers = []
    if isinstance(commit, dict) and isinstance(commit.get("subject"), str) and commit["subject"].strip():
        subject = commit["subject"].strip().splitlines()[0]
        body = commit.get("body") if isinstance(commit.get("body"), str) else ""
        body_lines = []
        for line in body.strip().splitlines():
            # A trailer-shaped line anywhere but the final block is body
            # text to git and a refusal to the commit-msg hook (2026-08-29:
            # a codex COMMIT_MSG with `Reviewed-by:` mid-body was refused
            # and the generic subject landed). The launcher's own trailers
            # are replaced; every other trailer the harness wrote moves
            # into the final block, contiguous.
            if re.match(r"^Dispatch:", line):
                continue
            if re.match(r"^[A-Za-z][A-Za-z-]*: \S", line) and not line.startswith(("http", "Note:", "TODO:")):
                kept_trailers.append(line.strip())
                continue
            body_lines.append(line)
        body = "\n".join(body_lines).strip()
    if subject is None:
        subject = f"{rec['job']}: work of dispatch {rec['id']}"
        body = ""
    sources = [x for x in kept_trailers if x.startswith("Source:")]
    coauthors = [re.sub(r"^Co-authored-by:", "Co-Authored-By:", x) for x in kept_trailers
                 if x.lower().startswith("co-authored-by:")]
    others = [x for x in kept_trailers if not x.startswith("Source:") and not x.lower().startswith("co-authored-by:")]
    mine = f"Co-Authored-By: {name} <{vendor}>"
    # the running model's line replaces any the harness wrote for *its own*
    # vendor (an id or a wrong display name); other co-authors are kept
    coauthors = [c for c in coauthors if f"<{vendor}>" not in c]
    block = []
    for line in [*sources, "Source: original", *others, *coauthors, mine]:
        if line not in block:
            block.append(line)
    explanation = (
        f"the {rec['harness']['name']} run was tripped before it committed"
        if collected else f"the {rec['harness']['name']} sandbox denies .git"
    )
    return (f"{subject}\n\n" + (f"{body}\n\n" if body else "")
            + f"Committed by the launcher: {explanation}.\n"
            f"Dispatch: {rec['id']}\n\n" + "\n".join(block) + "\n")


def _commit_for_harness(snapshot, rec, env=None, *, collected=False):
    """codex's sandbox keeps .git read-only, so a write job leaves its work
    uncommitted in the snapshot (2026-08-28: an implement job made the
    named test green and could not commit). The launcher commits it,
    crediting the model that ran, with the harness's own message when the
    envelope carries one (2026-08-29: five green codex runs landed under
    a generic subject and the conductor re-wrote each by hand)."""
    owner_env = dict(os.environ)
    owner_env.update(GIT_AUTHOR_NAME=OWNER_NAME, GIT_AUTHOR_EMAIL=OWNER_EMAIL,
                     GIT_COMMITTER_NAME=OWNER_NAME,
                     GIT_COMMITTER_EMAIL=OWNER_EMAIL)
    _git(snapshot, "add", "-A", env=owner_env)
    _git(snapshot, "commit", "-q", "-F", "-", env=owner_env,
         input=_commit_message(rec, env, snapshot.parent,
                               collected=collected))


def _cause(rec, job_dir, env=None):
    """Why the harness stopped, in the order plan ebe2bb U4b.1 fixes:
    tripped → timeout → unsupervised → harness-cli:<code> → a permission
    prompt the turn never recovered from → the last event the session
    store holds. First match wins; a cancelled prompt is blamed only when
    it is the last permission event and nothing but phase/turn bookkeeping
    followed it (review ba0d93: a cancellation the turn recovered from
    must not be blamed)."""
    env = env or {}
    attempt = (rec.get("attempts") or [{}])[-1]
    marker = job_dir / "TRIPPED.md"
    if attempt.get("tripped") or env.get("status") == "tripped" or marker.exists():
        # The launcher writes TRIPPED.md for all three kills; only its own
        # first line says which (review ff6440 N3: `tripped` shadowed
        # timeout and unsupervised; N4: the harness's prose must never
        # decide the reason).
        own = marker.read_text(errors="replace").strip().splitlines()[0].lower() if marker.exists() else ""
        for word in ("timeout", "unsupervised"):
            if own.startswith(word) or f" {word}" in own[:80]:
                return {"reason": word, "note": own or None}
        return {"reason": "tripped", "note": own or None}
    if attempt.get("exit"):
        return {"reason": f"harness-cli:{attempt['exit']}"}
    if rec.get("_envelope_cause"):
        return {"reason": rec.pop("_envelope_cause")}
    harness = rec["harness"]["name"]
    stream = (rec.get("session") or {}).get("stream")
    events = None
    if harness == "grok":
        candidates = []
        if stream and Path(stream).is_file():
            candidates.append(Path(stream).parent / "events.jsonl")
        candidates += sorted((job_dir / "home").glob("grok-stream/**/events.jsonl"))
        candidates += sorted((job_dir / "home").glob("grok/**/events.jsonl"))
        events = next((c for c in candidates if c.is_file()), None)
    elif stream and Path(stream).is_file():
        events = Path(stream)
    if events is None:
        return {"reason": "no-session-store"}
    last = None
    last_permission = None
    after_permission = []
    for line in events.read_text(errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        last = item
        if item.get("type") == "permission_resolved":
            last_permission = item
            after_permission = []
        elif last_permission is not None:
            after_permission.append(item.get("type"))
    # A cancelled tool emits its own tool_result row; that is the cancel,
    # not recovery (review ff6440 N2). Recovery is later work: another
    # permission request, an assistant turn, a tool call.
    if after_permission and after_permission[0] == "tool_result":
        after_permission = after_permission[1:]
    bookkeeping = ("phase_changed", "turn_ended", "session_end", "tool_result")
    if (last_permission is not None
            and last_permission.get("decision") not in (None, "approved", "allowed", "allow")
            and all(kind in bookkeeping for kind in after_permission)):
        return {"reason": f"permission-{last_permission.get('decision')}",
                "tool": last_permission.get("tool_name"),
                "wait_ms": last_permission.get("wait_ms"), "at": last_permission.get("ts")}
    if last is None:
        return {"reason": "empty-session-store", "path": str(events)}
    return {"reason": "last-event", "type": last.get("type"),
            "at": last.get("ts") or last.get("timestamp")}


def _spend(rec, stream, job_dir):
    """`session.spend` from the harness's own store, read by
    telemetry/usage.py (the same parsers the usage report uses). A value
    or `{unresolved: <why>}` — never a zero (U5; 2026-08-29: 105 of 105
    records carried `spend: null`, so no cost could be measured)."""
    harness = rec["harness"]["name"]
    session = (rec.get("session") or {}).get("id")
    fallback_roots = [job_dir / "home" / f"{harness}-stream",
                      job_dir / "home" / harness]

    def unreachable(reason, roots=None):
        return {"unresolved": f"UNREACHABLE: {reason}",
                "looked_in": [str(path) for path in (roots or fallback_roots)]}

    if not stream and harness != "agy":
        return unreachable("no session stream discovered")
    try:
        usage = _module("usage", TELEMETRY_APP / "usage.py")
    except (OSError, ImportError, SyntaxError) as exc:
        return unreachable(f"usage.py not loadable: {exc}")
    stream = job_dir / "raw.out" if harness == "agy" else Path(stream)
    try:
        if harness == "agy":
            root = job_dir
            sessions = list(usage.agy_sessions(
                root, usage.Placement(rec["snapshot"]["root"])))
        elif harness == "claude":
            root = stream.parent.parent
            sessions = list(usage.claude_sessions(root, usage.Placement(None)))
        elif harness == "codex":
            root = job_dir / "home" / "codex-stream"
            if not root.exists():
                root = job_dir / "home" / "codex"
            sessions = list(usage.codex_sessions(root, usage.Placement(None)))
        elif harness == "grok":
            root = job_dir / "home" / "grok-stream"
            if not root.exists():
                root = job_dir / "home" / "grok"
            sessions = list(usage.grok_sessions(root, usage.Placement(None)))
        else:
            return unreachable(f"no usage parser for {harness}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return unreachable(
            f"usage parse failed: {type(exc).__name__}: {exc}", [root])
    match = next((s for s in sessions if isinstance(s, dict) and str(s.get("session")) == str(session)), None)
    if match is None:
        return unreachable(
            f"session {session} not in the store ({len(sessions)} sessions read)",
            [root],
        )
    tokens = match.get("tokens")
    if not isinstance(tokens, dict) or not any(isinstance(v, (int, float)) for v in tokens.values()):
        return unreachable("the store carries no usage for this session", [root])
    spend = {k: tokens.get(k) for k in ("input", "cached", "output", "total") if tokens.get(k) is not None}
    if match.get("cost_usd_ticks") is not None:
        spend["cost_usd_ticks"] = match["cost_usd_ticks"]
    if match.get("incomplete"):
        spend["incomplete"] = True
    spend["source"] = str(stream)
    spend["messages"] = match.get("messages")
    return spend


def _fill_envelope_spend(rec, env):
    measured = (rec.get("session") or {}).get("spend") or {}
    supplied = env.get("spend") if isinstance(env, dict) else None
    fields = {"harness", "total", "out", "runs"}
    if (isinstance(measured, dict) and measured.get("total") is not None
            and isinstance(supplied, dict) and set(supplied) == fields):
        env["spend"] = {
            "harness": rec["harness"]["name"],
            "total": measured["total"],
            "out": measured.get("output", 0),
            "runs": len(rec.get("attempts") or []),
        }


def _write_residual_patch(snapshot, job_dir, residual=None):
    """Preserve what a harness left uncommitted — staged, unstaged and
    untracked alike — as one patch `git apply` accepts, so a retry can take
    it as an input instead of the conductor diffing the snapshot by hand.
    A temporary index keeps the snapshot's own index untouched."""
    import tempfile
    residual = (_residual_entries(snapshot) if residual is None
                else tuple(residual))
    paths = list(dict.fromkeys(
        path for entry in residual for path in entry.paths
    ))
    untracked = [
        _path_argument(path)
        for entry in residual if entry.code in {"??", "!!"}
        for path in entry.paths
    ]
    with tempfile.NamedTemporaryFile(prefix="residual-index-", delete=False) as tmp:
        index = tmp.name
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        _git(snapshot, "read-tree", "HEAD", env=env)
        _git(snapshot, "--literal-pathspecs", "add", "-A", "-f", "--",
             *paths, env=env)
        patch = _git(snapshot, "diff", "--cached", "--binary", "HEAD",
                     env=env, check=False, text=False).stdout
    finally:
        Path(index).unlink(missing_ok=True)
    out = job_dir / "residual.patch"
    out.write_bytes(patch)
    return {"path": str(out), "sha256": _sha(out), "untracked": untracked}


# Tool caches a read role leaves behind by running the suite or ruff are not
# residual writes. These are measured, bounded layouts: unknown names remain
# evidence even below a cache-named root.
CACHE_MARKERS = {".gitignore", "CACHEDIR.TAG"}
PYTEST_CACHE_VALUES = {"lastfailed", "nodeids", "stepwise"}
VERSION_DIR = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")
MYPY_CACHE_FILE = re.compile(
    r"(?:.+\.(?:data|meta|meta_ex|deps|refs)\.(?:json|ff)(?:\.[0-9a-f]+)?"
    r"|cache(?:\.[0-9]+)?\.db(?:-(?:shm|wal))?)"
)
MYPY_GLOBAL_FILES = {
    "@deps.meta.json", "@plugins_snapshot.json", "@root.deps.json",
}


class _ResidualEntry:
    __slots__ = ("code", "paths")

    def __init__(self, code, paths):
        self.code = code
        self.paths = paths


_C_ESCAPES = {
    7: "a", 8: "b", 9: "t", 10: "n", 11: "v", 12: "f", 13: "r",
    34: '"', 92: "\\",
}


def _path_argument(raw):
    try:
        path = os.fsdecode(raw)
        round_trip = os.fsencode(path)
    except UnicodeError:
        path = None
        round_trip = None
    if round_trip != raw:
        _refuse("residual-path", "a lossless filesystem path byte sequence",
                repr(raw), "use a filesystem encoding that preserves the path")
    return path


def _display_path(raw):
    if all(33 <= byte < 127 and byte not in {34, 92} for byte in raw):
        return raw.decode("ascii")
    rendered = []
    for byte in raw:
        if byte in _C_ESCAPES:
            rendered.append("\\" + _C_ESCAPES[byte])
        elif 32 <= byte < 127:
            rendered.append(chr(byte))
        else:
            rendered.append(f"\\{byte:03o}")
    return '"' + "".join(rendered) + '"'


def _display_residual(entry):
    names = " -> ".join(_display_path(path) for path in entry.paths)
    return f"{entry.code} {names}"


def _is_cache_residual(entry):
    for raw in entry.paths:
        path = Path(_path_argument(raw))
        parts = path.parts
        if "__pycache__" in parts[:-1] and path.suffix == ".pyc":
            continue
        if parts and parts[0] == ".ruff_cache":
            if len(parts) == 2 and (parts[1] in CACHE_MARKERS or not path.suffix):
                continue
            if (len(parts) == 3 and VERSION_DIR.fullmatch(parts[1])
                    and not path.suffix):
                continue
        if parts and parts[0] == ".pytest_cache":
            if len(parts) == 2 and parts[1] in CACHE_MARKERS | {"README.md"}:
                continue
            if (len(parts) == 4 and parts[1:3] == ("v", "cache")
                    and parts[3] in PYTEST_CACHE_VALUES):
                continue
        if parts and parts[0] == ".mypy_cache":
            if len(parts) == 2 and parts[1] in CACHE_MARKERS | {"missing_stubs"}:
                continue
            if (len(parts) >= 3 and VERSION_DIR.fullmatch(parts[1])
                    and (path.name in MYPY_GLOBAL_FILES
                         or MYPY_CACHE_FILE.fullmatch(path.name))):
                continue
        return False
    return True


def _residual_entries(snapshot):
    raw = _git(
        snapshot, "status", "--porcelain=v1", "-z", "-uall", text=False,
    ).stdout
    records = raw.split(b"\0")
    if records.pop() != b"":
        raise RuntimeError("git status -z did not end with NUL")
    entries = []
    offset = 0
    while offset < len(records):
        record = records[offset]
        offset += 1
        if len(record) < 4 or record[2:3] != b" ":
            raise RuntimeError(f"malformed git status -z record: {record!r}")
        try:
            code = record[:2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                f"non-ASCII git status code: {record[:2]!r}"
            ) from exc
        paths = (record[3:],)
        if "R" in code or "C" in code:
            if offset == len(records):
                raise RuntimeError("rename status omitted its source path")
            # In -z mode Git emits destination first, then source. Keep the
            # public display's source -> destination order.
            paths = (records[offset], record[3:])
            offset += 1
        for path in paths:
            _path_argument(path)
        entries.append(_ResidualEntry(code, paths))

    ignored = _git(
        snapshot, "ls-files", "--others", "--ignored", "--exclude-standard",
        "-z", text=False,
    ).stdout.split(b"\0")
    entries.extend(_ResidualEntry("!!", (path,)) for path in ignored if path)
    return tuple(entry for entry in entries if not _is_cache_residual(entry))


def _residual(snapshot):
    return [_display_residual(entry) for entry in _residual_entries(snapshot)]


RECLAIMABLE = ("snapshot", "home")


def _reclaim_conf():
    """The sweep's two keys, read with their names written out.

    `_lane_values` passes a key through a variable, which laneconf.py's closure
    verb cannot follow when it looks for a declaration's reader, so a key read
    only that way reports as an unread declaration. These literal gets are this
    file's evidence that it reads them. The refusal is the one `_lane_values`
    raises, on the same wire.
    """
    try:
        return (LANECONF.get("DISPATCH_RECLAIM"),
                LANECONF.get("DISPATCH_RECLAIM_DAYS"))
    except LANECONF.ConfigError as exc:
        kind = "conf-key" if exc.kind == "key" else exc.kind
        raise Refused(exc.line(kind)) from exc


def _reclaim_candidate(rec, job_dir, repo, cutoff):
    """Whether one closed job's bulk may go, and the reading if it may not.

    Returns (True, None) or (False, reason). Every refusal is a reading, so a
    job that stays says why on the sweep's record rather than being skipped in
    silence.
    """
    closed = (rec.get("at") or {}).get("closed")
    if not closed:
        return False, "not closed"
    try:
        when = _parse_stamp(closed)
    except (ValueError, TypeError):
        return False, f"unreadable closed stamp {closed!r}"
    if when > cutoff:
        return False, "younger than the retention window"
    # `resume` reads snapshot/ and home/ for a job whose envelope was invalid.
    result = rec.get("result") or {}
    if (result.get("envelope") or {}).get("status") != "ok":
        return False, "envelope is resumable"
    head = result.get("head")
    if not head or _git(repo, "cat-file", "-e", f"{head}^{{commit}}",
                        check=False).returncode:
        return False, f"head {head!r} is not in this repository"
    if (result.get("residual_paths") or []) and not result.get("residual_patch"):
        return False, "residual paths with no patch"
    if not any((job_dir / name).is_dir() for name in RECLAIMABLE):
        return False, "already reclaimed"
    return True, None


def _drop_bulk(job_dir):
    """Remove one job's snapshot/ and home/, lifting the harness's seal."""
    removed = []
    for name in RECLAIMABLE:
        target = job_dir / name
        if not target.is_dir():
            continue
        # The harness seals a finished snapshot 555 through the whole tree, and
        # rmtree cannot unlink from a directory it cannot write to (measured
        # 2026-09-18: 9,145 sealed directories in one hand sweep).
        for path in (target, *target.rglob("*")):
            if path.is_dir():
                with contextlib.suppress(OSError):
                    path.chmod(path.stat().st_mode | 0o700)
        try:
            shutil.rmtree(target)
            removed.append(name)
        except OSError:
            pass
    return removed


def _reclaim_store(repo, exclude_id):
    """Drop the reproducible bulk of jobs that closed outside the window.

    `snapshot/` is a git checkout whose head refs/dispatch/<id> holds; `home/`
    is the harness isolation directory. The evidence -- record.json,
    diff.patch, out/, context/, the streams -- is never touched.

    The sweep runs at a close and never touches the job that just closed:
    a fresh snapshot is what `resume` resumes from, what a reviewer opens, and
    what the launcher's own suite inspects after a run. Only the backlog goes.

    Measured 2026-09-18 before this existed: 845 job directories held
    1,556,838 inodes, 44% of this host, and snapshot/ plus home/ were
    1,525,881 of them, accumulating since 2026-09-03 because nothing swept a
    store that lives outside the repository.
    """
    setting, window = _reclaim_conf()
    if (setting or "").strip() != "on":
        return {"swept": False, "reason": "DISPATCH_RECLAIM is not 'on'"}
    try:
        days = int((window or "").strip())
        if days < 1:
            raise ValueError(days)
    except (TypeError, ValueError):
        return {"swept": False,
                "reason": f"DISPATCH_RECLAIM_DAYS={window!r} is not a positive integer"}

    root = _jobs_root()
    if not root.is_dir():
        return {"swept": False, "reason": "no dispatch store"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    reclaimed, kept = [], {}
    for job_dir in sorted(root.iterdir()):
        if not job_dir.is_dir() or job_dir.name == exclude_id:
            continue
        record_path = job_dir / "record.json"
        if not record_path.is_file():
            kept[job_dir.name] = "no record in the store"
            continue
        try:
            rec = json.loads(record_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            kept[job_dir.name] = f"unreadable record: {exc}"
            continue
        ok, why = _reclaim_candidate(rec, job_dir, repo, cutoff)
        if not ok:
            if why != "already reclaimed":
                kept[job_dir.name] = why
            continue
        if _drop_bulk(job_dir):
            reclaimed.append(job_dir.name)
    return {"swept": True, "days": days, "reclaimed": len(reclaimed),
            "kept": len(kept), "ids": reclaimed[:20]}


def _settle(repo, record_path, rec, env, *, subject=None, preserve_cause=False):
    """Collect the snapshot's state, finalize the record, commit it."""
    job_dir, snapshot = _bound_snapshot(rec)
    ref_sha = rec["snapshot"]["ref_sha"]
    _verify_record_ref(snapshot, ref_sha)
    role, job_id = rec["role"], rec["id"]
    prior_cause = (rec.get("result") or {}).get("cause")
    _fill_envelope_spend(rec, env)
    residual_entries = _residual_entries(snapshot)
    tripped = bool(rec.get("attempts") and rec["attempts"][-1].get("tripped"))
    if (role == "write" and rec["harness"]["name"] == "codex"
            and residual_entries and not tripped):
        _commit_for_harness(snapshot, rec, env)
        residual_entries = _residual_entries(snapshot)
    residual = [_display_residual(entry) for entry in residual_entries]
    residual_patch = (_write_residual_patch(snapshot, job_dir, residual_entries)
                      if residual_entries else None)
    head = _git(snapshot, "rev-parse", "HEAD").stdout.strip()
    changed = _git(
        snapshot, "diff", "--name-only", ref_sha, head, "--",
    ).stdout.splitlines()
    if role == "read" and head != ref_sha:
        _invalidate_envelope(env, "read-role-head: HEAD must equal ref_sha")
    elif role == "read" and residual:
        _invalidate_envelope(env, "read-role-snapshot: snapshot must be unchanged")
    elif role == "write" and _git(snapshot, "merge-base", "--is-ancestor", ref_sha, head, check=False).returncode:
        _invalidate_envelope(env, "off-lineage-head: HEAD does not descend from ref_sha")
    elif role == "write":
        _git(repo, "-c", "core.logAllRefUpdates=always", "fetch",
             "--no-write-fetch-head", str(snapshot), f"{head}:refs/dispatch/{job_id}")
    if role == "write":
        declared = REUSE_RE.search(str(env.get("note") or ""))
        if declared is None and env.get("status") == "ok":
            _invalidate_envelope(env, "reuse-undeclared: a write envelope's note carries "
                                      "`reused: <what>` or `reused: nothing applied`")
    wired = bool(((rec.get("brief") or {}).get("mcp") or {}).get("server"))
    # The record holds the store path with its snapshot segment made
    # portable; the reader needs the real path (_absolute_paths inverts it).
    real_stream = _absolute_paths({"snapshot": rec.get("snapshot"),
                                   "session": rec.get("session")})["session"].get("stream")
    calls = _mcp_calls(real_stream, rec["harness"]["name"]) if wired else None
    if wired and env.get("status") == "ok":
        if calls is None:
            _invalidate_envelope(env, "framework-unproven: the worker's store could not be read, "
                                      "so no lane MCP call is proven; could not look is not a pass")
        elif not calls:
            _invalidate_envelope(env, "framework-unused: the worker made no lane MCP call; the brief "
                                      "named the server and its tools")
    rec["result"] = {"head": head, "changed_paths": changed,
                     "residual_paths": residual, "envelope": env}
    rec["result"]["proof"] = {
        "mcp_wired": wired,
        "mcp_calls": None if calls is None else len(calls),
        "mcp_tools": sorted(set(calls)) if calls else [],
        "store": real_stream,
        **({"unresolved": "no readable store"} if wired and calls is None else {}),
    }
    if role == "write":
        rec["result"]["reuse"] = declared.group(1).strip() if declared else "undeclared"
    if residual_patch:
        rec["result"]["residual_patch"] = residual_patch
    cause = prior_cause if preserve_cause else _cause(rec, job_dir, env)
    rec.pop("_envelope_cause", None)
    rec["result"]["cause"] = cause
    if env.get("status") == "invalid" and str(env.get("note", "")).startswith("envelope-parse"):
        env["note"] = f"{env['note']}; cause: {json.dumps(cause, sort_keys=True)}"
    rec["at"]["closed"] = _now()
    rec["status"] = "closed"
    # The backlog sweep, never this job: a fresh snapshot is what resume
    # resumes from and what a reviewer opens. A failure here never fails a
    # close -- the job is settled either way.
    try:
        rec["result"]["store_sweep"] = _reclaim_store(repo, rec["id"])
    except Exception as exc:  # noqa: BLE001 - a sweep never fails a close
        rec["result"]["store_sweep"] = {"swept": False, "reason": str(exc)}
    _write_record(repo, rec)
    _commit_record(repo, record_path, rec, subject=subject)


def _job_state(d):
    if not d.exists():
        return "unlaunched"
    if (d / "TRIPPED.md").exists():
        return "tripped"
    if (d / "exit").exists():
        return "finished"
    try:
        pid = json.loads((d / "state.json").read_text()).get("pid")
        os.kill(int(pid), 0)
        return "running"
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return "DIED"


def _status(args):
    root = _jobs_root()
    dirs = [_job_paths(args.id)[0]] if args.id else sorted(
        path for path in root.glob("*") if path.is_dir())
    rows = []
    for d in dirs:
        state = _job_state(d)
        rows.append({"id": d.name, "status": state})
    payload = rows[0] if args.id and rows else rows
    print(json.dumps(payload) if args.json else "\n".join(f"{r['id']} {r['status']}" for r in rows))
    return 0


def _find_record(repo, job_id):
    _job_paths(job_id)
    path = repo / ".dev/records/dispatches" / f"{job_id}.json"
    if not path.is_file():
        raise RuntimeError(f"record not found: {job_id}")
    _job_dir, _snapshot_path, rec = _bind_record(
        json.loads(path.read_text()), job_id,
    )
    return path, rec


def _resume(args):
    repo = _repo()
    path, rec = _find_record(repo, args.id)
    if rec["harness"]["name"] == "agy":
        job_dir, snapshot = _bound_snapshot(rec)
        if rec["role"] == "write":
            _refuse("write-role-unadmitted", "measured agy provider attribution",
                    "provider unavailable", "launch an attributed worker")
        _effort_argv("agy", rec["model"].get("effort_requested"))
        sid = rec["session"].get("id")
        try:
            valid_id = bool(sid) and str(uuid.UUID(sid)) == sid
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        rel = Path(".gemini/antigravity-cli/brain") / str(sid) / ".system_generated/logs/transcript.jsonl"
        stores = [job_dir / "home" / name / rel for name in ("agy", "agy-stream")]
        if not valid_id or not any(p.is_file() and _inside(p.resolve(), job_dir) for p in stores):
            _refuse("resume-store", "the recorded agy conversation transcript",
                    f"{sid}: absent in live and archived home", "launch afresh")
        _verify_record_ref(snapshot, rec["snapshot"]["ref_sha"])
        iso = _module("agy_resume_isolation", HARNESS_APP / "isolation.py")
        home = job_dir / "home/agy"
        auth = home / iso.HARNESSES["agy"]["auth_files"][0]
        if not auth.is_file():
            _refuse("isolation", "the retained agy credential leaf", str(auth),
                    "restore the declared credential or launch afresh")
        rec["harness"]["isolation"]["env"] = iso.dispatch_env("agy", home)
        rec["harness"]["sandbox"] = "always-proceed"
        rec["model"].setdefault("provider", {
            "id": None, "read_from": None, "unresolved": "native provider unavailable"})
    worker = _worker(rec["harness"]["name"])
    recorded_worker = rec["harness"].get("worker")
    if recorded_worker is not None:
        for key, kind in (("name", "worker"), ("executable", "executable")):
            if recorded_worker.get(key) != worker[key]:
                _refuse(kind, f"recorded {key} {recorded_worker.get(key)!r}",
                        f"current {key} {worker[key]!r}", "launch afresh")
    rec["harness"]["worker"] = worker
    job_dir, _snapshot_path = _bound_snapshot(rec)
    prior_session = copy.deepcopy(rec.get("session") or {})
    prior_model = copy.deepcopy(rec.get("model") or {})
    prior_result = copy.deepcopy(rec.get("result") or {})
    prior_envelope = (rec.get("result") or {}).get("envelope") or {}
    preserve_cause = False
    settled_refusal = None
    if rec.get("status") == "closed" and prior_envelope.get("status") == "invalid":
        settled_refusal = {
            key: prior_envelope.get(key) for key in ("status", "verdict", "note")
        }
    if (job_dir / "TRIPPED.md").exists() and not args.reason and args.cap_out is None:
        raise RuntimeError("tripped job requires a changed cap or reason")
    if args.cap_out is not None:
        rec["caps"]["cap-out"] = args.cap_out
        rec["caps"]["source"] = "resume"
    jobs = json.loads(Path(JOBS_PATH).read_text())
    raw_path = job_dir / "raw.out"
    raw_offset = raw_path.stat().st_size if raw_path.is_file() else 0
    _run_attempt(rec, job_dir, job_caps=jobs[rec["job"]].get("caps"),
                 resume=True, reason=args.reason)
    runtime_note = rec.pop("_runtime_note", None)
    stream, ran, found_id, mismatch = _stream(
        job_dir, rec["harness"]["name"], rec["session"]["id"],
        Path(rec["snapshot"]["root"]), rec["snapshot"]["ref_sha"], rec["role"],
    )
    if rec["harness"]["name"] in {"codex", "grok"}:
        harness = rec["harness"]["name"]
        live = job_dir / "home" / harness / "sessions"
        streamed = job_dir / "home" / f"{harness}-stream" / "sessions"
        if live.exists():
            streamed.parent.mkdir(parents=True, exist_ok=True)
            if streamed.exists():
                shutil.copytree(live, streamed, dirs_exist_ok=True)
                shutil.rmtree(live)
            else:
                shutil.move(str(live), str(streamed))
            if stream is not None:
                stream = streamed / stream.relative_to(live)
                if harness == "grok":
                    portable = _portable_paths({
                        "snapshot": rec["snapshot"], "stream": str(stream),
                    })["stream"]
                    portable_stream = Path(portable)
                    if portable_stream != stream and not portable_stream.exists():
                        portable_stream.parent.mkdir(parents=True, exist_ok=True)
                        portable_stream.symlink_to(os.path.relpath(
                            stream, portable_stream.parent))
                    stream = portable_stream
    raw = raw_path.read_bytes()[raw_offset:]
    if runtime_note:
        envelope = (rec.get("result") or {}).get("envelope") or {}
        envelope.update(status="tripped" if runtime_note.startswith("trip") else "invalid",
                        verdict=None, note=runtime_note)
        rec.setdefault("result", {})["envelope"] = envelope
        rec["result"]["cause"] = _cause(rec, job_dir, envelope)
    else:
        code = rec["attempts"][-1]["exit"]
        note = f"harness-cli: exited {code}" if code else None
        prior_digest = prior_session.get("stream_sha256_at_close")
        stream_changed = bool(stream and _sha(stream) != prior_digest)
        if stream:
            rec["session"].update(
                stream=str(stream), stream_sha256_at_close=_sha(stream),
            )
            if rec["harness"]["name"] != "agy":
                _record_stream_model(rec, ran, stream)
            if rec["harness"]["name"] in {"codex", "agy"} and found_id:
                rec["session"]["id"] = found_id
        last_message = job_dir / "out" / "last-message.json"
        has_candidate = bool(_json_objects(raw.decode("utf-8", "replace")))
        if rec["harness"]["name"] == "codex" and last_message.is_file():
            has_candidate = True
        if stream_changed or has_candidate:
            rec["session"]["spend"] = _spend(rec, stream, job_dir)
        else:
            rec["session"] = prior_session
            rec["model"] = prior_model
        if mismatch:
            note = f"{note}; {mismatch}" if note else mismatch
        if has_candidate:
            env = _envelope(raw, rec["job"], rec["snapshot"]["ref_sha"], note,
                            harness=rec["harness"]["name"], job_dir=job_dir,
                            rec=rec)
            if settled_refusal is not None:
                env.update(settled_refusal)
            _retain_findings(env, prior_envelope)
            _fill_envelope_spend(rec, env)
            rec.setdefault("result", {})["envelope"] = env
            rec["result"]["cause"] = _cause(rec, job_dir, env)
        else:
            rec["result"] = prior_result
            preserve_cause = True
            if rec["role"] == "read" and note:
                env = copy.deepcopy(prior_envelope) or _invalid(
                    rec["job"], rec["snapshot"]["ref_sha"], note,
                )
                if env.get("note") != note:
                    _invalidate_envelope(env, note)
                rec.setdefault("result", {})["envelope"] = env
    if rec["harness"]["name"] == "agy":
        _record_stream_model(rec, ran, stream)
    attempt = rec["attempts"][-1]["n"]
    if rec["role"] == "read":
        envelope = (rec.get("result") or {}).get("envelope") or _invalid(
            rec["job"], rec["snapshot"]["ref_sha"],
            "resume-envelope: no prior or resumed envelope",
        )
        _settle(
            repo, path, rec, envelope,
            subject=f"dispatch: record {rec['id']} after attempt {attempt}",
            preserve_cause=preserve_cause,
        )
    else:
        _write_record(repo, rec)
        _commit_record(
            repo, path, rec,
            subject=f"dispatch: record {rec['id']} after attempt {attempt}",
        )
    return 0


def _collect(args):
    agent = _identity()
    repo = _repo()
    try:
        path, rec = _find_record(repo, args.id)
    except RuntimeError:
        _refuse("record-target", f"a record for {args.id}", "none",
                "name a launched dispatch")
    branch = _branch(repo)
    lineage = rec["lineage"]["branch"]
    if branch != lineage:
        _refuse("record-target", f"the checkout on {lineage}", branch,
                f"run from a worktree of {lineage}")
    if rec.get("role") != "write":
        _refuse("collect-role", "a write role", rec.get("role"),
                "collect only a tripped write job")
    result = rec.get("result") or {}
    if rec.get("status") != "closed":
        _refuse("collect-state", "a closed write job", rec.get("status"),
                "wait for the worker to close before collecting")
    if result.get("collected"):
        _refuse("collect-state", "an uncollected failed write job",
                "already collected", "use the recorded collected head")
    attempts = rec.get("attempts") or []
    last = attempts[-1] if attempts else {}
    residual = result.get("residual_paths") or []
    failed = any(bool(attempt.get("tripped") or attempt.get("exit"))
                 for attempt in attempts)
    committed = (not residual and
                 (result.get("envelope") or {}).get("status") in {"invalid", "tripped"})
    if not committed and (not failed or not residual):
        _refuse(
            "collect-state",
            "an uncollected write job with a failed or tripped attempt and residual paths",
            f"exit={last.get('exit')} tripped={bool(last.get('tripped'))} "
            f"residual={len(residual)}",
            "use the normal result or name a recoverable job with residue",
        )
    snapshot = Path(rec["snapshot"]["root"])
    job_dir = snapshot.parent
    _verify_record_ref(snapshot, rec["snapshot"]["ref_sha"])
    before = _git(snapshot, "rev-parse", "HEAD").stdout.strip()
    ref_sha = rec["snapshot"]["ref_sha"]
    if committed and _git(snapshot, "cat-file", "-e", f"{before}^{{commit}}",
                          check=False).returncode:
        _refuse("collect-state", "an available recorded candidate commit", before,
                "restore the preserved commit object before verification")
    if _git(snapshot, "merge-base", "--is-ancestor", ref_sha, before,
            check=False).returncode:
        _refuse("off-lineage-head", f"HEAD to descend from {ref_sha}",
                before, "restore the snapshot to the dispatched lineage")
    def check_committed():
        head = _git(snapshot, "rev-parse", "HEAD", check=False)
        delta = _git(snapshot, "diff", "--name-only", ref_sha, before, "--", check=False)
        paths = delta.stdout.splitlines()
        if (head.returncode or delta.returncode or head.stdout.strip() != before
                or before != result.get("head") or before == ref_sha
                or not paths or paths != result.get("changed_paths")
                or _residual(snapshot)):
            _refuse("collect-state", "the exact recorded nonempty committed candidate without residue",
                    "unavailable, changed or mismatched candidate",
                    "restore the preserved candidate before explicit verification")
    if committed:
        check_committed()
    recorded_timeout = float(
        (rec.get("caps") or {}).get("timeout")
        or _lane_values("WALL_DEFAULT")["WALL_DEFAULT"]
    )
    timeout = max(recorded_timeout, args.timeout or recorded_timeout)
    verify_home = job_dir / "home" / "verify"
    verify_home.mkdir(parents=True, exist_ok=True)
    verify_base = {
        "HOME": str(verify_home),
        "PATH": os.environ.get("PATH", os.defpath),
    }
    try:
        verified = subprocess.run(
            ["bash", "-c", args.verify],
            cwd=snapshot,
            env=_child_env(verify_base, {}, rec["id"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        tail = (exc.stderr or "")[-200:]
        _refuse("verify", f"{args.verify!r} to exit 0 within {timeout:g}s",
                f"timeout; stderr tail {tail!r}",
                "fix the residual tree or pass a verification that completes")
    if verified.returncode:
        _refuse("verify", f"{args.verify!r} to exit 0",
                f"exit {verified.returncode}; stderr tail {verified.stderr[-200:]!r}",
                "fix the residual tree and rerun collect")
    envelope = result.get("envelope") or {}
    if committed:
        check_committed()
    else:
        _commit_for_harness(snapshot, rec, envelope, collected=True)
    head = _git(snapshot, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "-c", "core.logAllRefUpdates=always", "fetch",
         "--no-write-fetch-head", str(snapshot),
         f"{head}:refs/dispatch/{rec['id']}")
    result["head"] = head
    result["changed_paths"] = _git(
        snapshot, "diff", "--name-only", rec["snapshot"]["ref_sha"], head, "--",
    ).stdout.splitlines()
    result["residual_paths"] = _residual(snapshot)
    result["collected"] = {
        "at": _now(), "by": agent, "head": head,
        "verify": {"command": args.verify, "exit": 0},
        "reason": args.reason,
    }
    rec["result"] = result
    rec["status"] = "closed"
    _write_record(repo, rec)
    _commit_record(
        repo, path, rec,
        subject=f"dispatch: record {rec['id']} after collection",
    )
    return 0


def _wait(args):
    stdout, stderr = sys.stdout, sys.stderr
    repo = _repo()
    job_dir, _snapshot_path = _job_paths(args.id)
    record_path = repo / ".dev/records/dispatches" / f"{args.id}.json"
    if not record_path.is_file():
        _refuse("wait-target", f"a record for {args.id}", "none",
                "name a launched dispatch")
    started = monotonic()
    while True:
        elapsed = monotonic() - started
        state = _job_state(job_dir)
        print(f"poll {args.id} {state} elapsed={elapsed:.1f}s",
              file=stderr, flush=True)
        try:
            rec = _absolute_paths(json.loads(record_path.read_text()))
        except (OSError, json.JSONDecodeError):
            rec = {}
        if rec.get("status") in {"closed", "died"}:
            attempt = ((rec.get("attempts") or [{}])[-1])
            envelope = ((rec.get("result") or {}).get("envelope") or {})
            tripped = bool(attempt.get("tripped"))
            code = attempt.get("exit")
            code = -1 if code is None else code
            print(f"{args.id} {rec['status']} attempt={attempt.get('n', 0)} "
                  f"exit={code} tripped={str(tripped).lower()} "
                  f"envelope={envelope.get('status', 'invalid')}",
                  file=stdout)
            clean = (rec["status"] == "closed" and code == 0
                     and not tripped and envelope.get("status") == "ok")
            return 0 if clean else 1
        if elapsed >= args.timeout:
            return 2
        sleep(min(args.interval, max(0.0, args.timeout - elapsed)))


def _close(args):
    repo = _repo()
    _job_paths(args.id)
    preflight_path = repo / ".dev/records/dispatches" / f"{args.id}.json"
    if not preflight_path.is_file():
        raise RuntimeError(f"record not found: {args.id}")
    preflight = json.loads(preflight_path.read_text())
    branch = _branch(repo)
    lineage = (preflight.get("lineage") or {}).get("branch")
    if branch != lineage:
        _refuse("record-target", f"the checkout on {lineage}", branch,
                f"run from a worktree of {lineage}")
    path, rec = _find_record(repo, args.id)
    # Records launched before effort_argv joined the wire remain closable.
    rec["model"].setdefault(
        "effort_argv",
        _effort_argv(rec["harness"]["name"], rec["model"].get("effort_requested")),
    )
    job_dir, _snapshot_path = _bound_snapshot(rec)
    try:
        state = _attempt_state(job_dir, required=True)
        if type(state.get("pid")) is not int or state["pid"] <= 0:
            raise ValueError("pid must be a positive integer")
    except (OSError, ValueError, TypeError) as exc:
        _refuse("state", "valid attempt state with pid and raw_offset", str(exc),
                "repair state.json from retained attempt evidence")
    raw_offset = state.get("raw_offset", 0)
    try:
        os.kill(int(state["pid"]), 0)
        died = False
    except OverflowError as exc:
        _refuse("state", "a representable process id", str(exc),
                "repair state.json from retained attempt evidence")
    except (OSError, TypeError, ValueError):
        died = not (job_dir / "exit").exists()
    if died:
        raw_path = job_dir / "raw.out"
        raw = raw_path.read_bytes()[raw_offset:] if raw_path.is_file() else b""
        env = _envelope(raw, rec["job"], rec["snapshot"]["ref_sha"],
                        "DIED: process vanished without exit",
                        harness=rec["harness"]["name"], job_dir=job_dir,
                        rec=rec)
        rec["result"] = {"head": rec["snapshot"]["ref_sha"], "changed_paths": [],
                         "residual_paths": [], "envelope": env}
        rec["status"] = "died"
        rec["at"]["closed"] = _now()
        _write_record(repo, rec)
        _commit_record(repo, path, rec)
        return 0
    if (job_dir / "exit").exists() and not rec.get("result"):
        # The harness finished and wrote its exit; the launcher did not get
        # to collect (it crashed on 2026-08-28 parsing a string stamp). The
        # output is still in raw.out, so collect it now rather than lose it.
        code = int((job_dir / "exit").read_text().strip() or 0)
        raw = (job_dir / "raw.out").read_bytes()[raw_offset:]
        note = f"harness-cli: exited {code}" if code else None
        if (job_dir / "TRIPPED.md").exists():
            note = "trip: battery tripped"
        stream, ran, found_id, mismatch = _stream(
            job_dir, rec["harness"]["name"], rec["session"]["id"],
            Path(rec["snapshot"]["root"]), rec["snapshot"]["ref_sha"], rec["role"],
        )
        if stream:
            rec["session"].update(
                stream=str(stream), stream_sha256_at_close=_sha(stream),
            )
            if rec["harness"]["name"] in {"codex", "agy"} and found_id:
                rec["session"]["id"] = found_id
        _record_stream_model(rec, ran, stream)
        if rec["harness"]["name"] == "agy":
            rec["session"]["spend"] = _spend(rec, stream, job_dir)
        if mismatch:
            note = f"{note}; {mismatch}" if note else mismatch
        env = _envelope(raw, rec["job"], rec["snapshot"]["ref_sha"], note,
                        harness=rec["harness"]["name"], job_dir=job_dir,
                        rec=rec)
        if not rec["attempts"]:
            rec["attempts"].append({"n": 1, "launched": rec["at"]["launched"],
                                    "ended": _now(), "exit": code,
                                    "tripped": (job_dir / "TRIPPED.md").exists()})
        _settle(repo, path, rec, env)
    return 0


def _brief(args):
    repo = _repo()
    _path, rec = _find_record(repo, args.check)
    job_dir, _snapshot_path = _bound_snapshot(rec)
    _verify_graft(rec, job_dir, _snapshot_path)
    prompt = job_dir / "prompt.txt"
    ok = prompt.is_file() and _sha(prompt) == rec["brief"]["sha256"]
    print("brief matches" if ok else "brief digest mismatch")
    return 0 if ok else 1


def _parser():
    p = argparse.ArgumentParser(prog="launch.py")
    sub = p.add_subparsers(dest="verb")
    s = sub.add_parser("status")
    s.add_argument("id", nargs="?")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("resume")
    r.add_argument("id")
    r.add_argument("--prompt-file")
    r.add_argument("--reason")
    r.add_argument("--cap-out", type=int)
    c = sub.add_parser("close")
    c.add_argument("id")
    collect = sub.add_parser("collect")
    collect.add_argument("id")
    collect.add_argument("--verify", required=True)
    collect.add_argument("--reason")
    collect.add_argument("--timeout", type=float)
    wait = sub.add_parser("wait")
    wait.add_argument("id")
    wait.add_argument("--timeout", type=float, default=3600)
    wait.add_argument("--interval", type=float, default=5)
    b = sub.add_parser("brief")
    b.add_argument("--check", required=True)
    return p


def _launch_parser():
    p = argparse.ArgumentParser(prog="launch.py")
    p.add_argument("job")
    p.add_argument("--harness", required=True)
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--ref", required=True)
    p.add_argument("--lineage")
    p.add_argument("--unit")
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--scope")
    p.add_argument("--input", action="append", default=[])
    p.add_argument("--follows", action="append", default=[])
    # The assignment this dispatch is work on, if it is work on
    # one. Naming it binds the worker to it; leaving it out is a
    # dispatch with no assignment, which most reviews are, and
    # changes nothing.
    p.add_argument("--for", dest="assignment")
    p.add_argument("--answers", action="append", default=[])
    p.add_argument("--override", action="append", default=[])
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] and argv[0] in {
                "status", "resume", "close", "collect", "wait", "brief"}:
            args = _parser().parse_args(argv)
            return {"status": _status, "resume": _resume, "close": _close,
                    "collect": _collect, "wait": _wait,
                    "brief": _brief}[args.verb](args)
        return _launch(_launch_parser().parse_args(argv))
    except CORRECTION.Refusal as exc:
        print(exc.line(), file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"launch.py: refusal: {exc}", file=sys.stderr)
        return REFUSAL
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"launch.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
