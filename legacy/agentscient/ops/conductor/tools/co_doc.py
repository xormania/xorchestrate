#!/usr/bin/env python3
"""Read-only probes for the conductor permission surface.

Inherits ops/conductor/instrument-conventions.md for the wire: an
argument it does not know refuses class `arguments`, one line on
stderr, exit 2, stdout empty (owner, 2026-09-13, ruling 2). The report
is the tool's outcome, not a refusal, and keeps its shape.
"""

import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import IDENTITY, Refusal, emit, refuse, state_root
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

PROBES = (
    "P-SET-1", "P-PERM-1", "P-PERM-2", "P-HOOK-1", "P-HOOK-2",
    "P-LEV-1", "P-ID-1", "P-WF-1", "P-DB-1",
)
ROOT = Path(__file__).resolve().parents[3]
SETTINGS = (
    ROOT / ".claude" / "settings.json",
    ROOT / ".claude" / "settings.local.json",
    Path.home() / ".claude" / "settings.json",
)
ABSOLUTE = re.compile(r"(?<![\w$])(/[^\s)'\";,]+)")


class Report:
    def __init__(self):
        self.lines = []
        self.notes = []
        self.invalid = False

    def add(self, kind, probe, expected, found, remedy, invalid=False):
        self.lines.append(
            f"[{kind}] {probe}: expected {expected} / found {found}"
            f" — remedy: {remedy}"
        )
        self.invalid |= invalid

    def invalid_probe(self, probe, expected, found, remedy):
        self.add("Abort", probe, expected, f"INVALID: {found}", remedy, True)

    def note(self, probe, text):
        self.notes.append(f"{probe}: {text}")


def load_settings(report):
    loaded = []
    for path in SETTINGS:
        if not path.exists():
            report.add("Finding", "P-SET-1", f"a JSON object at {path}",
                       "ABSENT", f"create {path} if this settings layer is intended")
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                value = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            report.invalid_probe(
                "P-SET-1", f"a readable JSON object at {path}",
                f"{path}: {exc}",
                "restore the timestamped backup beside it or fix the JSON",
            )
            continue
        if not isinstance(value, dict):
            report.invalid_probe(
                "P-SET-1", f"a JSON object at {path}",
                f"{path} contains {type(value).__name__}",
                "replace the top-level value with a JSON object",
            )
            continue
        loaded.append((path, value))
    return loaded


def permission_rules(report, loaded):
    allows = []
    denies = []
    for path, settings in loaded:
        permissions = settings.get("permissions", {})
        if permissions is None:
            continue
        if not isinstance(permissions, dict):
            report.invalid_probe("P-PERM-1", f"permissions object in {path}",
                                 type(permissions).__name__,
                                 "make permissions a JSON object")
            continue
        for key, target in (("allow", allows), ("deny", denies)):
            rules = permissions.get(key, [])
            if not isinstance(rules, list) or any(not isinstance(x, str) for x in rules):
                report.invalid_probe(
                    "P-PERM-1" if key == "allow" else "P-PERM-2",
                    f"permissions.{key} as a list of strings in {path}",
                    repr(rules), f"make permissions.{key} a list of rule strings",
                )
                continue
            target.extend((path, rule) for rule in rules)

    for source, rule in allows:
        for raw in ABSOLUTE.findall(rule):
            text = raw.split(":", 1)[0].rstrip("*}")
            candidate = Path(text)
            if not candidate.exists():
                report.add(
                    "Denial", "P-PERM-1", f"absolute allow target {candidate} to exist",
                    f"inert grant {rule!r} in {source}",
                    "repoint the rule at the resolving path or complete the step that makes it resolve; never widen",
                )

    for source, allow in allows:
        allow_paths = {p.split(":", 1)[0].rstrip("*}") for p in ABSOLUTE.findall(allow)}
        for deny_source, deny in denies:
            if any(path and path in deny for path in allow_paths) or allow == deny:
                report.add(
                    "Denial", "P-PERM-2", "no deny rule to textually shadow an allow target",
                    f"heuristic match: allow {allow!r} ({source}) and deny {deny!r} ({deny_source})",
                    "narrow or remove the conflicting rule after owner review; do not widen the allow",
                )


