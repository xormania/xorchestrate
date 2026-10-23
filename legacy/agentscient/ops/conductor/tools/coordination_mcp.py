#!/usr/bin/env python3
"""CLI and stdio MCP projection for acting coordination verbs.

The contract is `ops/conductor/coordination-mcp-lever.md`; the domain is
the coordination core (`coordination.py`, contract
`coordination-lever.md`), which this module projects and never
duplicates. Mutations travel only through `tools/call`; resources are
reads. Every refusal is the wire: one line, class first, then expected,
found and needed.

    coordination_mcp.py [--config FILE] check | serve | call TOOL [JSON]

This module holds no authority of its own. It checks that a request does
not name its own authority, checks the binding's capability list, checks
the arguments against a closed schema, resolves who is calling from its
own environment or from a config on its own argv, and calls one core
verb looked up by its wire name. A role or a participant named in a
request is refused: a caller that can name its own authority has none.
"""

import sys

sys.dont_write_bytecode = True

import inspect  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

try:
    from instrument import Refusal, refuse, session
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

from mcp_transport import (  # noqa: E402
    PROTOCOLS,
    _reject_constant,
)
from mcp_transport import (  # noqa: E402
    dispatch as dispatch_request,
)
from mcp_transport import (  # noqa: E402
    error as _error,
)
from mcp_transport import (  # noqa: E402
    result as _result,
)
from mcp_transport import (  # noqa: E402
    serve as serve_stdio,
)

# The whole-response bound, measured on the serialised response and not
# on the payload inside it: tools/call carries the payload twice, as
# content text and as structuredContent, so a payload under the bound can
# still leave a response over it (G2#1 with X2#3). A response over the
# bound answers the unavailable reading rather than a truncated object
# that would read as complete.
RESPONSE_BYTES = 16_384
# The most of a caller-chosen value a refusal will echo back.
ECHO_BYTES = 200
# Rows per page. The core's verbs do not page: they return the whole list
# ordered by id, and the paging below is this server's own.
PAGE_ROWS = 50
# JSON-RPC's implementation-defined server-error range is -32000..-32099.
# A domain refusal on resources/read answers with this code, its message
# the wire line and its data the four parts.
DOMAIN_REFUSAL = -32000
SERVER_NAME = "coordination"
CONFIG_VERSION = 1
CONFIG_KEYS = frozenset({
    "version", "participant", "assignment", "branch", "worktree", "record",
    "capabilities", "issued_at",
})
CONFIG_BYTES = 65_536
PARTICIPANT_RE = re.compile(r"PT-[1-9][0-9]{0,8}")
# The core exposes its verbs through a table keyed by the verb's wire
# name; a projection calls by string and never imports a function named
# for a verb. Settled against coordination.py at 05b8aebe: `CORE` is that
# table, each entry taking a store handle and the resolved participant,
# while `VERBS` beside it holds the CLI wrappers and is the wrong layer
# for this server. The participant resolver is `bound_participant`, not a
# verb, so the wire name `resolve` is the message verb alone.
CORE_TABLE = "CORE"
CORE_RESOLVE = "bound_participant"
CORE_WRITER = "writer"
CORE_READER = "reader"
INSTRUCTIONS = (
    "Acting server: these tools change durable state. Every mutation needs a "
    "binding the caller cannot supply. Evidence is not authority; every "
    "refusal carries expected, found and needed."
)
# A caller never names its own authority (contract section 3; the core
# refuses the same fault class `arguments` at its own boundary).
FORBIDDEN_ARGUMENTS = ("role", "roles", "participant", "session", "agent", "identity")


class Tool:
    """One row of the catalogue: the tool, the core verb's wire name, the
    mutation flag and the closed argument object it accepts."""

    __slots__ = ("mutates", "name", "properties", "renames", "required",
                 "strings", "summary", "verb")

    def __init__(self, name, verb, mutates, properties, required, summary,
                 renames=None, strings=()):
        self.name = name
        self.verb = verb
        self.mutates = mutates
        self.properties = properties
        self.required = required
        self.summary = summary
        # What the core spells differently, declared here rather than
        # discovered at the call: `show` takes a `target`, and the core's
        # generation gate parses a string because its own CLI hands it
        # argv. The wire keeps the type a JSON caller would expect.
        self.renames = dict(renames or {})
        self.strings = tuple(strings)


