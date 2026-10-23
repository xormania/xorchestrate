"""The shared instrument module: one wire, one identity, one clock, one
bounded call, and the gate that keeps the tools from growing copies.

Contract: `ops/conductor/instrument-conventions.md` §The shared
implementation. The module `ops/conductor/tools/instrument.py` is the
subject. Its functions are loaded from the file and exercised directly;
its CLI verbs are run as a subprocess under a scratch HOME.

Written by hand under grant 176 (owner, 2026-09-12: "agreed - hand grant
ok'd for #1, #2, #3, and #4"), one producer (Claude Fable 5.1), the
second producer absent by the owner's word (AGENTS.md §Ground rules).
Red: this file against a tree with no `instrument.py` fails
`test_the_module_file_is_present` and errors every load; green with the
same command once the module lands.

The consolidation gate at the end is the measured red of every later
migration: a tool joins MIGRATED before its private helpers go, and the
gate fails until they are gone. A tool is in exactly one of MIGRATED or
PENDING, so a tool nobody has placed is a failure naming it, never a
silent pass.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
MODULE = TOOLS / "instrument.py"
AGENT = "Claude Fable 5.1 <noreply@anthropic.com>"

# Owner, 2026-09-13 ("minimize parallel work conflicts"): the three
# placements were sorted tuples here, and every session that migrated a
# tool inserted into the same lines. Each tool now declares its placement
# in its own file, ops/conductor/placement/<tool>.py.json, as
# {"placement": "migrated" | "pending" | "no-wire"}: a new tool adds a
# file, a migration edits one, and no two sessions touch the same line.
# The gate closes both ways: a tool with no file is a failure naming the
# tool, a file with no tool a failure naming the file, a file with any
# other placement a failure naming the field.
PLACEMENT_DIR = REPO / "ops" / "conductor" / "placement"
PLACEMENTS = ("migrated", "pending", "no-wire", "shared")


def placements() -> dict[str, list[str]]:
    """The declared placement of every tool, read from the directory:
    migrated (imports the shared module, carries none of its helpers),
    pending (still carrying private copies, named so the absence is a
    declaration), no-wire (returns outcomes, never refuses, nothing to
    import), shared (the module the others import, which defines those
    helpers rather than importing them). Malformed files are reported by
    the gate's closure test."""
    found: dict[str, list[str]] = {k: [] for k in PLACEMENTS}
    if PLACEMENT_DIR.is_dir():
        for path in sorted(PLACEMENT_DIR.glob("*.py.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            place = data.get("placement") if isinstance(data, dict) else None
            if place in found:
                found[place].append(path.name[: -len(".json")])
    return found


PLACED = placements()
MIGRATED: tuple[str, ...] = tuple(PLACED["migrated"])
PENDING: tuple[str, ...] = tuple(PLACED["pending"])
NO_WIRE: tuple[str, ...] = tuple(PLACED["no-wire"])
SHARED: tuple[str, ...] = tuple(PLACED["shared"])

# A migrated or no-wire tool defines none of these at module level.
PRIVATE_NAMES = frozenset({
    "refuse", "Refused", "Refusal", "identity", "IDENTITY", "IDENTITY_RE",
    "AGENT_FORM", "now", "stamp", "parse_stamp", "lane_name", "conf_value",
    "state_root",
})


def load(path=MODULE):
    spec = importlib.util.spec_from_file_location("instrument_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TheModuleIsPresent(unittest.TestCase):
    def test_the_module_file_is_present(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")


class TheRefusalWire(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-wire-")
        self.addCleanup(self.world.close)
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def test_refuse_raises_a_refusal_whose_text_is_the_wire_line(self):
        with self.assertRaises(self.m.Refusal) as caught:
            self.m.refuse("store", "a store at X", "ABSENT", "seed it")
        self.assertEqual(
            str(caught.exception), "store: expected a store at X; found ABSENT; needed seed it"
        )
        self.assertEqual(caught.exception.kind, "store")
        self.assertEqual(caught.exception.line(), str(caught.exception))

    def test_multi_line_fields_collapse_to_one_line(self):
        with self.assertRaises(self.m.Refusal) as caught:
            self.m.refuse("git-error", "a clean\n  tree", "two\nlines\n", "  fix  it ")
        line = str(caught.exception)
        self.assertNotIn("\n", line)
        self.assertEqual(
            line, "git-error: expected a clean tree; found two lines; needed fix it"
        )

    def test_emit_prints_one_stderr_line_and_returns_the_rejection_code(self):
        err = io.StringIO()
        with self.assertRaises(self.m.Refusal) as caught:
            self.m.refuse("identity", "x", "y", "z")
        with redirect_stderr(err):
            code = self.m.emit(caught.exception)
        self.assertEqual(code, 2)
        self.assertEqual(code, self.m.EXIT_REJECTION)
        self.assertEqual(err.getvalue(), "identity: expected x; found y; needed z\n")

    def test_the_four_classes_are_the_wire_table(self):
        self.assertEqual(
            (self.m.EXIT_DONE, self.m.EXIT_FINDING, self.m.EXIT_REJECTION, self.m.EXIT_ABORT),
            (0, 1, 2, 3),
        )


class Identity(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-identity-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def refusal_for(self, value):
        env = {k: v for k, v in os.environ.items() if k != "WF_AGENT"}
        if value is not None:
            env["WF_AGENT"] = value
        with (
            mock.patch.dict(os.environ, env, clear=True),
            self.assertRaises(self.m.Refusal) as caught,
        ):
            self.m.identity()
        return caught.exception

    def test_unset_refuses_identity_and_says_unset(self):
        refusal = self.refusal_for(None)
        self.assertEqual(refusal.kind, "identity")
        self.assertIn("found unset", str(refusal))

    def test_a_bare_name_refuses_identity(self):
        self.assertEqual(self.refusal_for("JustAName").kind, "identity")

    def test_an_address_without_a_domain_refuses_identity(self):
        self.assertEqual(self.refusal_for("Name <noreply>").kind, "identity")

    def test_the_forms_in_use_pass_and_are_returned_whole(self):
        for value in (
            AGENT,
            "Grok 4.6 <noreply@x.ai>",
            "GPT-5 Codex <noreply@openai.com>",
            "Lane Owner <lane-owner@example.invalid>",
        ):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"WF_AGENT": value}):
                self.assertEqual(self.m.identity(), value)

    def test_identity_parts_splits_name_and_address(self):
        with mock.patch.dict(os.environ, {"WF_AGENT": AGENT}):
            self.assertEqual(
                self.m.identity_parts(), ("Claude Fable 5.1", "noreply@anthropic.com")
            )


class TheIdentityOverride(unittest.TestCase):
    """A deliberate identity for one command outranks the ambient one.

    Measured 2026-09-16: .claude/settings.local.json binds
    env.WF_AGENT for every session on this host, so WF_AGENT is always
    set and a tool cannot tell "meant, on this command" from "inherited
    from settings" — they arrive as the same key. Without a second key
    no later branch of the precedence can ever fire, and every row a
    session writes carries whichever name the settings file holds.
    """

    OTHER = "Claude Opus 5 (1M context) <noreply@anthropic.com>"

    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-override-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def with_env(self, **keys):
        env = {k: v for k, v in os.environ.items()
               if k not in ("WF_AGENT", "WF_AGENT_OVERRIDE")}
        env.update({k: v for k, v in keys.items() if v is not None})
        return mock.patch.dict(os.environ, env, clear=True)

    def test_the_override_is_the_identity_when_both_are_set(self):
        with self.with_env(WF_AGENT=AGENT, WF_AGENT_OVERRIDE=self.OTHER):
            self.assertEqual(self.m.identity(), self.OTHER)

    def test_the_override_alone_is_the_identity(self):
        with self.with_env(WF_AGENT_OVERRIDE=self.OTHER):
            self.assertEqual(self.m.identity(), self.OTHER)

    def test_the_ambient_agent_still_answers_when_no_override_is_set(self):
        with self.with_env(WF_AGENT=AGENT):
            self.assertEqual(self.m.identity(), AGENT)

    def test_a_malformed_override_refuses_and_never_falls_through(self):
        # Falling back to the ambient name would stamp rows with someone
        # else while the command said otherwise. A deliberate value that
        # is wrong is not an absent one.
        with self.with_env(WF_AGENT=AGENT, WF_AGENT_OVERRIDE="JustAName"):
            with self.assertRaises(self.m.Refusal) as caught:
                self.m.identity()
        self.assertEqual(caught.exception.kind, "identity")
        self.assertIn("WF_AGENT_OVERRIDE", str(caught.exception))

    def test_a_blank_override_is_not_a_declaration(self):
        with self.with_env(WF_AGENT=AGENT, WF_AGENT_OVERRIDE="   "):
            self.assertEqual(self.m.identity(), AGENT)

    def test_identity_parts_reads_the_override_too(self):
        with self.with_env(WF_AGENT=AGENT, WF_AGENT_OVERRIDE=self.OTHER):
            self.assertEqual(
                self.m.identity_parts(),
                ("Claude Opus 5 (1M context)", "noreply@anthropic.com"),
            )


class TheSessionId(unittest.TestCase):
    """Who the harness says is running, or a declared absence.

    LD-50, measured 2026-09-16 at bbdcc651: all 251 handwork rows carried
    session `unknown` and all 506 skill events `unrecorded`, because the
    tools read CLAUDE_SESSION_ID and this harness sets
    CLAUDE_CODE_SESSION_ID. That is the absence discipline failing at its
    own name: a default that reads as an answer, printed by a reader
    looking at a key nothing sets.
    """

    KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
            "CODEX_THREAD_ID", "CODEX_SESSION_ID")

    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-session-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def session_for(self, default=None, **keys):
        env = {k: v for k, v in os.environ.items() if k not in self.KEYS}
        env.update({k: v for k, v in keys.items() if v is not None})
        with mock.patch.dict(os.environ, env, clear=True):
            return self.m.session() if default is None else self.m.session(default)

    def test_the_harness_key_is_the_one_read(self):
        self.assertEqual(
            self.session_for(CLAUDE_CODE_SESSION_ID="63a377bf-6a5b"), "63a377bf-6a5b"
        )

    def test_the_older_key_still_answers(self):
        self.assertEqual(self.session_for(CLAUDE_SESSION_ID="older-1"), "older-1")

    def test_the_harness_key_wins_over_the_older_one(self):
        self.assertEqual(
            self.session_for(CLAUDE_CODE_SESSION_ID="live", CLAUDE_SESSION_ID="stale"),
            "live",
        )

    def test_neither_set_reads_unknown_and_the_caller_may_name_its_own(self):
        self.assertEqual(self.session_for(), "unknown")
        self.assertEqual(self.session_for(default="unrecorded"), "unrecorded")

    def test_a_blank_value_is_not_a_declaration(self):
        self.assertEqual(
            self.session_for(CLAUDE_CODE_SESSION_ID="", CLAUDE_SESSION_ID="named"),
            "named",
        )
        self.assertEqual(self.session_for(CLAUDE_CODE_SESSION_ID="   "), "unknown")


class TheSessionIdentityStore(unittest.TestCase):
    """A session may bind its own name, and that outranks the ambient one.

    The ambient WF_AGENT is bound by a settings file for every session on
    this host, so without a per-session identity every row carries one
    name whoever wrote it. The store is where a session says who it is.
    The override still beats it, because a command may always say
    otherwise; the ambient key is beneath both, because it is the one
    nobody chose.
    """

    OTHER = "Claude Opus 5 (1M context) <noreply@anthropic.com>"
    THIRD = "Grok 4.6 <noreply@x.ai>"
    SESSION = "store-session-1"

    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-store-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.conf = self.world.plant_text("store-lane.conf", "LANE_NAME='testlane'\n")
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def env(self, **keys):
        base = {k: v for k, v in os.environ.items()
                if k not in ("WF_AGENT", "WF_AGENT_OVERRIDE",
                             "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                             "CODEX_THREAD_ID", "CODEX_SESSION_ID")}
        base["LANE_CONF"] = str(self.conf)
        base.update({k: v for k, v in keys.items() if v is not None})
        return mock.patch.dict(os.environ, base, clear=True)

    def test_an_absent_store_is_an_answer_and_never_a_refusal(self):
        # Looked and found nothing: no session has bound a name here, so
        # the ambient one answers. A read must not create the store.
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID=self.SESSION):
            self.assertFalse(self.m.state_db().exists())
            self.assertEqual(self.m.identity(), AGENT)
            self.assertFalse(self.m.state_db().exists(), "a read created the store")

    def test_the_stored_identity_outranks_the_ambient_one(self):
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID=self.SESSION):
            self.m.set_identity(self.THIRD)
            self.assertEqual(self.m.identity(), self.THIRD)

    def test_the_override_outranks_the_stored_identity(self):
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID=self.SESSION,
                      WF_AGENT_OVERRIDE=self.OTHER):
            self.m.set_identity(self.THIRD)
            self.assertEqual(self.m.identity(), self.OTHER)

    def test_a_row_answers_its_own_session_and_no_other(self):
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID=self.SESSION):
            self.m.set_identity(self.THIRD)
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID="a-different-session"):
            self.assertEqual(self.m.identity(), AGENT)

    def test_setting_again_replaces_the_row_and_never_adds_one(self):
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID=self.SESSION):
            self.m.set_identity(self.THIRD)
            self.m.set_identity(self.OTHER)
            self.assertEqual(self.m.identity(), self.OTHER)
            store = self.m.state_db()
        with sqlite3.connect(store) as con:
            rows = con.execute(
                "SELECT session_id, agent FROM session_identity").fetchall()
        self.assertEqual(rows, [(self.SESSION, self.OTHER)])

    def test_setting_a_malformed_agent_refuses_identity(self):
        with self.env(CLAUDE_CODE_SESSION_ID=self.SESSION):
            with self.assertRaises(self.m.Refusal) as caught:
                self.m.set_identity("JustAName")
        self.assertEqual(caught.exception.kind, "identity")

    def test_setting_with_no_session_refuses_and_leaves_no_store(self):
        # A row keyed on the word `unknown` would be shared by every
        # session that has no id, which is worse than no row at all.
        with self.env(WF_AGENT=AGENT):
            with self.assertRaises(self.m.Refusal) as caught:
                self.m.set_identity(self.THIRD)
            self.assertEqual(caught.exception.kind, "session")
            self.assertFalse(self.m.state_db().exists(),
                             "a refused set left a store behind")

    def test_a_store_that_cannot_be_read_refuses_rather_than_answering(self):
        # Could not look. Falling through to the ambient name here would
        # print an answer the reader never obtained.
        with self.env(WF_AGENT=AGENT, CLAUDE_CODE_SESSION_ID=self.SESSION):
            store = self.m.state_db()
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_bytes(b"this is not a database")
            with self.assertRaises(self.m.Refusal) as caught:
                self.m.identity()
        self.assertEqual(caught.exception.kind, "store")

    def test_a_copy_with_no_lane_conf_answers_from_the_ambient_key(self):
        # No conf means no lane, so no lane state directory and no store:
        # found nothing, not could-not-look. A staged copy must still be
        # able to name its agent.
        base = {k: v for k, v in os.environ.items()
                if k not in ("WF_AGENT", "WF_AGENT_OVERRIDE", "LANE_CONF",
                             "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                             "CODEX_THREAD_ID", "CODEX_SESSION_ID")}
        base["WF_AGENT"] = AGENT
        base["CLAUDE_CODE_SESSION_ID"] = self.SESSION
        with mock.patch.dict(os.environ, base, clear=True):
            self.assertEqual(self.m.identity(), AGENT)


class TheIdentitySetVerb(unittest.TestCase):
    """The shell-facing half of the store, on the same wire."""

    OTHER = "Claude Opus 5 (1M context) <noreply@anthropic.com>"

    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-set-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("set-lane.conf", "LANE_NAME='testlane'\n")
        self.module = self.world.stage(MODULE, siblings=("laneconf.py",))

    def invoke(self, *argv, session=None, agent=None):
        extra = {"LANE_CONF": str(self.conf)}
        if session is not None:
            extra["CLAUDE_CODE_SESSION_ID"] = session
        if agent is not None:
            extra["WF_AGENT"] = agent
        return run([sys.executable, str(self.module), *argv],
                   cwd=self.world.cwd, env=self.world.env(extra), timeout=60)

    def test_set_then_read_prints_the_bound_identity(self):
        written = self.invoke("identity", "--set", self.OTHER, session="cli-1")
        self.assertEqual(written.exit, 0, written.stderr)
        self.assertIn(self.OTHER, written.stdout)
        read = self.invoke("identity", session="cli-1", agent=AGENT)
        self.assertEqual(read.exit, 0, read.stderr)
        self.assertEqual(read.stdout, self.OTHER + "\n")

    def test_set_with_no_session_refuses_on_the_wire(self):
        proc = self.invoke("identity", "--set", self.OTHER)
        self.assertEqual(proc.exit, 2)
        self.assertEqual(proc.stdout, "")
        lines = proc.stderr.splitlines()
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertRegex(lines[0], r"^session: expected .*; found .*; needed .*$")


class TheClock(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-clock-")
        self.addCleanup(self.world.close)
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def test_now_is_aware_utc(self):
        value = self.m.now()
        self.assertIsNotNone(value.tzinfo)
        self.assertEqual(value.utcoffset().total_seconds(), 0)

    def test_stamp_ends_in_z_and_round_trips(self):
        moment = datetime(2026, 9, 12, 3, 4, 5, 678901, tzinfo=timezone.utc)
        text = self.m.stamp(moment)
        self.assertEqual(text, "2026-09-12T03:04:05.678901Z")
        self.assertEqual(self.m.parse_stamp(text), moment)

    def test_stamp_without_an_argument_is_now(self):
        before = self.m.now()
        parsed = self.m.parse_stamp(self.m.stamp())
        self.assertGreaterEqual(parsed, before.replace(microsecond=0))

    def test_a_naive_stamp_is_read_as_utc(self):
        parsed = self.m.parse_stamp("2026-09-12T03:04:05")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_a_malformed_stamp_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.m.parse_stamp("yesterday")


class TheStateRootAndTheConf(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-state-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.tmp = self.world.root
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def test_state_root_honors_xdg_state_home(self):
        self.assertEqual(self.m.state_root(), self.world.xdg_state)

    def test_state_root_falls_back_to_home_local_state(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_STATE_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.m.state_root(), self.world.home / ".local" / "state")

    def test_conf_reads_the_lane_conf_named_by_lane_conf(self):
        conf = self.world.plant_text("read-lane.conf", "LANE_NAME='testlane'\n")
        with mock.patch.dict(os.environ, {"LANE_CONF": str(conf)}):
            self.assertEqual(self.m.conf("LANE_NAME"), "testlane")
            self.assertEqual(self.m.lane_name(), "testlane")

    def test_state_dir_is_the_lane_under_the_state_root(self):
        conf = self.world.plant_text("state-lane.conf", "LANE_NAME='testlane'\n")
        env = {"LANE_CONF": str(conf)}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self.m.state_dir(), self.world.xdg_state / "testlane")

    def test_a_staged_copy_names_its_conf_or_refuses_conf_file(self):
        """A copy of the module staged beside a copy of a tool has no
        tracked conf beside it. No name stands in: with nothing named
        it refuses conf-file, and a LANE_CONF names the conf it reads.
        The module itself carries no lane-name literal."""
        staged = self.world.stage(
            MODULE, "second/instrument.py", siblings=("laneconf.py",)
        )
        spec = importlib.util.spec_from_file_location("instrument_staged", staged)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = {k: v for k, v in os.environ.items() if k not in ("LANE_CONF", "LANE_NAME")}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            self.assertRaises(module.Refusal) as caught,
        ):
            module.lane_name()
        self.assertEqual(caught.exception.kind, "conf-file")
        conf = self.world.plant_text("named.conf", "LANE_NAME='namedlane'\n")
        env["LANE_CONF"] = str(conf)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(module.lane_name(), "namedlane")
        source = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("'agentscient'", source)
        self.assertNotIn('"agentscient"', source)

    def test_a_missing_key_refuses_conf_key(self):
        conf = self.world.plant_text("missing-key.conf", "LANE_NAME='testlane'\n")
        with (
            mock.patch.dict(os.environ, {"LANE_CONF": str(conf)}),
            self.assertRaises(self.m.Refusal) as caught,
        ):
            self.m.conf("NO_SUCH_KEY")
        self.assertEqual(caught.exception.kind, "conf-key")
        self.assertIn("NO_SUCH_KEY", str(caught.exception))

    def test_a_missing_file_refuses_conf_file(self):
        with (
            mock.patch.dict(os.environ, {"LANE_CONF": str(self.tmp / "absent.conf")}),
            self.assertRaises(self.m.Refusal) as caught,
        ):
            self.m.conf("LANE_NAME")
        self.assertEqual(caught.exception.kind, "conf-file")


class TheOwnerTerminal(unittest.TestCase):
    """LD-125: a terminal the owner opened by hand has no managed launch, so
    `bind --run R-n` mints its coordination capability. The order is token,
    participant row, file (the XO agentscient-87's ruling, 2026-09-23): the
    verb mints the token and inserts the row, and instrument.py writes the
    pointer, owner-terminal-profile/1, at
    <state dir>/runs/R-n/terminals/PT-n/home/profile.json. It is not a hook
    profile, so hook_profile() never returns it; capability_digest() reads it
    and refuses `capability` naming `missing` or `malformed <what>`."""

    CAP = "ab" * 32

    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-terminal-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))
        conf = self.world.plant_text("terminal-lane.conf", "LANE_NAME='testlane'\n")
        env = {k: v for k, v in os.environ.items()
               if k not in ("AGENTSCIENT_HOOK_PROFILE", "COORDINATION_CAPABILITY")}
        env["LANE_CONF"] = str(conf)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = self.world.xdg_state / "testlane/runs/R-3/terminals/PT-7/home"

    def refused(self, call):
        with self.assertRaises(self.m.Refusal) as caught:
            call()
        self.assertEqual(caught.exception.kind, "capability", str(caught.exception))
        return str(caught.exception)

    def named(self, path):
        return mock.patch.dict(os.environ, {"AGENTSCIENT_HOOK_PROFILE": str(path)})

    def test_mint_writes_the_pointer_where_the_contract_names_it(self):
        path = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        self.assertEqual(Path(path), self.home / "profile.json")
        self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(Path(path).read_text()), {
            "schema": "owner-terminal-profile/1", "run": "R-3", "terminal": "PT-7",
            "coordination_capability": self.CAP})

    def enroll(self):
        env = self.world.env({"LANE_CONF": os.environ["LANE_CONF"], "WF_AGENT": AGENT,
                              "CODEX_THREAD_ID": "terminal", "COORDINATION_CAPABILITY": self.CAP})
        result = run([sys.executable, str(TOOLS / "coordination.py"), "bind", "--roles", "worker"],
                     cwd=self.world.cwd, env=env, timeout=60)
        self.assertEqual(result.exit, 0, result.stderr)
        with self.m.connect_store(self.m.state_db(), mode="rw") as conn:
            conn.execute("UPDATE participants SET id=7, terminal_run=3 WHERE session_id='terminal'")

    def test_the_digest_reads_the_pointer_and_never_the_ambient_credential(self):
        self.enroll()
        path = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        with self.named(path), mock.patch.dict(os.environ, {"COORDINATION_CAPABILITY": "cd" * 32}):
            self.assertEqual(self.m.capability_digest(), hashlib.sha256(self.CAP.encode()).hexdigest())
            self.assertIsNone(self.m.hook_profile())
            self.assertEqual(self.m.state_root(), self.world.xdg_state)

    def test_an_absent_pointer_refuses_missing_and_never_falls_back(self):
        with self.named(self.home / "profile.json"), \
                mock.patch.dict(os.environ, {"COORDINATION_CAPABILITY": self.CAP}):
            reason = self.refused(self.m.capability_digest)
        self.assertIn("; found missing;", reason)

    def test_a_malformed_pointer_refuses_naming_the_malformation(self):
        good = {"schema": "owner-terminal-profile/1", "run": "R-3", "terminal": "PT-7",
                "coordination_capability": self.CAP}
        cases = {
            "json": "{",
            "schema": json.dumps({**good, "schema": "shared-hook-profile/1"}, sort_keys=True),
            "capability": json.dumps({**good, "coordination_capability": "ab"}, sort_keys=True),
            "run": json.dumps({**good, "run": "R-4"}, sort_keys=True),
            "keys": json.dumps({**good, "harness": "claude"}, sort_keys=True),
        }
        path = self.home / "profile.json"
        for what, body in cases.items():
            with self.subTest(what=what):
                self.home.mkdir(parents=True, exist_ok=True)
                path.unlink(missing_ok=True)
                path.write_text(body)
                path.chmod(0o600)
                with self.named(path):
                    reason = self.refused(self.m.capability_digest)
                self.assertIn("; found malformed ", reason)
        path.unlink()
        path.write_text(json.dumps(good, sort_keys=True))
        path.chmod(0o644)
        with self.named(path):
            self.assertIn("; found malformed permissions", self.refused(self.m.capability_digest))

    def test_a_pointer_outside_the_state_dir_refuses_malformed_location(self):
        minted = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        elsewhere = self.world.root / "copy/runs/R-3/terminals/PT-7/home/profile.json"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text(Path(minted).read_text())
        elsewhere.chmod(0o600)
        with self.named(elsewhere):
            self.assertIn("; found malformed location", self.refused(self.m.capability_digest))

    def test_a_symlinked_ancestor_never_leads_outside_the_state_dir(self):
        # origin: A-548 (record 20260923T060230Z-review-codex-bd7988#1): mkdir followed
        # an ancestor symlink, O_NOFOLLOW guarded only profile.json, and the reader's
        # two realpaths crossed the same link, so mint wrote outside the state root,
        # the digest read that file and remove unlinked it.
        state = self.world.xdg_state / "testlane"
        parts = ("runs", "R-3", "terminals", "PT-7", "home")
        body = json.dumps({"schema": "owner-terminal-profile/1", "run": "R-3", "terminal": "PT-7",
                           "coordination_capability": self.CAP}, sort_keys=True)
        for depth in range(1, len(parts) + 1):
            linked = state.joinpath(*parts[:depth])
            outside = self.world.root / f"outside-{depth}"
            with self.subTest(ancestor="/".join(parts[:depth])):
                linked.parent.mkdir(parents=True, exist_ok=True)
                outside.mkdir()
                linked.symlink_to(outside, target_is_directory=True)
                try:
                    landed = outside.joinpath(*parts[depth:], "profile.json")
                    self.refused(lambda: self.m.mint_terminal_profile("R-3", "PT-7", self.CAP))
                    self.assertFalse(landed.exists(), "mint wrote through the link")
                    landed.parent.mkdir(parents=True, exist_ok=True)
                    landed.write_text(body)
                    landed.chmod(0o600)
                    with self.named(self.home / "profile.json"):
                        self.assertIn("; found malformed location", self.refused(self.m.capability_digest))
                    self.assertFalse(self.m.remove_terminal_profile("R-3", "PT-7", self.CAP))
                    self.assertTrue(landed.is_file(), "remove unlinked through the link")
                finally:
                    linked.unlink()
        # Clean neighbour: the same tree with no link mints, reads and removes.
        self.enroll()
        path = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        self.assertEqual(Path(path), self.home / "profile.json")
        with self.named(path):
            self.assertEqual(self.m.terminal_profile()["coordination_capability"], self.CAP)
        self.assertTrue(self.m.remove_terminal_profile("R-3", "PT-7", self.CAP))

    def test_a_minted_home_is_never_replaced(self):
        path = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        before = Path(path).read_text()
        self.refused(lambda: self.m.mint_terminal_profile("R-3", "PT-7", "cd" * 32))
        self.assertEqual(Path(path).read_text(), before)

    def test_mint_token_is_a_fresh_capability_each_call(self):
        # agentscient-10's side calls it before the INSERT: the token, then the row.
        first, second = self.m.mint_token(), self.m.mint_token()
        for token in (first, second):
            self.assertRegex(token, r"\A[0-9a-f]{64}\Z")
        self.assertNotEqual(first, second)

    def test_a_failed_commit_removes_only_the_pointer_it_wrote(self):
        # A COMMIT that fails after the file exists leaves a pointer whose participant
        # never landed; the retry reuses the id, and O_EXCL would refuse it.
        path = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        self.assertFalse(self.m.remove_terminal_profile("R-3", "PT-7", "cd" * 32))
        self.assertTrue(Path(path).is_file())
        self.assertTrue(self.m.remove_terminal_profile("R-3", "PT-7", self.CAP))
        self.assertFalse(Path(path).exists())
        self.assertFalse(self.m.remove_terminal_profile("R-3", "PT-7", self.CAP))
        self.assertEqual(Path(self.m.mint_terminal_profile("R-3", "PT-7", "cd" * 32)), path)

    def test_remove_rejects_traversal_before_touching_a_matching_file(self):
        outside = self.world.root / "outside/terminals/PT-7/home/profile.json"
        outside.parent.mkdir(parents=True)
        outside.write_text(json.dumps({"coordination_capability": self.CAP}))
        state = self.world.xdg_state / "testlane"
        (state / "runs").mkdir(parents=True)
        traversal = os.path.relpath(outside.parents[3], state / "runs")
        with self.assertRaises(self.m.Refusal):
            self.m.remove_terminal_profile(traversal, "PT-7", self.CAP)
        self.assertTrue(outside.is_file())

    def test_an_unenrolled_pointer_is_not_a_capability(self):
        path = self.m.mint_terminal_profile("R-3", "PT-7", self.CAP)
        with self.named(path):
            self.assertIn("malformed enrollment", self.refused(self.m.capability_digest))

    def test_mint_refuses_a_malformed_run_terminal_or_capability(self):
        for run_id, terminal, capability in [("R-x", "PT-1", self.CAP), ("R-1", "PT-1/..", self.CAP),
                                             ("R-1", "PT-1", "zz"), ("R-1", "PT-1", None)]:
            with self.subTest(run=run_id, terminal=terminal), self.assertRaises(self.m.Refusal):
                self.m.mint_terminal_profile(run_id, terminal, capability)
        self.assertFalse((self.world.xdg_state / "testlane/runs").exists())


