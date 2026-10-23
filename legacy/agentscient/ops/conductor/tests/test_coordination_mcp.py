"""Acceptance tests for the acting coordination MCP server, from the contract.

Contract (only): `ops/conductor/coordination-mcp-lever.md`, with
`docs/wire.md` for the refusal shape and the counterparty contract
`ops/conductor/coordination-lever.md` at 537f3a35 for the core's verb
names and refusal classes. The subject
`ops/conductor/tools/coordination_mcp.py` is not read here.

The subject is invoked as a subprocess through its documented CLI:

    python3 -B coordination_mcp.py [--config FILE] check | serve | call TOOL [JSON]

MCP is exercised as UTF-8 newline-delimited JSON-RPC on that same CLI's
`serve` stdio. No live state store, no network, no credentials, no
harness call: World owns HOME, the XDG roots and TMPDIR, so the core's
own store can never be the host's.

This file was sealed at 333c200f and reopened at the Lead's disposition
of 2026-09-16, which accepted eleven review findings. What changed and
why is in the fix commit, finding by finding; the rows below name their
origin.

The implementation may be absent. `World.stage` plants a no-op
stand-in (exit 0, no output) when it is, so each case fails on the
contract promise rather than on Python's missing-file exit 2; the
absent entrypoint is one red of its own,
`test_the_tool_file_is_present`.

**A case that never executes the subject's behaviour is a declared gap
wearing a pass.** Found by auditing this file's own cases on 2026-09-16
against #242's second review round, and being added to docs/tests.md as a
rule of its own. Six cases here failed it: five asserted something about a
schema or a mock and one about a name, and not one of them ran the server
and looked at what it did. Two of the six no reviewer had found. When a
case pins a key in an inputSchema, a shape the counterparty never returns,
or two vocabularies that cannot collide, it is a gap: rewrite it to run
the subject, or drop it and say so.

Fixture set (docs/tests.md rule 3). Planted faults it must catch: an
unknown verb, an unknown tool, a role supplied as an argument, a
participant supplied as an argument, an unknown argument key, a
malformed config, a config of an unknown version, a config naming a
participant that is not `PT-n`, a config naming a participant the core
does not hold, a generation below the bound the schema advertises, a
call with no binding of any kind, a call whose binding lacks the
capability, a core with no verb table, a verb absent from the table, an
oversized transport line, an unknown JSON-RPC method, a non-string
protocol version, and a payload over the response bound. Clean
neighbours it must pass quietly: a negotiated initialize on each of the
three declared protocols, a ping carrying extra members, a tools/list
carrying every declared tool, a notification answered with silence, a
bounded valid line after an oversized one, and a call whose binding
carries the capability.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run
from ops.testkit.cases import RefusalCase, refusal_wire, run_table

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "coordination_mcp.py"
CORE_TOOL = TOOLS / "coordination.py"
LEVER = REPO / "ops" / "conductor" / "coordination-mcp-lever.md"
LOCK_LEVER = TOOLS / "deliverable.py"
SIBLINGS = ("instrument.py", "mcp_transport.py")

# The real-core class binds two sessions that share one agent string,
# because the participant and not the agent is the authority.
SHARED_AGENT = "Claude Opus 5 (1M context) <noreply@example.test>"
LEAD_SESSION = "sess-mcp-lead"
WORKER_SESSION = "sess-mcp-worker"
FIXTURE_BRANCH = "lane/session-20260916-mcp"
FIXTURE_REVISION = "a1b2c3d"


def load_module(path, name):
    """A module loaded from a path, for reading its signatures."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_core():
    """The real core, for the signatures the mock must match."""
    return load_module(CORE_TOOL, "coordination_for_signatures")

TIMEOUT = 30.0
# Renamed from agentscient-coord at the Lead's ruling C1, 2026-09-16:
# the lane literal guard exempts four files by name and laneconf.py is
# wall, so the server carries no lane literal instead.
SERVER = "coordination"
LATEST = "2025-06-18"
DOMAIN_REFUSAL = -32000
RESPONSE_BYTES = 16_384

SESSION = "ab281ec6-0000-4000-8000-00000000d0d0"
PARTICIPANT = "PT-7"
ABSENT_PARTICIPANT = "PT-9"

# The catalogue of slice one, from the contract's section 3: the tool,
# the core verb's wire name, and whether it mutates.
CATALOGUE = (
    ("coord_brief", "brief", False),
    ("coord_assignments", "assignments", False),
    ("coord_show", "show", False),
    ("coord_draft", "draft", True),
    ("coord_offer", "offer", True),
    ("coord_claim", "claim", True),
    ("coord_release", "release", True),
    ("coord_submit", "submit", True),
    ("coord_verify", "verify", True),
    ("coord_return", "return", True),
    ("coord_accept", "accept", True),
    ("coord_send", "send", True),
    ("coord_inbox", "inbox", False),
    ("coord_ack", "ack", True),
    ("coord_resolve", "resolve", True),
    ("coord_outstanding", "outstanding", False),
    ("coord_observe", "observe", True),
)
TOOL_NAMES = tuple(name for name, _verb, _mutates in CATALOGUE)
MUTATIONS = tuple(name for name, _verb, mutates in CATALOGUE if mutates)
READS = tuple(name for name, _verb, mutates in CATALOGUE if not mutates)
# Every writing tool on an assignment states the generation the caller
# believes; coord_observe joined them at X#2.
GENERATION_TOOLS = (
    "coord_claim", "coord_release", "coord_submit", "coord_verify",
    "coord_return", "coord_accept", "coord_observe", "coord_offer",
)
# coord_observe also carries the core's explicit stale flag (05b8aebe).
STALE_TOOL = "coord_observe"
# Every paged read carries an explicit cursor (X#3).
PAGED_TOOLS = ("coord_assignments", "coord_inbox", "coord_outstanding")
# The resources this server lists, and the tool each reaches. Named here
# so a case can assert the mapping rather than compare two vocabularies
# that cannot collide.
RESOURCE_ROWS = (
    ("coord://brief", "coord_brief", "This session's bounded picture"),
    ("coord://assignments", "coord_assignments", "Assignments in scope"),
    ("coord://inbox", "coord_inbox", "Messages for this participant"),
    ("coord://outstanding", "coord_outstanding", "Unresolved obligations"),
)