def _text(description=None):
    return {"type": "string"} if description is None else {
        "type": "string", "description": description}


_INT = {"type": "integer", "minimum": 1}
_BOOL = {"type": "boolean"}
# Every paged read carries an explicit cursor; a page with no cursor is
# an unbounded list wearing a bound.
_CURSOR = {"type": "string", "description": "the cursor a previous page returned"}

CATALOGUE = (
    Tool("coord_brief", "brief", False, {}, (),
         "This session's bounded picture: held assignments, unresolved inbox, "
         "open decisions, stale observations."),
    Tool("coord_assignments", "assignments", False,
         {"state": _text(), "to": _text(), "cursor": _CURSOR}, (),
         "A bounded page of assignments this participant holds, oldest first. "
         "There is no `mine`: the CLI's flag chooses whether to pass the bound "
         "participant, and this server always passes it, so the filter is "
         "always on."),
    Tool("coord_show", "show", False, {"assignment": _text()}, ("assignment",),
         "One assignment: its row, its marking, its observations and its open messages.",
         renames={"assignment": "target"}),
    Tool("coord_draft", "draft", True,
         {"title": _text(), "outcome": _text(), "requirement": _text(),
          "revision": _text(), "depends": _text(), "duty": _text(),
          "scope": _text(), "inputs": _text()},
         ("title", "outcome"),
         "Create an assignment. It names no recipient: offer does that."),
    Tool("coord_offer", "offer", True,
         {"assignment": _text(), "to": _text(), "generation": _INT},
         ("assignment", "to", "generation"),
         "Name the recipient of a drafted assignment. It carries the "
         "generation like every other assignment write: the core gave "
         "`offer` one at 73e47041.", strings=("generation",)),
    Tool("coord_claim", "claim", True,
         {"assignment": _text(), "branch": _text(), "generation": _INT,
          "ttl": _INT},
         ("assignment", "branch", "generation"),
         "Take the hold. The branch must be named in the deliverable lock.",
         strings=("generation",)),
    Tool("coord_release", "release", True,
         {"assignment": _text(), "why": _text(), "generation": _INT},
         ("assignment", "why", "generation"),
         "Give the hold back; bumps the generation.", strings=("generation",)),
    Tool("coord_submit", "submit", True,
         {"assignment": _text(), "evidence": _text(), "revision": _text(),
          "generation": _INT},
         ("assignment", "evidence", "revision", "generation"),
         "Submit the candidate with the reading it rests on.",
         strings=("generation",)),
    Tool("coord_verify", "verify", True,
         {"assignment": _text(), "candidate": _text(), "evidence": _text(),
          "generation": _INT},
         ("assignment", "candidate", "evidence", "generation"),
         "An independent verifier's pass, never the producer's own. Two "
         "readings that are not the same reading: `candidate` is the revision "
         "verified, `evidence` the reading of the verification run.",
         strings=("generation",)),
    Tool("coord_return", "return", True,
         {"assignment": _text(), "findings": _text(), "generation": _INT},
         ("assignment", "findings", "generation"),
         "Return the candidate to its holder with findings.",
         strings=("generation",)),
    Tool("coord_accept", "accept", True,
         {"assignment": _text(), "evidence": _text(), "generation": _INT},
         ("assignment", "evidence", "generation"),
         "The lead's acceptance. Never the producer's own.",
         strings=("generation",)),
    Tool("coord_send", "send", True,
         {"to": _text(), "purpose": _text("inform, request or finding"),
          "body": _text(), "assignment": _text(), "decision": _text(),
          "reply_to": _text(), "artifact": _text(),
          "key": _text("the caller's request key: the same key and payload return "
                       "the first message, another payload refuses idempotency")},
         ("to", "purpose", "body"),
         "Queue a durable message to a participant or a role."),
    Tool("coord_inbox", "inbox", False, {"unresolved": _BOOL, "cursor": _CURSOR}, (),
         "Messages addressed to this participant or its roles."),
    Tool("coord_ack", "ack", True, {"message": _text()}, ("message",),
         "Acknowledge receipt. An acknowledgment never closes the obligation."),
    # The core's message verb. The participant resolver shares the word
    # and is not a verb, so this maps by wire name and never by a lookup
    # on the module.
    Tool("coord_resolve", "resolve", True,
         {"message": _text(), "evidence": _text()}, ("message", "evidence"),
         "Answer the obligation, separately from the acknowledgment."),
    Tool("coord_outstanding", "outstanding", False,
         {"to": _text(), "cursor": _CURSOR}, (),
         "Requests and findings not yet resolved."),
    Tool("coord_observe", "observe", True,
         {"assignment": _text(), "observation": _text(), "revision": _text(),
          "generation": _INT, "stale": _BOOL, "execution": _text(),
          "artifact": _text(), "requirement": _text(), "environment": _text()},
         ("assignment", "observation", "revision", "generation"),
         "Record an observation against an assignment and a revision. The core "
         "refuses class revision when the assignment has moved on, and `stale` "
         "is the caller saying so on purpose.", strings=("generation",)),
)
BY_NAME = {tool.name: tool for tool in CATALOGUE}
# What a paged verb calls its list, at coordination.py 05b8aebe: the key
# is named here rather than guessed, so a verb that renames its list
# fails loudly instead of returning an unpaged whole.
PAGE_KEY = {
    "coord_assignments": "assignments",
    "coord_inbox": "messages",
    "coord_outstanding": "outstanding",
}

