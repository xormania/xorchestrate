"""Planted faults for session_adapters/grok.py (sessions-lever.md, slice 2).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. Every row of
test_session_adapters_grok.py must stay green on the clean subject, and
the rows named in each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/session_adapters/grok.py \\
        --mutants ops/conductor/tests/session_adapters_grok_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_session_adapters_grok.py"

Anchored on the subject at bf924ee7 (#277); the six permission mutants on
b3's ea986978 (A-527); M-foreign-answered on aa's 88abc6ba (A-522 #2). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/session_adapters/grok.py"

MUTANTS = [
    Mutant(
        "M-permission-allowed",
        SUBJECT,
        'outcome = ({"outcome": "selected", "optionId": reject} if reject',
        'outcome = ({"outcome": "selected", "optionId": "allow-once"} if reject',
        "the permission is answered allow-once (grok-observe-permission, "
        "20260923T020912Z-review-codex-9d8b8c #19)",
    ),
    Mutant(
        "M-channel-argv-short",
        SUBJECT,
        'return ["--no-auto-update", "agent", "stdio"]',
        'return ["agent", "stdio"]',
        "the channel argv without --no-auto-update (#19)",
    ),
    Mutant(
        "M-cwd-own",
        SUBJECT,
        "return str(Path(named).absolute()) if named else os.getcwd()",
        "return os.getcwd()",
        "session/new sends the bridge's own cwd, not the member's worktree "
        "(grok-session-new-cwd, 0e's review of b3's grok.py)",
    ),
    Mutant(
        "M-servers-empty",
        SUBJECT,
        '"mcpServers": self.servers}',
        '"mcpServers": []}',
        "session/new names no MCP server (grok-session-new-mcp-named, "
        "11's review of fed6a4ce)",
    ),
    Mutant(
        "M-ready-not-read",
        SUBJECT,
        'return f"tool {common.SERVER} ready"',
        'return "activity active"',
        "the coordination server's ready status is not read as the tool "
        "(grok-session-new-mcp-named)",
    ),
    Mutant(
        "M-coordination-declined",
        SUBJECT,
        "            if wanted is not None and once is not None:\n",
        "            if False:\n",
        "the coordination call is declined like every other "
        "(grok-permission-coordination-allowed; A-527, b3's live reading)",
    ),
    Mutant(
        "M-any-server-allowed",
        SUBJECT,
        '    prefix = f"{common.SERVER}__"\n',
        '    prefix = ""\n',
        "a use_tool call on any server is allowed (grok-permission-other-server, "
        "grok-permission-prefix-only)",
    ),
    Mutant(
        "M-empty-tool-allowed",
        SUBJECT,
        " or len(target) == len(prefix):\n",
        ":\n",
        "coordination__ with no tool is allowed (grok-permission-no-tool)",
    ),
    Mutant(
        "M-no-input-allowed",
        SUBJECT,
        '    return target[len(prefix):] if isinstance(raw.get("tool_input"), dict) else None\n',
        "    return target[len(prefix):]\n",
        "a call with no input object is allowed (grok-permission-no-input, "
        "grok-permission-input-not-object)",
    ),
    Mutant(
        "M-always-allow-chosen",
        SUBJECT,
        '                         if entry.get("kind") == "allow_once" and entry.get("optionId")), None)\n',
        '                         if str(entry.get("kind", "")).startswith("allow") and entry.get("optionId")), None)\n',
        "always-allow is chosen when offered first (grok-permission-coordination-allowed, "
        "grok-permission-no-allow-once)",
    ),
    Mutant(
        "M-any-tool-allowed",
        SUBJECT,
        '    if tool.get("name") != "use_tool" or tool.get("namespace") != MACHINERY:\n'
        "        return None\n"
        "    raw = call.get",
        "    if False:\n"
        "        return None\n"
        "    raw = call.get",
        "a call that is not grok's own use_tool is allowed (grok-permission-not-use-tool, "
        "grok-permission-other-namespace)",
    ),
    Mutant(
        "M-foreign-answered",
        SUBJECT,
        "        if other is not None:\n"
        "            # A foreign session's frame is refused before anything answers\n"
        "            # it: its permission request goes out unanswered (A-522 #2).\n"
        "            return other\n"
        "        if \"id\" in frame and method is not None:\n"
        "            return self.answer(rpc, frame, method, params, other)\n",
        "        if \"id\" in frame and method is not None:\n"
        "            return self.answer(rpc, frame, method, params, other)\n"
        "        if other is not None:\n"
        "            return other\n",
        "the order before aa's 88abc6ba (37a6a26f): a foreign session's permission "
        "request is answered before it is refused (grok-permission-foreign-coordination, "
        "grok-permission-foreign-other-tool; A-522 #2, "
        "20260923T040605Z-review-codex-6ed2cb)",
    ),
]