def binding(**over) -> dict:
    """A valid acting config, version 1, with its exact key set."""
    row = {
        "version": 1,
        "participant": PARTICIPANT,
        "assignment": "A-3",
        "branch": "lane/session-20260916-d",
        "worktree": "/tmp/nowhere",
        "record": "20260916T000000Z-code-claude-000000",
        "capabilities": ["coord_brief", "coord_submit"],
        "issued_at": "2026-09-16T00:00:00Z",
    }
    row.update(over)
    return row


class Subject:
    """The staged tool and the world it runs in."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.path = world.stage(TOOL, siblings=SIBLINGS if TOOL.is_file() else ())

    def argv(self, *rest: str, config: Path | str | None = None) -> list[str]:
        head = [sys.executable, "-B", str(self.path)]
        if config is not None:
            head += ["--config", str(config)]
        return head + list(rest)

    def env(self, *, session: str | None = None) -> dict[str, str]:
        extra = {} if session is None else {"CLAUDE_CODE_SESSION_ID": session,
                                             "COORDINATION_CAPABILITY": hashlib.sha256(session.encode()).hexdigest()}
        return self.world.env(extra=extra)

    def run(self, *rest: str, config=None, session=None, stdin=None):
        return run(self.argv(*rest, config=config), cwd=self.world.root,
                   env=self.env(session=session), stdin=stdin, timeout=TIMEOUT)

    def config(self, row: dict | str, name: str = "coordination-mcp.json") -> Path:
        text = row if isinstance(row, str) else json.dumps(row, indent=1) + "\n"
        return self.world.plant_text(name, text)

    def core(self, body: str) -> Path:
        """Plant a stub coordination core beside the staged tool."""
        return self.world.plant_text(self.path.parent / "coordination.py", body)


# The contract-verified mock of the core, in the shape coordination.py
# carries at 05b8aebe: CORE keyed by wire name, each entry taking a store
# handle and the resolved participant; VERBS beside it for the CLI, which
# is the wrong layer for this server and is planted here so a projection
# that reached for it fails; and bound_participant, the resolver, which
# is not a verb, so the wire name `resolve` is the message verb alone.
STUB_CORE = '''"""A stand-in for the coordination core, in its b1d868b0 shape.