# The read tools a resource mirrors. Resources are reads by construction:
# a mutation has no URI, so no client can act through resources/read.
RESOURCES = (
    ("coord://brief", "coord_brief", "This session's bounded picture"),
    ("coord://assignments", "coord_assignments", "Assignments in scope"),
    ("coord://inbox", "coord_inbox", "Messages for this participant"),
    ("coord://outstanding", "coord_outstanding", "Unresolved obligations"),
)


def canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


# What a core verb calls the thing it just created or moved. A response
# over the bound keeps these and withholds the rest: a mutation that
# committed must never answer as though nothing happened, or the caller
# retries and the core writes a second row (A#2).
IDENTITY_KEYS = ("assignment", "message", "observation", "decision",
                 "resource", "participant", "cursor")


def unavailable(payload=None) -> dict:
    """The reading a response over the bound answers with.

    It carries the identity of whatever the call created or moved, so the
    caller can find the row it cannot be shown. Withholding a body is a
    bounded answer; withholding an id is a lost write.
    """
    reading = {"state": "unavailable", "reason": "the body was withheld",
               "bound": RESPONSE_BYTES}
    if isinstance(payload, dict):
        for key in IDENTITY_KEYS:
            value = payload.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                reading[key] = value
    return reading


def over_bound(message) -> bool:
    """Whether a whole serialised response is over the bound."""
    return len(canonical(message).encode("utf-8")) > RESPONSE_BYTES


def clip(value) -> str:
    """A bounded slice of a value the caller chose, for a refusal to name.

    A refusal that echoed the whole of an attacker-chosen string carried
    it past the bound the server declares (beb7b6#4).
    """
    text = repr(value)
    return text if len(text) <= ECHO_BYTES else text[:ECHO_BYTES] + "...(clipped)"


