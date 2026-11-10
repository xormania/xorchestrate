"""Observable outcomes for retained failure reasons, run-location readiness and the handshake probe.

Expectations here are stated independently of the test-kit: which directory
modes the product accepts for a private home, what a shareable receipt may
contain, and what an operator must be able to read after a failure.
"""

import json

# What the refusing peer answers to a native session start; the operator has to see it.
PROVIDER_REASON = "missing field `cwd`"
PROVIDER_SUMMARY = "Invalid params"

# Directory mode of the evidence root's parent -> whether a private home beneath it is acceptable.
# Group or other write permission lets another user replace the home, unless the directory is sticky.
LOCATIONS = {
    "private": (0o700, True),
    "listable": (0o755, True),
    "group-writable": (0o775, False),
    "other-writable": (0o757, False),
    "sticky-shared": (0o1777, True),
}


def code_only_receipt(receipt, text, *, code, private):
    """A shareable receipt names the private diagnostics and carries assertion codes, never reasons."""
    assert receipt["status"] == "failed", receipt
    assert receipt["error"]["code"] == code, receipt["error"]
    assert set(receipt["error"]) == {"type", "code"}
    failed = [row for row in receipt["checks"] if row["status"] == "failed"]
    assert failed, "The failing check must stay visible in the receipt"
    assert all(set(row["failure"]) == {"type", "code"} for row in failed)
    assert "diagnostics.json" in receipt["private_artifacts"], "The receipt must name the private diagnostics"
    for word in private:
        assert word not in text, f"Receipt exposes private reason text: {word!r}"


def retained_reason(document, *, check, words, tool=None):
    """The private diagnostics hold exactly one row for the failed check, with the actual reason."""
    assert document["schema_version"] == 1 and document["scope"] == "private"
    rows = [row for row in document["failures"] if row["check"] == check]
    assert len(rows) == 1, document
    text = json.dumps(rows[0])
    for word in words:
        assert word in text, f"Retained diagnostics omit {word!r}: {text}"
    if tool is not None:
        assert rows[0]["details"]["tool"] == tool
    return rows[0]


def surfaced_reason(lines, *, check, words):
    """One single-line reason names the failed check and quotes what the operator needs."""
    assert isinstance(lines, list) and all(isinstance(line, str) for line in lines), f"No failure lines surfaced: {lines!r}"
    assert all("\n" not in line for line in lines)
    matching = [line for line in lines if line.startswith(check + ":")]
    assert len(matching) == 1, lines
    for word in words:
        assert word in matching[0], f"Surfaced reason omits {word!r}: {matching[0]}"
    return matching[0]


def location_verdict(report, *, accepted, directory):
    """Every check that provisions a home reports the location; a refusal names the directory at fault."""
    rows = [(check, item) for check in report["checks"] for item in check["readiness"]
            if item["name"] == "run_location"]
    assert rows, "No selected check assessed the run location"
    assert all(item["passed"] is accepted for _, item in rows), rows
    assert report["status"] == ("passed" if accepted else "setup_error")
    for check, item in rows:
        assert check["status"] == ("passed" if accepted else "setup_error")
        if not accepted:
            assert item["directory"] == str(directory)
            assert item["needed"]
            assert str(directory) in check["reason"]


def handshake_only(methods, *, start, turn):
    """The harness saw its session start and never a turn."""
    assert start in methods, methods
    assert turn not in methods, methods


def probe_report(probe, *, passed, failed):
    """A probe result names every selected attempt with its own outcome."""
    outcomes = {row["name"]: row["status"] for row in probe["checks"]}
    assert set(outcomes) == set(passed) | set(failed), outcomes
    assert all(outcomes[name] == "passed" for name in passed), outcomes
    assert all(outcomes[name] != "passed" for name in failed), outcomes
    assert probe["status"] == ("failed" if failed else "passed")