The future import is not decoration: the core carries it, so its
annotations are strings, and a stand-in without it compares unequal to
the real signature it must match.
"""

from __future__ import annotations


class Refusal(Exception):
    pass


def _answer(verb, participant, arguments):
    return {"verb": verb, "participant": participant, "arguments": arguments}


def writer():
    return {"store": "writer"}


def reader():
    return {"store": "reader"}


def bound_participant(conn, *, participant=None):
    """The participant resolver. Not a verb, and not in CORE.

    It reads the session id from its own environment, as
    instrument.session() does; echoing it proves the id reached this
    process rather than being supplied by the caller.
    """
    import os

    return {"participant": "PT-1", "id": 1, "roles": "lead,worker",
            "agent": "Stub Agent <noreply@example.test>",
            "store": conn, "session": os.environ.get("CLAUDE_CODE_SESSION_ID", "")}


def _brief(conn, part) -> dict:
    return _answer("brief", part, {})


def _submit(conn, part, *, agent, assignment, evidence, revision,
            generation) -> dict:
    return _answer("submit", part, {
        "agent": agent, "assignment": assignment, "evidence": evidence,
        "revision": revision, "generation": generation})


def _message_resolve(conn, part, *, agent, message, evidence) -> dict:
    """The core's message verb. Shares its wire name with the resolver
    above and must never be confused with it."""
    return _answer("message-resolve", part,
                   {"agent": agent, "message": message, "evidence": evidence})


def _participants(conn, part, *, role=None) -> dict:
    return {"participants": [{"participant": "PT-7", "id": 7,
                              "roles": "worker"}]}


def _assignments(conn, part, *, state=None, to=None) -> dict:
    """The whole list, ordered by id, exactly as the core returns it: the
    core does not page, so a server that forwarded a cursor would fail on
    the signature before it ever reached here."""
    return {"assignments": [{"id": number, "title": f"t{number}"}
                            for number in range(1, 131)]}


def _show(conn, part, *, target) -> dict:
    """A payload comfortably under the response bound on its own, and over
    it once tools/call carries it twice."""
    return {"blob": "x" * 12000, "target": target}


CORE = {
    "brief": _brief,
    "submit": _submit,
    "resolve": _message_resolve,
    "participants": _participants,
    "assignments": _assignments,
    "show": _show,
}

# The CLI wrappers live beside the table and are not this server's layer.
VERBS = {"brief": lambda *a, **k: {"verb": "the-cli-wrapper"}}
'''

# A core whose table is there but whose participant list holds nobody.
STUB_CORE_NO_ROWS = STUB_CORE.replace(
    '"participants": [{"participant": "PT-7", "id": 7,\n'
    '                              "roles": "worker"}]', '"participants": []')
# A core that is present but exposes no verb table at all.
STUB_CORE_NO_TABLE = STUB_CORE.replace("CORE = {", "NOT_THE_TABLE = {")


def serve_once(subject, request):
    """One JSON-RPC request over the staged server's stdio, the answers back.

    A module-level helper because two classes need it: the transport cases
    and the resource case, which stages a faulted copy of the server.
    """
    result = run(subject.argv("serve"), cwd=subject.world.root,
                 env=subject.env(), stdin=json.dumps(request) + chr(10),
                 timeout=TIMEOUT)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def answer(answers, identifier=None):
    """The one answer a case reads, or an assertion naming what was absent.

    A stand-in that writes nothing must fail a case on its own promise,
    never on an IndexError: an error is a fixture defect, not a red
    (ops/process/tdd.md).
    """
    if not answers:
        raise AssertionError(
            "the server answered nothing; one JSON-RPC response was expected")
    if identifier is None:
        return answers[0]
    for item in answers:
        if item.get("id") == identifier:
            return item
    raise AssertionError(f"no answer carried id {identifier}: {answers}")


def payload(result):
    """The JSON a verb printed, or an assertion naming an empty stdout."""
    if not result.stdout.strip():
        raise AssertionError(
            f"the verb printed nothing; exit {result.exit}, stderr {result.stderr!r}")
    return json.loads(result.stdout)


class CoordinationMcpCli(unittest.TestCase):
    """The CLI projection: exit 2, one stderr line, stdout empty."""

    def rows(self, subject: Subject):
        good = subject.config(binding())
        unknown_version = subject.config(binding(version=9), "version.json")
        unknown_key = subject.config(dict(binding(), extra="no"), "extra.json")
        bad_participant = subject.config(
            binding(participant="Claude <noreply@anthropic.com>"), "agent.json")
        return (
            RefusalCase(
                id="no-verb", argv=(), cls="arguments",
                named=("check", "serve", "call"),
                origin="contract section 1, the declared command shape"),
            RefusalCase(
                id="unknown-verb", argv=("dispatch",), cls="arguments",
                named=("dispatch",),
                origin="contract section 1"),
            RefusalCase(
                id="call-no-tool", argv=("call",), cls="arguments",
                origin="contract section 3"),
            RefusalCase(
                id="unknown-tool", argv=("call", "coord_nope"), cls="arguments",
                named=("coord_nope",),
                origin="contract section 3, the catalogue is closed"),
            RefusalCase(
                id="role-as-argument",
                argv=("call", "coord_brief", '{"role": "lead"}'),
                cls="arguments", named=("role",),
                origin="AC-02: a caller may not name its own authority"),
            RefusalCase(
                id="participant-as-argument",
                argv=("call", "coord_brief", '{"participant": "PT-1"}'),
                cls="arguments", named=("participant",),
                origin="AC-02, and the core's own rule"),
            RefusalCase(
                id="unknown-argument",
                argv=("call", "coord_brief", '{"nope": 1}'),
                cls="arguments", named=("nope",),
                origin="contract section 3, arguments are a closed object"),
            RefusalCase(
                id="malformed-arguments",
                argv=("call", "coord_brief", "{not json"),
                cls="arguments",
                origin="contract section 1"),
            RefusalCase(
                id="generation-below-the-bound",
                argv=("call", "coord_submit", json.dumps(
                    {"assignment": "A-1", "evidence": "abc1234",
                     "revision": "abc1234", "generation": 0})),
                cls="arguments", named=("generation", "1"),
                origin="G#1, 20260916T075500Z-review-grok-c7a708#1: a declared "
                       "bound the server does not keep makes the next actor guess"),
            RefusalCase(
                id="config-absent",
                argv=("--config", "/dev/null", "call", "coord_brief"),
                cls="config",
                origin="contract section 2.1"),
            RefusalCase(
                id="config-unknown-version",
                argv=("--config", str(unknown_version), "call", "coord_brief"),
                cls="config", named=("9",),
                origin="contract section 2.1, its own version"),
            RefusalCase(
                id="config-unknown-key",
                argv=("--config", str(unknown_key), "call", "coord_brief"),
                cls="config", named=("extra",),
                origin="contract section 2.1, its own exact key set"),
            RefusalCase(
                id="config-participant-not-an-id",
                argv=("--config", str(bad_participant), "call", "coord_brief"),
                cls="config",
                origin="contract section 2.1: a PT-n id, never an agent string"),
            RefusalCase(
                id="no-binding-at-all",
                argv=("call", "coord_brief"), cls="session",
                origin=("contract section 2.3; also the registry's identity "
                        "provocation (G#5 and X#5)")),
            RefusalCase(
                id="capability-not-carried",
                argv=("--config", str(good), "call", "coord_observe"),
                cls="capability", named=("coord_observe",),
                origin="contract section 2.1: the allowlist narrows"),
        )

    def test_the_refusal_wire_on_every_row(self):
        with World() as world:
            subject = Subject(world)
            rows = self.rows(subject)
            run_table(self, rows, lambda row: run(
                subject.argv(*row.argv), cwd=world.root,
                env=subject.env(), timeout=TIMEOUT))

    def test_a_session_with_no_core_refuses_state_not_silence(self):
        """The binding resolves, the core is absent: a reading, never a pass."""
        with World() as world:
            subject = Subject(world)
            result = subject.run("call", "coord_brief", session=SESSION)
            refusal = refusal_wire(
                RefusalCase(id="core-absent", argv=(), cls="state",
                            origin="contract section 7, the core edge"),
                result)
            self.assertIn("coordination", refusal.line)

    def test_an_unauthorised_caller_learns_nothing_about_arguments(self):
        """Pins: a caller whose binding lacks the tool is refused class
        capability whatever it puts in the arguments, so it learns neither
        the argument table nor the forbidden-key table.

        Planted fault: the call carries a forbidden authority key, which
        the server checked first until G2#4
        (20260916T084259Z-review-grok-62fa82#4) ran this very call and got
        class arguments back. Catches any return to that order.
        """
        with World() as world:
            subject = Subject(world)
            config = subject.config(binding())
            for arguments in (None, json.dumps({"role": "lead"}),
                              json.dumps({"assignment": "A-1"})):
                with self.subTest(arguments=arguments):
                    argv = ["call", "coord_observe"]
                    if arguments is not None:
                        argv.append(arguments)
                    result = subject.run(*argv, config=config)
                    refusal = refusal_wire(
                        RefusalCase(id=f"capability-first-{arguments}", argv=(),
                                    cls="capability",
                                    origin="G2#4, 20260916T084259Z-review-grok-62fa82#4"),
                        result)
                    self.assertIn("arguments are checked once the capability is held",
                                  refusal.line)

    def test_a_carried_capability_passes_the_allowlist(self):
        """The clean neighbour of capability-not-carried: same config, a tool
        the allowlist names, so the refusal is no longer `capability`."""
        with World() as world:
            subject = Subject(world)
            config = subject.config(binding())
            result = subject.run("call", "coord_submit", config=config)
            self.assertEqual(result.exit, 2, result.stderr)
            self.assertNotIn("capability:", result.stderr)


class CoordinationMcpTransport(unittest.TestCase):
    """Newline-delimited JSON-RPC 2.0 over stdio, stdout protocol only."""

    def serve(self, subject: Subject, *requests, session=None, raw=None):
        lines = raw if raw is not None else [
            json.dumps(item) for item in requests]
        result = run(subject.argv("serve"), cwd=subject.world.root,
                     env=subject.env(session=session),
                     stdin="".join(line + "\n" for line in lines),
                     timeout=TIMEOUT)
        answers = []
        for line in result.stdout.splitlines():
            if line.strip():
                answers.append(json.loads(line))
        return result, answers

    def initialize(self, identifier=1, protocol=LATEST):
        return {"jsonrpc": "2.0", "id": identifier, "method": "initialize",
                "params": {"protocolVersion": protocol,
                           "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}

    def test_initialize_names_this_server_and_negotiates(self):
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(subject, self.initialize())
            result = answer(answers).get("result", {})
            self.assertEqual(result.get("serverInfo", {}).get("name"), SERVER)
            self.assertEqual(result.get("protocolVersion"), LATEST)
            self.assertIn("instructions", result)


    def test_each_method_refuses_a_member_it_does_not_accept(self):
        """Pins: initialize accepts protocolVersion, capabilities and
        clientInfo and nothing else, capabilities and clientInfo are
        objects, and the two list methods accept no members at all.

        Planted faults: an unknown member, a list where an object is
        declared, a string where an object is declared, and a member on a
        list method. Every one was accepted before X2#4
        (20260916T084315Z-review-codex-b317d7#4).
        """
        faults = (
            ("initialize-unknown-member",
             {"protocolVersion": LATEST, "bogus": 1}),
            ("initialize-capabilities-not-an-object",
             {"protocolVersion": LATEST, "capabilities": []}),
            ("initialize-clientinfo-not-an-object",
             {"protocolVersion": LATEST, "clientInfo": "bad"}),
        )
        with World() as world:
            subject = Subject(world)
            for name, params in faults:
                with self.subTest(fault=name):
                    _result, answers = self.serve(subject, {
                        "jsonrpc": "2.0", "id": 20, "method": "initialize",
                        "params": params})
                    self.assertEqual(answer(answers)["error"]["code"], -32602)
            for method in ("tools/list", "resources/list"):
                with self.subTest(fault=f"{method}-with-a-member"):
                    _result, answers = self.serve(subject, {
                        "jsonrpc": "2.0", "id": 21, "method": method,
                        "params": {"bogus": 1}})
                    self.assertEqual(answer(answers)["error"]["code"], -32602)


    def test_an_unknown_method_is_a_method_not_found(self):
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(
                subject, {"jsonrpc": "2.0", "id": 3, "method": "tools/invent"})
            self.assertEqual(answer(answers)["error"]["code"], -32601)


    def test_the_tool_list_is_the_contract_catalogue(self):
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(
                subject, {"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
            listed = answer(answers)["result"]["tools"]
            self.assertEqual(
                tuple(sorted(item["name"] for item in listed)),
                tuple(sorted(TOOL_NAMES)))
            for item in listed:
                with self.subTest(tool=item["name"]):
                    self.assertTrue(item.get("description"))
                    self.assertIn("annotations", item)
                    self.assertIn("inputSchema", item)

    def test_a_mutation_is_annotated_as_one_and_a_read_is_not(self):
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(
                subject, {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
            listed = {item["name"]: item for item in answer(answers)["result"]["tools"]}
            for name in MUTATIONS:
                with self.subTest(tool=name, kind="mutation"):
                    self.assertIs(
                        listed[name]["annotations"].get("readOnlyHint"), False)
            for name in READS:
                with self.subTest(tool=name, kind="read"):
                    self.assertIs(
                        listed[name]["annotations"].get("readOnlyHint"), True)
                    self.assertIs(
                        listed[name]["annotations"].get("destructiveHint"), False)

    def test_a_refused_call_carries_the_wire_line_in_the_result(self):
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(subject, {
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "coord_brief", "arguments": {"role": "lead"}}})
            result = answer(answers)["result"]
            self.assertIs(result["isError"], True)
            text = result["content"][0]["text"]
            self.assertTrue(text.startswith("arguments:"), text)
            for word in ("expected", "found", "needed"):
                self.assertIn(word, text)

    def test_a_refused_resource_read_is_an_error_object_carrying_the_parts(self):
        """G#2: the envelope is asserted, not the words in the raw JSON.

        resources/read has no content field, so a tools/call body hides
        the refusal as surely as the bare -32602 that LD-68 records.
        """
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(subject, {
                "jsonrpc": "2.0", "id": 10, "method": "resources/read",
                "params": {"uri": "coord://brief"}})
            found = answer(answers)
            self.assertNotIn("result", found, found)
            error = found["error"]
            self.assertEqual(error["code"], DOMAIN_REFUSAL)
            self.assertTrue(error["message"].startswith("session:"), error)
            data = error["data"]
            self.assertEqual(data["class"], "session")
            for part in ("expected", "found", "needed"):
                self.assertTrue(str(data[part]).strip(), f"{part} is empty: {data}")

    def test_an_unknown_resource_uri_refuses_the_same_way(self):
        with World() as world:
            subject = Subject(world)
            _result, answers = self.serve(subject, {
                "jsonrpc": "2.0", "id": 15, "method": "resources/read",
                "params": {"uri": "coord://nothing-of-the-kind"}})
            error = answer(answers)["error"]
            self.assertEqual(error["code"], DOMAIN_REFUSAL)
            self.assertEqual(error["data"]["class"], "arguments")


class CoordinationMcpCore(unittest.TestCase):
    """The seam to the coordination core, with a stub standing in for it."""

    def test_the_binding_resolves_from_the_inherited_session_id(self):
        """The Lead's ruling 5, 2026-09-16: the binding is proven here, in a
        hermetic world, not only by the live reading in the PR."""
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = subject.run("call", "coord_brief", session=SESSION)
            self.assertEqual(result.exit, 0, result.stderr)
            answered = payload(result)
            self.assertEqual(
                answered.get("participant", {}).get("session"), SESSION,
                f"the binding did not come from the environment: {answered}")

    def test_an_absent_session_id_refuses_session_and_never_guesses(self):
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = subject.run("call", "coord_brief")
            refusal_wire(
                RefusalCase(id="session-absent", argv=(), cls="session",
                            origin="contract section 2.2 and 2.3"),
                result)

    def test_the_message_verb_is_not_the_participant_resolver(self):
        """X#1's surviving half, 20260916T075518Z-review-codex-c22c2d#1: the
        core's message verb and its participant resolver share the word
        `resolve`. A projection that looked the verb up on the module
        would answer a message resolution with the resolver."""
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = subject.run(
                "call", "coord_resolve",
                '{"message": "M-1", "evidence": "abc1234"}', session=SESSION)
            self.assertEqual(result.exit, 0, result.stderr)
            self.assertEqual(payload(result).get("verb"), "message-resolve")

    def test_a_core_with_no_verb_table_refuses_state_and_names_it(self):
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE_NO_TABLE)
            result = subject.run("call", "coord_brief", session=SESSION)
            refusal = refusal_wire(
                RefusalCase(id="table-absent", argv=(), cls="state",
                            named=("CORE",),
                            origin="X#1 and the seam ruling, 2026-09-16"),
                result)
            self.assertIn("CORE", refusal.line)

    def test_the_cli_wrapper_table_is_not_this_server_s_layer(self):
        """The core keeps VERBS beside CORE for its own CLI. A projection
        that reached for it would call a wrapper that prints and exits."""
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = subject.run("call", "coord_brief", session=SESSION)
            self.assertEqual(result.exit, 0, result.stderr)
            self.assertNotEqual(payload(result).get("verb"), "the-cli-wrapper")

    def test_a_verb_absent_from_the_table_refuses_state_and_names_it(self):
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = subject.run("call", "coord_outstanding", session=SESSION)
            refusal = refusal_wire(
                RefusalCase(id="verb-absent", argv=(), cls="state",
                            named=("outstanding",),
                            origin="contract section 7, the core edge"),
                result)
            self.assertIn("outstanding", refusal.line)

    def test_a_response_over_the_bound_answers_the_unavailable_reading(self):
        """Pins: the bound is measured on the serialised JSON-RPC response,
        not on the payload inside it.

        Planted fault: a payload of about 12,000 bytes, which is under the
        16,384 bound on its own and over it once tools/call carries it
        twice, as content text and as structuredContent. Measured at
        40d340b2: payload 12,013 bytes, response 24,140 bytes, and the
        server answered it whole (G2#1 and X2#3).
        """
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = run(subject.argv("serve"), cwd=world.root,
                         env=subject.env(session=SESSION),
                         stdin=json.dumps({
                             "jsonrpc": "2.0", "id": 30, "method": "tools/call",
                             "params": {"name": "coord_show",
                                        "arguments": {"assignment": "A-1"}}}) + "\n",
                         timeout=TIMEOUT)
            answered = json.loads(result.stdout.splitlines()[0])
            self.assertLessEqual(
                len(json.dumps(answered, separators=(",", ":")).encode("utf-8")),
                RESPONSE_BYTES,
                "the whole response is over the bound it declares")
            self.assertEqual(
                answered["result"]["structuredContent"].get("state"),
                "unavailable")

    def test_a_paged_read_returns_one_page_and_a_cursor(self):
        """Pins: the server pages the verb's whole list itself, ordered by
        id, and returns the last id as the cursor while rows remain.

        Planted fault: the stub verb raises TypeError on any argument, so a
        server that forwarded the cursor to the core fails here; and it
        returns 130 rows, so a server that returned the list whole fails on
        the count. Before this, cursor was a schema word nothing read
        (X2#3).
        """
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            first = payload(subject.run("call", "coord_assignments",
                                        session=SESSION))
            self.assertEqual(len(first["assignments"]), 50)
            self.assertEqual(first["assignments"][0]["id"], 1)
            self.assertEqual(first["cursor"], "50")
            second = payload(subject.run(
                "call", "coord_assignments", json.dumps({"cursor": "50"}),
                session=SESSION))
            self.assertEqual(second["assignments"][0]["id"], 51)
            self.assertEqual(second["cursor"], "100")
            last = payload(subject.run(
                "call", "coord_assignments", json.dumps({"cursor": "100"}),
                session=SESSION))
            self.assertEqual(len(last["assignments"]), 30)
            self.assertIsNone(last["cursor"])

    def test_a_cursor_that_is_not_digits_refuses_arguments(self):
        """The planted fault beside the page above: a cursor the server
        cannot have issued."""
        with World() as world:
            subject = Subject(world)
            subject.core(STUB_CORE)
            result = subject.run(
                "call", "coord_assignments", json.dumps({"cursor": "../etc"}),
                session=SESSION)
            refusal_wire(
                RefusalCase(id="cursor-not-digits", argv=(), cls="arguments",
                            origin="X2#3, the cursor is the server's own"),
                result)


class CoordinationMcpMockFidelity(unittest.TestCase):
    """The mock is contract-verified or it is nothing.

    docs/verified-couplings.md obligation 3. The stub stands in for the
    core only where a fault must be planted that the real core cannot
    produce — an absent table, an absent verb, an absent core. Everywhere
    else the suite runs the real thing. This case is what keeps the stub
    honest.
    """

    def test_every_stub_verb_has_the_real_core_s_signature(self):
        """Pins the stub's call SHAPE against the core's, and nothing more.

        What it proves: the stub takes the same keywords, with the same
        defaults and the same names. What it does not prove, corrected
        here after grok#3 and a95a8d#5 said so: that the stub refuses the
        VALUES the core refuses. A signature-equal `submit` stub still
        answers success for generation='nope', which the core refuses
        class arguments. Only the real-core cases below hold behaviour;
        this one holds shape.

        Planted fault: the previous stub took **arguments on every verb,
        which is what hid X2#1 and X2#2 through two review rounds. Any
        return to that shape fails here.
        """
        real = load_core()
        with World() as world:
            subject = Subject(world)
            stub = load_module(subject.core(STUB_CORE), "stub_core_under_test")
            self.assertTrue(stub.CORE, "the stub declares no verbs")
            for wire, stub_verb in stub.CORE.items():
                with self.subTest(verb=wire):
                    self.assertIn(wire, real.CORE,
                                  "the stub stands in for a verb the core has not")
                    self.assertEqual(
                        inspect.signature(stub_verb),
                        inspect.signature(real.CORE[wire]),
                        f"the stub's `{wire}` does not take what the core's does")

    def test_the_stub_resolver_has_the_real_resolver_s_signature(self):
        real = load_core()
        with World() as world:
            subject = Subject(world)
            stub = load_module(subject.core(STUB_CORE), "stub_resolver_under_test")
            self.assertEqual(
                inspect.signature(stub.bound_participant),
                inspect.signature(real.bound_participant))


class TheProjectionNamesEveryCoreVerb(unittest.TestCase):
    """A verb the core grows is advertised or named unprojected, never neither.

    Why it exists, measured on this branch: the core gave `offer` a
    generation gate at 73e47041 and the catalogue did not follow, so
    `coord_offer` was the one assignment write an MCP caller could make
    without naming the generation it believes. The same merge carried
    `revoke` and `undelivered`, which this server neither advertises nor
    names. Both were found by a person reading two files side by side,
    which is what this case replaces: the core is package B's and moves
    without asking this package, so the lag is the normal case and not the
    accident.

    The contract already promises that what slice one leaves out is "named
    so the omission is visible rather than discovered". Nothing held it to
    that promise. This does.

    Planted fault: delete any name from the contract's unprojected list, or
    add a verb to the core without deciding either way, and the subTest for
    that verb fails naming it. The second case is the other direction of the
    same obligation — a name in the list that the core does not have is a
    dangling citation, and it fails too.
    """

    def unprojected(self):
        """The verbs the contract says slice one leaves out, read from it."""
        text = LEVER.read_text(encoding="utf-8")
        marker = "Not projected in slice one"
        self.assertIn(marker, text,
                      "the contract no longer names what it leaves out")
        start = text.index(marker)
        end = text.index("\n\n", start)
        return set(re.findall(r"`([\w-]+)`", text[start:end]))

    def test_every_core_verb_is_advertised_or_named_unprojected(self):
        core = load_core()
        advertised = {verb for _name, verb, _mutates in CATALOGUE}
        named = self.unprojected()
        for verb in sorted(core.CORE):
            with self.subTest(verb=verb):
                self.assertTrue(
                    verb in advertised or verb in named,
                    f"the core's `{verb}` is neither projected by this server "
                    f"nor named in the contract's unprojected list, so a "
                    f"caller learns it exists only by reading the core")

    def test_the_unprojected_list_names_no_verb_the_core_lacks(self):
        core = load_core()
        for verb in sorted(self.unprojected()):
            with self.subTest(verb=verb):
                self.assertIn(
                    verb, core.CORE,
                    f"the contract leaves out `{verb}`, which the core does "
                    f"not have: a dangling citation reads as coverage")


class CoordinationMcpRealCore(unittest.TestCase):
    """Every tool against the real core, over a real store.

    The store is this world's own: XDG_STATE_HOME points into it and a
    planted lane conf names the lane, the way the core's own suite does
    it, so no case touches the host's store. The fixture walks an
    assignment through the core's own verbs, so what a case starts from
    is the core's behaviour and not an arrangement of rows.
    """

    def setUp(self):
        self.world = World(prefix="coord-mcp-real-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")

    def env(self, session=None, identity=None):
        extra = {"LANE_CONF": str(self.conf)}
        if session:
            extra["CLAUDE_CODE_SESSION_ID"] = session
            extra["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        if identity:
            extra["WF_AGENT"] = identity
        return self.world.env(extra=extra)

    def cli(self, *argv, session=LEAD_SESSION, expect=0):
        """The core's own CLI, which is how the fixture moves state."""
        result = run([sys.executable, str(CORE_TOOL), *argv],
                     cwd=self.world.cwd,
                     env=self.env(session, SHARED_AGENT), timeout=TIMEOUT)
        if expect is not None:
            self.assertEqual(result.exit, expect, f"{argv}: {result.stderr}")
        return result

    def mcp(self, *argv, session=None, config=None):
        """This server, run with no WF_AGENT at all: it reads none, by
        ruling, and a case that supplied one would hide a dependence."""
        head = [sys.executable, "-B", str(TOOL)]
        if config is not None:
            head += ["--config", str(config)]
        return run([*head, *argv], cwd=self.world.cwd,
                   env=self.env(session), timeout=TIMEOUT)

    def lock(self, branch=FIXTURE_BRANCH):
        result = run([sys.executable, str(LOCK_LEVER), "add", branch,
                      "--why", "the package C suite"],
                     cwd=self.world.cwd,
                     env=self.env(LEAD_SESSION, SHARED_AGENT), timeout=TIMEOUT)
        self.assertEqual(result.exit, 0, result.stderr)

    # -- the walk-through fixture -----------------------------------------

    def bound(self):
        """Lead and worker, one agent string, two sessions."""
        if "PT-2" in self.cli("participants").stdout:
            return
        self.cli("bind", "--roles", "lead,coordinator", session=LEAD_SESSION)
        self.cli("bind", "--roles", "worker", session=WORKER_SESSION)

    def offered(self, to="PT-2"):
        self.bound()
        out = self.cli("draft", "the package C suite", "--outcome",
                       "every tool runs against the core", "--revision",
                       FIXTURE_REVISION, session=LEAD_SESSION).stdout
        assignment = out.split()[-1]
        self.cli("offer", assignment, "--to", to, "--generation", "1",
                 session=LEAD_SESSION)
        return assignment

    def generation(self, assignment) -> str:
        """The assignment's current generation, read from the core.

        Every move bumps it — `offer` takes a new assignment from 1 to 2 —
        so a fixture that named a number would be a stale holder the first
        time the core added a transition. It asks instead.
        """
        shown = self.cli("show", assignment, session=LEAD_SESSION).stdout
        found = re.search(r"generation (\d+)", shown)
        if not found:
            raise AssertionError(
                f"the core's show no longer prints a generation: {shown!r}")
        return found.group(1)

    def held(self):
        self.lock()
        assignment = self.offered()
        self.cli("claim", assignment, "--branch", FIXTURE_BRANCH,
                 "--generation", self.generation(assignment),
                 session=WORKER_SESSION)
        return assignment

    # -- the rewritten cases ----------------------------------------------

    def test_a_read_reaches_the_core_over_the_real_store(self):
        """Pins: a read tool returns what the core holds, so the reader
        handle reaches the verb and the verb's answer reaches the caller.

        Planted fault: the assignment exists only because the fixture drove
        the core's own verbs to create it, so a server that answered from
        nothing, or that passed a handle the verb ignored, returns a list
        without it. Replaces a case that read the resolver's own copy of
        its handle and asserted nothing about the verb (G2#3).
        """
        assignment = self.held()
        # The core filters to what the asking participant holds, so the
        # holder asks: a case that queried as the lead would read an empty
        # list and call it a defect in this server.
        result = self.mcp("call", "coord_assignments", session=WORKER_SESSION)
        self.assertEqual(result.exit, 0, result.stderr)
        rows = json.loads(result.stdout)["assignments"]
        self.assertIn(assignment, [row["assignment"] for row in rows])

    def test_a_mutation_reaches_the_core_and_the_core_keeps_it(self):
        """Pins: a mutation tool gets the writer handle and its write is
        durable — the core sees it afterwards, through its own CLI.

        Planted fault: the assertion reads the row back through a separate
        process, so a server that wrote to a handle nobody committed, or
        answered without writing, fails. writer() was never asserted before
        (G2#3).
        """
        self.bound()
        result = self.mcp("call", "coord_draft", json.dumps(
            {"title": "written over MCP", "outcome": "the row is there"}),
            session=LEAD_SESSION)
        self.assertEqual(result.exit, 0, result.stderr)
        assignment = json.loads(result.stdout)["assignment"]
        self.assertIn(assignment, self.cli("assignments").stdout)

    def test_show_reaches_the_core_though_the_core_calls_it_target(self):
        """Pins: the projection renames `assignment` to the core's `target`.

        Planted fault: the core takes `target` and this server's wire says
        `assignment`; a projection that forwarded the wire name raises
        TypeError inside the core, which is X2#1's shape.
        """
        assignment = self.offered()
        result = self.mcp("call", "coord_show",
                          json.dumps({"assignment": assignment}),
                          session=LEAD_SESSION)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["assignment"], assignment)

    def test_a_generation_the_wire_carries_as_an_integer_reaches_the_gate(self):
        """Pins: the wire keeps `generation` an integer, as a JSON caller
        expects, and the core's gate receives the string it parses.

        Planted fault: the core's gate calls .isdigit(), so an integer
        raises AttributeError inside the core (X2#1). The clean neighbour
        is the same call with the right generation, which submits.
        """
        assignment = self.held()
        stale = self.mcp("call", "coord_submit", json.dumps(
            {"assignment": assignment, "evidence": FIXTURE_REVISION,
             "revision": FIXTURE_REVISION, "generation": 9}),
            session=WORKER_SESSION)
        refusal_wire(
            RefusalCase(id="stale-generation", argv=(), cls="generation",
                        origin="X2#1, the gate parses what the wire sends"),
            stale)
        good = self.mcp("call", "coord_submit", json.dumps(
            {"assignment": assignment, "evidence": FIXTURE_REVISION,
             "revision": FIXTURE_REVISION,
             "generation": int(self.generation(assignment))}),
            session=WORKER_SESSION)
        self.assertEqual(good.exit, 0, good.stderr)

    def test_every_assignment_write_refuses_without_a_generation(self):
        """Pins: the generation is required at the call, not merely named
        in a schema.

        Planted fault: each of the seven tools called without it. The case
        it replaces asserted the key was in the inputSchema, which a server
        that advertised it and never enforced it would pass.
        """
        assignment = self.held()
        for name in GENERATION_TOOLS:
            with self.subTest(tool=name):
                result = self.mcp("call", name, json.dumps(
                    {"assignment": assignment, "evidence": FIXTURE_REVISION,
                     "candidate": FIXTURE_REVISION, "revision": FIXTURE_REVISION,
                     "observation": "a reading", "findings": "#242",
                     "why": "a reason", "branch": FIXTURE_BRANCH}),
                    session=WORKER_SESSION)
                refusal = refusal_wire(
                    RefusalCase(id=f"no-generation-{name}", argv=(),
                                cls="arguments",
                                origin="the audit of 2026-09-16, a schema pin"),
                    result)
                self.assertIn("generation", refusal.line)

    def test_no_resource_reaches_a_mutating_tool(self):
        """Pins: the promise of section 0, that a client cannot act through
        resources/read, asked of the running server and of its own table.

        Planted fault: a mutating row added to the server's RESOURCES in a
        staged copy of its source, which must make resources/list refuse
        class state. The case this replaces compared two test-local
        constants and never ran the subject — the fault this file's own
        rule names, committed in the fix for that very fault (grok#4,
        a95a8d#6).
        """
        with World() as world:
            subject = Subject(world)
            listed = answer(serve_once(
                subject, {"jsonrpc": "2.0", "id": 40,
                          "method": "resources/list"}))["result"]["resources"]
            self.assertTrue(listed, "the server lists no resources at all")
            names = {item["name"] for item in listed}
            self.assertFalse(names & set(MUTATIONS),
                             f"a listed resource reaches a mutation: {names}")

            # The fault, planted in the server's own table.
            source = subject.path.read_text(encoding="utf-8")
            anchor = 'RESOURCES = (\n'
            self.assertIn(anchor, source, "the server's resource table moved")
            planted = source.replace(
                anchor,
                anchor + '    ("coord://draft", "coord_draft", "planted"),\n',
                1)
            subject.path.write_text(planted, encoding="utf-8")
            error = answer(serve_once(
                subject, {"jsonrpc": "2.0", "id": 41,
                          "method": "resources/list"}))["error"]
            self.assertEqual(error["data"]["class"], "state",
                             "the server listed a mutating resource")

    def test_a_config_naming_a_participant_the_core_lacks_refuses_participant(self):
        """Pins: a config binding whose participant the core does not hold
        refuses class participant, against the core's own answer.

        Planted fault: PT-99, which the fixture never binds. The case this
        replaces planted {"id": "PT-7"} in a bare list, a shape the core
        never returns, so it certified the mock (X2#2).
        """
        self.bound()
        config = self.world.plant_text("absent.json", json.dumps(
            binding(participant="PT-99", capabilities=["coord_brief"])))
        result = self.mcp("call", "coord_brief", config=config, session=LEAD_SESSION)
        refusal = refusal_wire(
            RefusalCase(id="participant-absent", argv=(), cls="participant",
                        origin="X2#2, 20260916T084315Z-review-codex-b317d7#2"),
            result)
        self.assertIn("PT-99", refusal.line)

    def test_configured_participant_with_correct_capability_reads_own_brief(self):
        """The clean neighbour: the expected participant authenticates in core."""
        self.bound()
        config = self.world.plant_text("present.json", json.dumps(
            binding(participant="PT-1", capabilities=["coord_brief"])))
        result = self.mcp("call", "coord_brief", config=config, session=LEAD_SESSION)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["participant"]["participant"], "PT-1")
        self.assertNotIn(LEAD_SESSION, result.stdout)
        self.assertNotIn(self.env(LEAD_SESSION)["COORDINATION_CAPABILITY"], result.stdout)

    def schemas(self):
        """Every tool's advertised properties, asked of the server itself.

        A table of schemas kept in this file would be the fault this round
        is about: the case must read what the server advertises, not what
        the case remembers.
        """
        result = run([sys.executable, "-B", str(TOOL), "serve"],
                     cwd=self.world.cwd, env=self.env(LEAD_SESSION),
                     stdin=json.dumps({"jsonrpc": "2.0", "id": 1,
                                       "method": "tools/list"}) + chr(10),
                     timeout=TIMEOUT)
        listed = json.loads(result.stdout.splitlines()[0])["result"]["tools"]
        return {item["name"]: item["inputSchema"]["properties"]
                for item in listed}

    def test_every_advertised_keyword_composes_against_the_core(self):
        """Pins: everything this server advertises, the core accepts.

        Planted fault: `mine`, which the catalogue advertised and
        core_assignments never took, so a caller sending it was refused
        class state — a seam mismatch wearing a domain refusal (grok#1,
        a95a8d#2). This sends every advertised keyword of every tool to
        the real core and fails on class `state`, which is the class the
        composition uses when the seam does not line up. A domain refusal
        is fine here: the point is that the call reaches the core's own
        gates rather than dying at the projection.
        """
        self.bound()
        sample = {
            "assignment": "A-1", "branch": FIXTURE_BRANCH, "generation": 1,
            "ttl": 60, "why": "a reason", "evidence": FIXTURE_REVISION,
            "candidate": FIXTURE_REVISION, "revision": FIXTURE_REVISION,
            "findings": "#242", "title": "a title", "outcome": "an outcome",
            "requirement": "R-1", "depends": "A-2", "duty": "a duty",
            "scope": "a scope", "inputs": "in/x", "to": "PT-2",
            "purpose": "inform", "body": "a body", "decision": "DC-1",
            "reply_to": "M-1", "artifact": FIXTURE_REVISION, "message": "M-1",
            "observation": "a reading", "execution": "20260916T010203Z-job-x",
            "environment": "the world", "state": "held", "cursor": "1",
            "stale": False, "unresolved": True, "key": "k-1",
        }
        schemas = self.schemas()
        self.assertTrue(schemas, "the server advertises no tools")
        for name, schema in sorted(schemas.items()):
            with self.subTest(tool=name):
                arguments = {key: sample[key] for key in schema
                             if key in sample}
                self.assertEqual(
                    sorted(arguments), sorted(schema),
                    f"this case has no sample value for {sorted(set(schema) - set(sample))}")
                result = self.mcp("call", name, json.dumps(arguments),
                                  session=LEAD_SESSION)
                if result.exit == 0:
                    continue
                self.assertFalse(
                    result.stderr.startswith("state:"),
                    f"{name} does not compose against the core: {result.stderr}")

    def test_the_server_needs_no_agent_identity_of_its_own(self):
        """Pins: the binding is the session's, not WF_AGENT's. Every call
        in this class already runs without WF_AGENT; this names it.

        Planted fault: none is needed — the absence is the fault. A server
        that read WF_AGENT would fail every case here, because the
        environment has none.
        """
        self.bound()
        result = self.mcp("call", "coord_brief", session=LEAD_SESSION)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertNotIn("WF_AGENT", self.env(LEAD_SESSION))


if __name__ == "__main__":
    unittest.main()