def paged(name: str, payload, cursor):
    """One page of a verb's list result, and the cursor for the next.

    The core's verbs return the whole list ordered by id; the paging is
    this server's, so a caller that asks for a page gets one and a caller
    that does not still gets a bounded answer. The cursor is the last id
    returned, and a page that ends the list carries none.
    """
    key = PAGE_KEY.get(name)
    if key is None:
        return payload
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        refuse("state", f"a list under `{key}` from the core verb",
               f"{type(payload).__name__} without it",
               f"the core verb behind {name} no longer returns `{key}`")
    rows = payload[key]
    ordered = sorted(
        rows, key=lambda row: row.get("id", 0) if isinstance(row, dict) else 0)
    if cursor is not None:
        ordered = [row for row in ordered
                   if isinstance(row, dict) and row.get("id", 0) > cursor]
    page = ordered[:PAGE_ROWS]
    answer = dict(payload)
    answer[key] = page
    last = page[-1].get("id") if page and isinstance(page[-1], dict) else None
    answer["cursor"] = (
        str(last) if last is not None and len(ordered) > len(page) else None)
    return answer


# -- the config binding ----------------------------------------------------

def load_config(path: Path, *, catalogue=None) -> dict:
    """The acting config, validated against its own exact key set.

    Its own file and its own version: the read server's config is not
    extended and its loader is not touched (contract section 2.1).
    """
    absolute = Path(os.path.abspath(path))
    try:
        if not absolute.is_file():
            refuse("config", "a regular acting config file", str(absolute),
                   "pass --config FILE naming the config the launcher wrote")
        raw = absolute.read_bytes()
    except OSError as error:
        refuse("config", "a readable acting config", f"{absolute}: {error}",
               "restore the config the launcher wrote")
    if len(raw) > CONFIG_BYTES:
        refuse("config", f"a config no larger than {CONFIG_BYTES} bytes",
               f"{len(raw)} bytes", "shorten the config")
    try:
        root = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as error:
        refuse("config", "valid UTF-8 JSON", type(error).__name__,
               "repair the config")
    if not isinstance(root, dict):
        refuse("config", f"exact keys {sorted(CONFIG_KEYS)}",
               type(root).__name__, "write the declared object")
    if root.get("version") != CONFIG_VERSION or isinstance(root.get("version"), bool):
        refuse("config", f"version {CONFIG_VERSION}", repr(root.get("version")),
               "write a config of a version this server declares")
    unknown = sorted(set(root) - CONFIG_KEYS)
    missing = sorted(CONFIG_KEYS - set(root))
    if unknown or missing:
        refuse("config", f"exact keys {sorted(CONFIG_KEYS)}",
               f"unknown {unknown}, missing {missing}",
               "add missing and remove unknown keys")
    participant = root["participant"]
    if not isinstance(participant, str) or PARTICIPANT_RE.fullmatch(participant) is None:
        refuse("config", "a participant id of the form PT-n", repr(participant),
               "name the participant id the core bound, never an agent string")
    capabilities = root["capabilities"]
    if (not isinstance(capabilities, list)
            or any(not isinstance(item, str) for item in capabilities)):
        refuse("config", "capabilities as a list of tool names",
               repr(capabilities), "write the tool names this binding may call")
    stranger = sorted(set(capabilities) - set(BY_NAME if catalogue is None else catalogue))
    if stranger:
        refuse("config", "capabilities drawn from this server's catalogue",
               f"{stranger}", "remove the names this server does not carry")
    return root


# -- the core edge ---------------------------------------------------------

def _core():
    """The coordination core beside this tool, or a reading that it is not.

    Imported late and by name: a projection that cannot reach its domain
    refuses rather than answering from nothing.
    """
    try:
        # Imported at call time, not at module load, so `check` can
        # report an absent core rather than failing to start.
        import coordination
    except ModuleNotFoundError:
        refuse("state", "the coordination core beside this tool",
               "coordination.py absent",
               "land ops/conductor/tools/coordination.py, package B")
    return coordination


def _table(core):
    """The core's verb table, keyed by wire name."""
    table = getattr(core, CORE_TABLE, None)
    if not isinstance(table, dict):
        refuse("state", f"the core's verb table `{CORE_TABLE}`",
               "absent from coordination.py",
               "land the core's verb table, keyed by each verb's wire name")
    return table


def _verb(core, wire: str):
    """One core verb, by its wire name.

    Never by a lookup on the module: the message verb `resolve` and the
    participant resolver share a word, and a projection that guessed by
    name would call the resolver when a message was meant.
    """
    found = _table(core).get(wire)
    if not callable(found):
        refuse("state", f"the core verb `{wire}`", "absent from the verb table",
               f"land the verb `{wire}` in the core's table")
    return found