def hook_entries(report, loaded):
    entries = []
    for path, settings in loaded:
        hooks = settings.get("hooks", {})
        if hooks is None:
            continue
        if not isinstance(hooks, dict):
            report.invalid_probe("P-HOOK-1", f"hooks object in {path}",
                                 type(hooks).__name__, "make hooks a JSON object")
            continue
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                report.invalid_probe("P-HOOK-1", f"hook groups list for {event} in {path}",
                                     repr(groups), "make the event value a list of hook groups")
                continue
            for number, group in enumerate(groups):
                if not isinstance(group, dict):
                    report.invalid_probe("P-HOOK-1", f"hook group object for {event} in {path}",
                                         repr(group), "make each hook group a JSON object")
                    continue
                label = f"{path}:{event}[{number}]"
                matcher = group.get("matcher")
                trusted = getattr(report, "shared_hook_helper", None)
                shared = trusted and any(command_file(str(h.get("command", ""))) == Path(trusted) for h in group.get("hooks", []) if isinstance(h, dict))
                if (not isinstance(matcher, str) or not matcher.strip() or matcher.strip() in {".*", "*"}) and not shared:
                    report.add("Denial", "P-HOOK-2", "a matcher naming the tools it judges",
                               f"broad or absent matcher at {label}: {matcher!r}",
                               "scope the matcher to named tools; move general policy into permissions")
                configured = group.get("hooks")
                if not isinstance(configured, list):
                    report.invalid_probe("P-HOOK-1", f"hooks list at {label}", repr(configured),
                                         "make hooks a list of configured hook objects")
                    continue
                entries.extend((label, hook) for hook in configured)
    return entries


def command_file(command):
    expanded = command.replace("$CLAUDE_PROJECT_DIR", str(ROOT))
    expanded = os.path.expanduser(expanded)
    words = shlex.split(expanded)
    interpreters = {"bash", "sh", "python", "python3", "ruby", "node", "perl"}
    index = 0
    while index < len(words) and "=" in words[index] and not words[index].startswith("/"):
        index += 1
    if index < len(words) and Path(words[index]).name in interpreters:
        index += 1
        while index < len(words) and words[index].startswith("-"):
            index += 1
    if index >= len(words):
        raise ValueError("command has no command file")
    return Path(words[index])


def hooks(report, loaded):
    for label, hook in hook_entries(report, loaded):
        if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
            report.invalid_probe("P-HOOK-1", f"command hook object at {label}", repr(hook),
                                 "give each command hook a string command")
            continue
        try:
            path = command_file(hook["command"])
        except (ValueError, OSError) as exc:
            report.invalid_probe("P-HOOK-1", f"parseable command at {label}", str(exc),
                                 "replace the hook command with an executable command file")
            continue
        if not path.is_file() or not os.access(path, os.X_OK):
            report.add("Abort", "P-HOOK-1", f"executable hook command file {path}",
                       "ABSENT" if not path.exists() else "not an executable file",
                       "remove the entry or restore the executable file")


def parse_conf(path):
    values = {}
    with path.open(encoding="utf-8") as stream:
        for number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if not match:
                raise ValueError(f"line {number} is not a shell assignment")
            words = shlex.split(match.group(2), comments=True)
            if len(words) != 1:
                raise ValueError(f"line {number} does not assign exactly one value")
            values[match.group(1)] = words[0]
    return values


def levers(report):
    tools = ROOT / "ops" / "conductor" / "tools"
    conf = tools / "dispatch.conf"
    if not conf.exists():
        report.add("Abort", "P-LEV-1", f"parseable dispatch configuration at {conf}",
                   "ABSENT", f"restore {conf}")
        values = {}
    else:
        try:
            values = parse_conf(conf)
        except (OSError, UnicodeError, ValueError) as exc:
            report.invalid_probe("P-LEV-1", f"parseable dispatch configuration at {conf}",
                                 str(exc), "fix the shell assignments in dispatch.conf")
            values = {}
    launcher = values.get("LAUNCHER_PIN")
    if launcher:
        paths = (("LAUNCHER_PIN", launcher),)
    else:
        sibling = (tools / "../../devlane/dispatch/launch.py").resolve()
        if sibling.is_file():
            report.note("P-LEV-1", f"live launcher at {sibling} (sibling default)")
        else:
            report.add("Abort", "P-LEV-1", f"live launcher at {sibling} (sibling default)",
                       "inert: path is absent",
                       "restore the sibling launcher or define LAUNCHER_PIN in dispatch.conf")
        paths = ()
    paths += (("dispatch.sh", str(tools / "dispatch.sh")),
             ("gate.sh", str(tools / "gate.sh")))
    for name, text in paths:
        if not text:
            report.add("Abort", "P-LEV-1", f"live {name} path examined via {conf}",
                       "unset", f"define {name} in {conf}")
            continue
        path = Path(os.path.expanduser(text))
        if not path.is_file():
            report.add("Abort", "P-LEV-1", f"live {name} at {path}", "inert: path is absent",
                       "repoint it at the measured current target and record the measurement")


