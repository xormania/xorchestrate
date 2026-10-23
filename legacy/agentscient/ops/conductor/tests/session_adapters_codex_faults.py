"""Planted faults for session_adapters/codex.py (sessions-lever.md, slice 2).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. Every row of
test_session_adapters_codex.py must stay green on the clean subject, and
the rows named in each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/session_adapters/codex.py \\
        --mutants ops/conductor/tests/session_adapters_codex_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_session_adapters_codex.py"

Anchored on the subject at bf924ee7 (#277). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/session_adapters/codex.py"

MUTANTS = [
    Mutant(
        "M-initialized-skipped",
        SUBJECT,
        '            rpc.notify("initialized")\n',
        "",
        "the handshake skips `initialized` (codex-handshake-thread-id, "
        "20260923T020912Z-review-codex-9d8b8c #10)",
    ),
    Mutant(
        "M-channel-argv-short",
        SUBJECT,
        'return ["app-server", "--listen", "stdio://"]',
        'return ["app-server"]',
        "the channel argv without --listen stdio:// (#16)",
    ),
    Mutant(
        "M-idle-text-dropped",
        SUBJECT,
        'params = {"threadId": self.native, "input": [{"type": "text", "text": text}]}',
        'params = {"threadId": self.native, "input": [{"type": "text", "text": "x"}]}',
        "an idle submit sends a constant instead of the text (#11)",
    ),
    Mutant(
        "M-busy-output-constant",
        SUBJECT,
        '"output": text}}',
        '"output": "tool output"}}',
        "a busy submit sends a constant toolOutput (#12)",
    ),
    Mutant(
        "M-permission-not-printed",
        SUBJECT,
        '            print(f"permission asked: {title}; answered {json.dumps(DECLINE[method])}", flush=True)\n',
        "",
        "the permission prompt is answered but not printed (#15)",
    ),
    Mutant(
        "M-permission-approved",
        SUBJECT,
        '    "item/commandExecution/requestApproval": {"decision": "decline"},',
        '    "item/commandExecution/requestApproval": {"decision": "approve"},',
        "a command approval is answered approve (#15)",
    ),
]