# -- the binding -----------------------------------------------------------

def _store(core, tool: Tool):
    """The store handle the core hands out: a writer for a mutation, a
    reader for a read. The core owns the store; this server opens none."""
    name = CORE_WRITER if tool.mutates else CORE_READER
    opener = getattr(core, name, None)
    if not callable(opener):
        refuse("state", f"the core's store opener `{name}`",
               "absent from coordination.py",
               f"land `{name}` in the core")
    return opener()


def check_capability(config, tool: Tool) -> None:
    """The binding's allowlist, checked before the arguments are judged.

    The capability is about the tool, not its shape, so a caller whose
    binding does not carry the tool is refused before its arguments are
    read. The one check that runs earlier is the forbidden-authority
    one, because a request naming its own authority is malformed
    whoever asks. It narrows and never widens: the core's own role gate
    still runs inside every verb.
    """
    if config is None:
        return
    if tool.name not in config["capabilities"]:
        refuse("capability", f"a binding carrying {tool.name}",
               f"capabilities {sorted(config['capabilities'])}",
               "ask the dispatcher for a binding that carries this tool; the "
               "arguments are checked once the capability is held")


def resolve_binding(config, tool: Tool):
    """Who is calling, from the launcher's config or from the environment.

    Never from the request: a role or a participant in the arguments is
    refused before this is reached.
    """
    if config is not None:
        # Config names an expected participant, never authentication. Use
        # the same private-capability resolver as the unconfigured route;
        # a public catalogue is deliberately not an identity oracle.
        core = _core()
        conn = _store(core, tool)
        resolver = getattr(core, CORE_RESOLVE, None)
        if not callable(resolver):
            refuse("state", f"the core's participant resolver `{CORE_RESOLVE}`",
                   "absent from coordination.py", "restore the authenticated core resolver")
        return conn, resolver(conn, participant=config["participant"])
    # The session id is read before the core is reached: an unbound
    # session is a fault of this server's own boundary, and a core that
    # happens to be absent must not turn it into a different class.
    key = session("")
    if not key:
        refuse("session", "a session id in this server's environment",
               "unset",
               "run under a harness that sets CLAUDE_CODE_SESSION_ID, or pass "
               "--config FILE with a binding the dispatcher issued")
    # The participant resolver is a module function, not a verb: it
    # refuses class session or participant itself, and those refusals
    # surface unchanged.
    core = _core()
    resolver = getattr(core, CORE_RESOLVE, None)
    if not callable(resolver):
        refuse("state", f"the core's participant resolver `{CORE_RESOLVE}`",
               "absent from coordination.py",
               "land the participant resolution of package B")
    conn = _store(core, tool)
    return conn, resolver(conn)


# -- arguments -------------------------------------------------------------

def compose(verb, tool: Tool, participant, arguments) -> dict:
    """The keywords the core verb takes, from the arguments this server
    checked, and every one of them checked against the core's signature
    first.

    The core asks for an `agent` on the verbs whose marking records one;
    it is attribution, and it comes from the bound participant's own row,
    never from the request. A keyword the core does not accept, or a
    required keyword this server does not supply, refuses class `state`
    naming it, so a core that moves is a refusal on the wire and not a
    TypeError raised into a client that would see no response (X2#1).
    """
    kwargs = {}
    for name, value in arguments.items():
        kwargs[tool.renames.get(name, name)] = (
            str(value) if name in tool.strings else value)
    try:
        parameters = inspect.signature(verb).parameters
    except (TypeError, ValueError):
        refuse("state", f"a readable signature for core verb `{tool.verb}`",
               "the core verb is not introspectable",
               "expose the verb as a function in the core's table")
    keyword = {name: parameter for name, parameter in parameters.items()
               if parameter.kind is inspect.Parameter.KEYWORD_ONLY}
    if "agent" in keyword and "agent" not in kwargs and participant is not None:
        kwargs["agent"] = participant["agent"]
    stranger = sorted(set(kwargs) - set(keyword))
    if stranger:
        refuse("state", f"core verb `{tool.verb}` taking {sorted(keyword)}",
               f"this server passes {stranger}",
               f"settle {tool.name} against the core's signature")
    missing = sorted(
        name for name, parameter in keyword.items()
        if parameter.default is inspect.Parameter.empty and name not in kwargs)
    if missing:
        refuse("state", f"every required keyword of core verb `{tool.verb}`",
               f"missing {missing}",
               f"settle {tool.name} against the core's signature")
    return kwargs