def identity_probe(report):
    agent = os.environ.get("WF_AGENT")
    if agent is None:
        report.add("Finding", "P-ID-1", "WF_AGENT in Name <address> form when recording",
                   "unset (reads need no identity)",
                   "export WF_AGENT='Your Model Name <noreply@vendor>' before recording")
    elif IDENTITY.fullmatch(agent) is None:
        report.add("Rejection", "P-ID-1", "WF_AGENT in Name <address> form",
                   repr(agent), "export WF_AGENT='Your Model Name <noreply@vendor>'")


def workflow(report):
    wf = ROOT / "ops" / "devlane" / "workflow" / "wf.py"
    if not wf.is_file():
        report.add("Abort", "P-WF-1", f"wf.py at {wf} whose --help exits 0", "ABSENT",
                   "restore wf.py at the examined path")
        return
    try:
        result = subprocess.run((sys.executable, str(wf), "--help"),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False)
    except OSError as exc:
        report.invalid_probe("P-WF-1", f"execution of {wf} --help", str(exc),
                             "restore a runnable Python interpreter and wf.py")
    else:
        if result.returncode != 0:
            report.add("Abort", "P-WF-1", f"{wf} --help to exit 0",
                       f"exit {result.returncode}", "repair wf.py so its help path runs")


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def stale_grants(connection):
    instant = datetime.now(timezone.utc)
    now = instant.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = []
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns = [row[1] for row in connection.execute(
            f"PRAGMA table_info({quote_identifier(table)})")]
        # The two expiry schemas are independent branches: a table carrying
        # only ts + ttl_s (handwork's shape) must still reach the TTL scan.
        if "expires_at" in columns:
            status = next((c for c in ("status", "state", "settled_at", "closed_at") if c in columns), None)
            name = next((c for c in ("name", "grant", "grant_id", "id", "wo_id", "agent") if c in columns), None)
            selected = [name or "rowid", "expires_at"] + ([status] if status else [])
            query = "SELECT " + ",".join(quote_identifier(c) if c != "rowid" else c for c in selected)
            query += f" FROM {quote_identifier(table)} WHERE expires_at < ?"
            for row in connection.execute(query, (now,)):
                if status and row[2] not in (None, "", "open", "active", "granted"):
                    continue
                stale.append(f"{table}:{row[0]} (expired {row[1]})")

        # handwork's grants express their deadline as ts + ttl_s rather than
        # storing expires_at.  Settled rows are history, not open grants.
        if {"ts", "ttl_s", "status"}.issubset(columns):
            name = next((c for c in ("id", "name", "agent", "session") if c in columns), "rowid")
            query = ("SELECT " + (quote_identifier(name) if name != "rowid" else name)
                     + f",ts,ttl_s,status FROM {quote_identifier(table)}"
                     + " WHERE ttl_s IS NOT NULL")
            for identifier, timestamp, ttl, status in connection.execute(query):
                if status not in (None, "", "open", "active", "granted"):
                    continue
                try:
                    opened = datetime.fromisoformat(str(timestamp))
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    expires = opened + timedelta(seconds=int(ttl))
                except (TypeError, ValueError, OverflowError) as exc:
                    raise sqlite3.DataError(
                        f"cannot interpret TTL for {table}:{identifier}: {exc}") from exc
                if expires < instant:
                    stale.append(f"{table}:{identifier} (expired {expires.strftime('%Y-%m-%dT%H:%M:%SZ')})")
    return stale