class TheBoundedCall(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-call-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.m = load(self.world.stage(MODULE, siblings=("laneconf.py",)))

    def test_run_returns_the_completed_process_without_a_shell(self):
        result = self.m.run([sys.executable, "-c", "print('ok')"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ok\n")

    def test_a_timeout_refuses_with_the_executable_named(self):
        with self.assertRaises(self.m.Refusal) as caught:
            self.m.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
        name = Path(sys.executable).name
        self.assertEqual(caught.exception.kind, f"{name}-error")
        self.assertIn("timeout", str(caught.exception))

    def test_a_missing_executable_refuses_with_the_executable_named(self):
        with self.assertRaises(self.m.Refusal) as caught:
            self.m.run(["no-such-binary-instrument-test", "x"])
        self.assertEqual(caught.exception.kind, "no-such-binary-instrument-test-error")

    def test_git_runs_git_in_the_given_directory(self):
        result = self.m.git("rev-parse", "--is-inside-work-tree", cwd=REPO)
        self.assertEqual(result.stdout.strip(), "true")


class TheCommandLine(unittest.TestCase):
    """The two verbs shell levers may call for the same wire."""

    def setUp(self):
        self.assertTrue(MODULE.is_file(), f"absent: {MODULE}")
        self.world = World(prefix="instrument-home-")
        self.addCleanup(self.world.close)
        self.module = self.world.stage(MODULE, siblings=("laneconf.py",))

    def invoke(self, *argv, agent=None):
        extra = {}
        if agent is not None:
            extra["WF_AGENT"] = agent
        return run(
            [sys.executable, str(self.module), *argv], cwd=self.world.cwd,
            env=self.world.env(extra), timeout=60,
        )

    def test_identity_without_wf_agent_refuses_on_the_wire(self):
        proc = self.invoke("identity")
        self.assertEqual(proc.exit, 2, proc.stderr)
        self.assertEqual(proc.stdout, "")
        lines = proc.stderr.splitlines()
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertRegex(lines[0], r"^identity: expected .*; found unset; needed .*$")

    def test_identity_with_wf_agent_prints_it(self):
        proc = self.invoke("identity", agent=AGENT)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout, AGENT + "\n")
        self.assertEqual(proc.stderr, "")

    def test_refuse_emits_the_given_refusal(self):
        proc = self.invoke("refuse", "scope", "one job", "two jobs", "split them")
        self.assertEqual(proc.exit, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(
            proc.stderr, "scope: expected one job; found two jobs; needed split them\n"
        )

    def test_an_unknown_verb_refuses_arguments(self):
        proc = self.invoke("dance")
        self.assertEqual(proc.exit, 2)
        self.assertEqual(proc.stdout, "")
        self.assertRegex(proc.stderr, r"^arguments: expected .*; found dance; needed .*\n$")


class TheConsolidationGate(unittest.TestCase):
    """Every tool is placed; a migrated tool carries no private copy."""

    def tools(self) -> list[str]:
        # instrument.py was excluded here by name, so it alone had no
        # placement file — and the state law declares a state engine only
        # in a placement file. The module every tool imports was the one
        # place a store could be opened with nothing declaring it. It is
        # in the census now, under a placement of its own.
        return sorted(p.name for p in TOOLS.glob("*.py"))

    def test_every_tool_is_migrated_or_declared_pending(self):
        lists = (MIGRATED, PENDING, NO_WIRE, SHARED)
        placed = set().union(*lists)
        self.assertEqual(
            sum(len(t) for t in lists), len(placed), "a tool in two lists"
        )
        self.assertEqual(
            sorted(placed), self.tools(),
            "each tool under ops/conductor/tools/ has one file under ops/conductor/placement/",
        )

    def test_the_shared_placement_names_the_module_the_others_import(self):
        """`shared` is not a spare label. It is instrument.py, and what makes
        it shared is that it defines the helpers the others are forbidden to
        define. Pinning both halves keeps a tool from claiming the placement
        to escape the private-copy rule."""
        self.assertEqual(
            list(SHARED), ["instrument.py"],
            "shared names the module the others import, and nothing else",
        )
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        } | {
            target.id
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        # Not every name in PRIVATE_NAMES: four of them (AGENT_FORM,
        # IDENTITY_RE, Refused, conf_value) are retired spellings that no
        # module defines any more, and the set forbids them so a tool
        # cannot bring one back. What the shared module must hold is the
        # live ones the others import instead of defining.
        self.assertTrue(
            defined & PRIVATE_NAMES,
            "the shared module defines none of the names the others may not, "
            "so nothing is being shared",
        )
        for name in ("Refusal", "refuse", "identity", "now", "stamp"):
            with self.subTest(name=name):
                self.assertIn(
                    name, defined,
                    f"{name} is forbidden to every other tool and must come "
                    "from the shared module",
                )

    def test_placement_files_close_both_ways_and_name_a_declared_placement(self):
        """A file for no tool names the file; a file whose placement is not
        one of the declared ones names the field; the directory holds
        nothing else."""
        self.assertTrue(PLACEMENT_DIR.is_dir(), "ops/conductor/placement/ exists")
        tools = set(self.tools())
        for path in sorted(PLACEMENT_DIR.iterdir()):
            with self.subTest(file=path.name):
                self.assertTrue(path.name.endswith(".py.json"), f"{path.name}: not <tool>.py.json")
                self.assertIn(path.name[: -len(".json")], tools, f"{path.name}: no such tool")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(sorted(data), ["placement", "state"],
                                 f"{path.name}: exactly two fields, placement and state "
                                 "(test_state_placement.py reads state)")
                self.assertIn(data["placement"], PLACEMENTS, f"{path.name}: placement not one of {PLACEMENTS}")

    def test_a_no_wire_tool_defines_no_private_copy_and_refuses_nowhere(self):
        for name in NO_WIRE:
            with self.subTest(tool=name):
                tree = ast.parse((TOOLS / name).read_text(encoding="utf-8"))
                defined = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                } | {
                    target.id
                    for node in tree.body if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)
                }
                self.assertEqual(
                    sorted(defined & PRIVATE_NAMES), [],
                    f"{name} still defines a private copy",
                )
                called = {
                    node.func.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
                self.assertNotIn("refuse", called, f"{name} refuses; it belongs in MIGRATED")

    def test_a_migrated_tool_imports_the_module_and_defines_no_private_copy(self):
        for name in MIGRATED:
            with self.subTest(tool=name):
                tree = ast.parse((TOOLS / name).read_text(encoding="utf-8"))
                defined = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                } | {
                    target.id
                    for node in tree.body if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)
                }
                self.assertEqual(
                    sorted(defined & PRIVATE_NAMES), [],
                    f"{name} still defines a private copy",
                )
                imported = {
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                } | {
                    node.module
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                self.assertIn("instrument", imported, f"{name} does not import instrument")


if __name__ == "__main__":
    unittest.main()