def check_named_authority(tool: Tool, arguments) -> None:
    """A request may not name its own authority, whoever is asking.

    It runs after the capability check: an unauthorised caller learns
    that its binding does not carry the tool and nothing whatever about
    the tool's arguments, not even which ones are forbidden (G2#4).
    """
    if not isinstance(arguments, dict):
        return
    for name in FORBIDDEN_ARGUMENTS:
        if name in arguments:
            refuse("arguments", f"no caller-supplied `{name}`",
                   f"{name} in the arguments of {tool.name}",
                   "remove it: the binding names the caller, the request never does")


def check_arguments(tool: Tool, arguments) -> dict:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        refuse("arguments", f"a closed object of arguments for {tool.name}",
               type(arguments).__name__, "pass the declared arguments object")
    unknown = sorted(set(arguments) - set(tool.properties))
    if unknown:
        refuse("arguments", f"arguments drawn from {sorted(tool.properties)}",
               f"{unknown}", f"remove the arguments {tool.name} does not take")
    missing = [name for name in tool.required if name not in arguments]
    if missing:
        refuse("arguments", f"the required arguments {list(tool.required)}",
               f"missing {missing}", "supply them")
    for name, value in arguments.items():
        schema = tool.properties[name]
        wanted = schema.get("type")
        if wanted == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                refuse("arguments", f"{name} as an integer", repr(value),
                       f"pass {name} as a whole number")
            # A bound this server advertises and does not keep makes the
            # next actor guess (docs/wire.md).
            low = schema.get("minimum")
            if isinstance(low, int) and value < low:
                refuse("arguments", f"{name} of at least {low}", repr(value),
                       f"pass {name} as {low} or more")
        if wanted == "string" and not isinstance(value, str):
            refuse("arguments", f"{name} as a string", repr(value),
                   f"pass {name} as text")
        if wanted == "boolean" and not isinstance(value, bool):
            refuse("arguments", f"{name} as a boolean", repr(value),
                   f"pass {name} as true or false")
    return arguments


def call(config, name, arguments) -> dict:
    """One tool call: the tool, the caller's own words, the capability,
    the arguments, the binding, then the one core verb."""
    tool = BY_NAME.get(name)
    if tool is None:
        refuse("arguments", "a tool this server carries", clip(name),
               f"choose one of {sorted(BY_NAME)}")
    check_capability(config, tool)
    check_named_authority(tool, arguments)
    checked = check_arguments(tool, arguments)
    cursor = _cursor(tool, checked)
    conn, participant = resolve_binding(config, tool)
    verb = _verb(_core(), tool.verb)
    kwargs = compose(verb, tool, participant, checked)
    return paged(tool.name, verb(conn, participant, **kwargs), cursor)


def _cursor(tool: Tool, arguments: dict):
    """Take the cursor out of the arguments: it is this server's, and the
    core's verbs do not take one."""
    if "cursor" not in arguments:
        return None
    raw = arguments.pop("cursor")
    if not isinstance(raw, str) or not raw.isdigit():
        refuse("arguments", "a cursor of decimal digits", repr(raw),
               f"pass the cursor a previous {tool.name} page returned")
    return int(raw)


# -- the transport ---------------------------------------------------------