def ledgers(report):
    try:
        state = state_root()
    except Refusal as error:
        report.invalid_probe("P-DB-1", "a validated authority state root", str(error),
                             "repair the native profile before reading authority ledgers")
        return
    for path in (state / "agentscient" / "skills.db",):
        if not path.exists():
            report.add("Finding", "P-DB-1", f"read-only ledger at {path}", "ABSENT",
                       f"initialize {path} with its owning tool before relying on the ledger")
            continue
        try:
            connection = instrument.connect_store(path)
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("SELECT 1").fetchone()
                stale = stale_grants(connection)
            finally:
                connection.close()
        except sqlite3.Error as exc:
            report.invalid_probe("P-DB-1", f"read-only SQLite ledger at {path}", str(exc),
                                 "restore a readable, parseable ledger database")
            continue
        if stale:
            report.add("Finding", "P-DB-1", f"zero open grants past TTL in {path}",
                       f"{len(stale)} stuck grant(s): {', '.join(stale)}",
                       "settle or expire them with reason")


def main():
    try:
        return probe(sys.argv[1:])
    except Refusal as refusal:
        return emit(refusal)


def probe(argv):
    if argv == ["--list-probes"]:
        print("\n".join(PROBES))
        return 0
    if argv:
        refuse("arguments", "no argument, or --list-probes", shlex.join(argv),
               "run co_doc.py alone, or with --list-probes")
    report = Report()
    loaded = load_settings(report)
    permission_rules(report, loaded)
    shared_context(report)
    hooks(report, loaded)
    control_context(report)
    levers(report)
    identity_probe(report)
    workflow(report)
    ledgers(report)
    print("\n".join((*report.notes, *report.lines)))
    return 2 if report.invalid else (1 if report.lines else 0)


def shared_context(report):
    """Inspect through the trusted profile validator; never launch or activate."""
    selected = os.environ.get("AGENTSCIENT_HOOK_PROFILE")
    if not selected:
        report.note("P-HOOK-1", "shared native profile ABSENT; no active projection supplied")
        return
    try:
        data = json.loads(Path(selected).read_text())
        if data["schema"] != "shared-hook-profile/1" or data["harness"] not in {"claude", "codex", "grok", "agy"}:
            raise ValueError("unknown profile")
        if Path(data["source"]).resolve() != ROOT:
            raise ValueError("profile points outside this trusted installation")
        path = ROOT / "ops/devlane/hooks/profiles.py"
        spec = importlib.util.spec_from_file_location("doctor_hook_profiles", path)
        profiles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(profiles)
        profiles.validate(data)  # read-only, from this trusted installation
        report.shared_hook_helper = data["helper"]
        report.note("P-HOOK-1", f"{data['harness']} {data['mode']} shared profile bytes match; native interception and mandatory launch are separate evidence")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        report.invalid_probe("P-HOOK-1", "a complete pinned shared hook projection",
                             "missing, malformed or changed native profile",
                             "regenerate the reviewed profile and remeasure native interception before activation")


def control_context(report):
    """Inspect the new gate without importing or executing that gate."""
    selected = os.environ.get("CONTROL_STATE")
    if not selected:
        report.note("P-HOOK-1", "conductor control context ABSENT; no active control state was supplied")
        return
    state = Path(selected)
    try:
        data = json.loads((state / "state.json").read_text())
        for name, expected in data["pins"].items():
            actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
            if actual != expected:
                report.add("Denial", "P-HOOK-1", "pinned conductor controller and hook bytes",
                           "changed binding", "restore the pinned installation or have the owner initialize a new reviewed cycle")
                return
        raw = (state / "plan.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != data["plan_sha256"]:
            report.add("Denial", "P-HOOK-1", "unchanged owner plan", "changed retained plan",
                       "restore the retained owner declaration; do not refill the current cycle")
            return
        report.note("P-HOOK-1", "conductor control bindings verified; inspect control.py status for action outcomes and remaining allowance")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        report.invalid_probe("P-HOOK-1", "readable conductor control context", "missing or malformed state or pinned file",
                             "restore the control installation and inspect the owner state directory")


if __name__ == "__main__":
    sys.exit(main())