def _schemas() -> list:
    listed = []
    for tool in CATALOGUE:
        annotations = {
            "readOnlyHint": not tool.mutates,
            "destructiveHint": False,
            "idempotentHint": not tool.mutates,
        }
        listed.append({
            "name": tool.name,
            "description": tool.summary,
            "inputSchema": {
                "type": "object",
                "properties": dict(tool.properties),
                "required": list(tool.required),
                "additionalProperties": False,
            },
            "annotations": annotations,
        })
    return listed


def _resources() -> list:
    """The listed resources, each proven a read before it is offered.

    Section 0 promises that a client cannot act through `resources/read`.
    The promise is kept here rather than in prose: a row naming a tool
    this server marks as a mutation refuses class `state`, so the table
    cannot quietly grow one (grok#4, a95a8d#6).
    """
    listed = []
    for uri, name, description in RESOURCES:
        tool = BY_NAME.get(name)
        if tool is None:
            refuse("state", "a resource naming a tool this server carries",
                   f"{uri} names {name}", "correct the resource table")
        if tool.mutates:
            refuse("state", "a resource naming a read", f"{uri} names {name}, "
                   "a mutation", "mutations travel only through tools/call")
        listed.append({"uri": uri, "name": name, "description": description,
                       "mimeType": "application/json"})
    return listed


def _call_result(identifier, payload) -> dict:
    return _result(identifier, {
        "content": [{"type": "text", "text": canonical(payload)}],
        "structuredContent": payload,
        "isError": False,
    })


def _read_result(identifier, uri, payload) -> dict:
    return _result(identifier, {"contents": [
        {"uri": uri, "mimeType": "application/json",
         "text": canonical(payload)}]})


def _refusal_error(identifier, refusal: Refusal) -> dict:
    """A domain refusal on a method whose result shape has no room for one.

    The read server answers a bare -32602 here and throws the line away
    (LD-68); a tools/call body on a resources/read would hide it just as
    well, in a field that method's schema does not have. This carries
    the line as the message and its four parts as the data.
    """
    return _error(identifier, DOMAIN_REFUSAL, refusal.line(), {
        "class": refusal.kind,
        "expected": refusal.expected,
        "found": refusal.found,
        "needed": refusal.needed,
    })


def _refusal_result(identifier, refusal: Refusal) -> dict:
    """A domain refusal on tools/call, whose result shape carries it."""
    return _result(identifier, {
        "content": [{"type": "text", "text": refusal.line()}],
        "isError": True,
    })


def _dispatch(config, request):
    return dispatch_request(
        request, lambda identifier, method, params: _route(config, identifier, method, params),
        server={
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
            "instructions": INSTRUCTIONS,
        }, strict_initialize=True,
    )


def _route(config, identifier, method, params):
    if method == "tools/list":
        if params:
            return _error(identifier, -32602, "Invalid params")
        return _result(identifier, {"tools": _schemas()})
    if method == "resources/list":
        if params:
            return _error(identifier, -32602, "Invalid params")
        try:
            return _result(identifier, {"resources": _resources()})
        except Refusal as exc:
            return _refusal_error(identifier, exc)
    if method == "tools/call":
        if set(params) not in ({"name"}, {"name", "arguments"}) or not isinstance(
                params.get("name"), str):
            return _error(identifier, -32602, "Invalid params")
        # `arguments` is an object or it is not this method's parameter: a
        # malformed one is a protocol fault, not a domain refusal (a95a8d#3).
        if "arguments" in params and not isinstance(params["arguments"], dict):
            return _error(identifier, -32602, "Invalid params")
        try:
            payload = call(config, params["name"], params.get("arguments", {}))
        except Refusal as exc:
            return _refusal_result(identifier, exc)
        except Exception as exc:  # noqa: BLE001 — a server answers, always
            # A fault inside the core is not this server's to interpret,
            # but a client that receives no response at all cannot even
            # retry (X2#1). The type is named; the message is not, because
            # it is not this server's to disclose.
            return _error(identifier, -32603, f"Internal error: {type(exc).__name__}")
        message = _call_result(identifier, payload)
        if over_bound(message):
            # Measured on the whole response: this one carries the payload
            # twice, so the payload's own size never settles it. The
            # reading keeps the identity of what the call created, because
            # the core has already committed it.
            message = _call_result(identifier, unavailable(payload))
        return message
    if method == "resources/read":
        if set(params) != {"uri"}:
            return _error(identifier, -32602, "Invalid params")
        try:
            payload = _read_resource(config, params["uri"])
        except Refusal as exc:
            return _refusal_error(identifier, exc)
        except Exception as exc:  # noqa: BLE001 — a server answers, always
            return _error(identifier, -32603, f"Internal error: {type(exc).__name__}")
        message = _read_result(identifier, params["uri"], payload)
        if over_bound(message):
            message = _read_result(identifier, params["uri"], unavailable(payload))
        return message
    return _error(identifier, -32601, "Method not found")


def _read_resource(config, uri) -> dict:
    for listed, name, _description in RESOURCES:
        if uri == listed:
            return call(config, name, {})
    refuse("arguments", "a listed resource URI", clip(uri),
           f"select one of {[row[0] for row in RESOURCES]}")


def _send(message) -> None:
    """The one place a message leaves this server, and the one place the
    bound is enforced.

    Everything passes here: results, refusals, protocol errors, and the
    answers to `ping` and the list methods, which carry whatever id the
    caller chose. A message that cannot be sent within the bound is
    replaced by one that says so, with a null id, because an id too large
    to send cannot be echoed to correlate it (a95a8d#1, beb7b6#4).
    """
    if over_bound(message):
        message = {"jsonrpc": "2.0", "id": None, "error": {
            "code": DOMAIN_REFUSAL,
            "message": (f"state: expected a response within {RESPONSE_BYTES} "
                        f"bytes; found one over it; needed ask for less, or "
                        f"send a smaller request id"),
            "data": {"bound": RESPONSE_BYTES}}}
    sys.stdout.write(canonical(message) + "\n")
    sys.stdout.flush()


def serve(config):
    return serve_stdio(sys.stdin.buffer, lambda request: _dispatch(config, request), _send)


# -- the CLI ---------------------------------------------------------------

def check_result(config) -> dict:
    """What this server would answer with, without answering anything."""
    try:
        _core()
        core = "present"
    except Refusal:
        core = "absent"
    return {
        "server": SERVER_NAME,
        "protocols": list(PROTOCOLS),
        "tools": [tool.name for tool in CATALOGUE],
        "mutations": [tool.name for tool in CATALOGUE if tool.mutates],
        "binding": "config" if config is not None else (
            "session" if session("") else "none"),
        "core": core,
        "response_bound": RESPONSE_BYTES,
    }


def _cli(argv):
    config_path = None
    rest = list(argv)
    if rest[:1] == ["--config"]:
        if len(rest) < 2:
            refuse("arguments", "--config FILE", "--config with no file",
                   "name the config file")
        config_path = Path(rest[1])
        rest = rest[2:]
    if not rest:
        refuse("arguments", "check, serve, or call TOOL [JSON]",
               "no verb", "name one of check, serve, call")
    return config_path, rest[0], rest[1:]


def main() -> int:
    try:
        config_path, verb, rest = _cli(sys.argv[1:])
        config = load_config(config_path) if config_path is not None else None
        if verb == "check" and not rest:
            print(canonical(check_result(config)))
            return 0
        if verb == "serve" and not rest:
            return serve(config)
        if verb == "call" and len(rest) in (1, 2):
            if len(rest) == 2:
                try:
                    arguments = json.loads(rest[1], parse_constant=_reject_constant)
                except (ValueError, UnicodeError, RecursionError):
                    refuse("arguments", "a JSON object of arguments",
                           "unparseable", "pass the declared arguments as JSON")
            else:
                arguments = {}
            payload = call(config, rest[0], arguments)
            if over_bound(payload):
                payload = unavailable()
            print(canonical(payload))
            return 0
        refuse("arguments", "check, serve, or call TOOL [JSON]",
               repr([verb, *rest]), "use the declared verb and arguments")
    except Refusal as exc:
        print(exc.line(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
