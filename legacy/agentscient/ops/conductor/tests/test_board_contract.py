#!/usr/bin/env python3
"""Executable U0 acceptance driver for the board contract and fault corpus."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from ops.testkit import World, run

CONDUCTOR = Path(__file__).resolve().parents[1]
CONTRACT = CONDUCTOR / "contracts" / "board.cue"
EXAMPLE = CONDUCTOR / "contracts" / "examples" / "board.json"
FAULTS = CONDUCTOR / "contracts" / "faults"

EXPECTED_SECTIONS = {"branches", "prs", "decisions", "dispatches"}
EXPECTED_OWNER_DECISIONS = {
    "merge-pr-83": (
        "Merge PR #83 (`harness-fixes/dev` → `dev`) — trailers fixed and "
        "force-pushed; merging unblocks G-PIN and closes the `launch.py:324` "
        "finding on PR #81."
    ),
    "pr-81-codeql-settings": (
        "PR #81's CodeQL failures — needs the repository-settings change "
        "(default setup off, advanced upload on) that only the owner can make."
    ),
    "e2e-fixture-repository": (
        "The GitHub fixture repository for the E2E second tier — deferred behind "
        "Gitea, and gated on the isolation condition in the `e2e-fixture` plan."
    ),
    "reasoning-role-tree-mutation": (
        'Whether "a role that reasons does not mutate the tree" becomes a standing '
        'ratified rule (from `github.com/minspec`: "AI reasons. Commands mutate.").'
    ),
}
MARKER = re.compile(
    r"^- \[ \] <!-- owner-decision:([a-z0-9]+(?:-[a-z0-9]+)*) --> (.+)$"
)


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: top-level JSON value is not an object")
    return value


def cue_accepts(world: World, contract: Path, path: Path) -> tuple[bool, str]:
    cue = world.require("cue")
    completed = run(
        [cue, "vet", str(contract), str(path), "-d", "#Board"],
        cwd=world.cwd,
        env=world.env(),
        timeout=30,
    )
    return completed.exit == 0, completed.stderr.strip()


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None


def evidence_refs(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    evidence = value.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [item for item in evidence if isinstance(item, str)]


def relational_refusals(document: dict[str, Any]) -> list[str]:
    refusals: list[str] = []
    observations = document.get("observations")
    if not isinstance(observations, dict):
        return ["board.evidence-reference"]

    for observation in observations.values():
        if not isinstance(observation, dict):
            refusals.append("board.observation-command")
            continue
        command = observation.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            refusals.append("board.observation-command")
        started = timestamp(observation.get("started_at"))
        ended = timestamp(observation.get("ended_at"))
        if started is None or ended is None or ended < started:
            refusals.append("board.observation-time")

    sections = document.get("sections")
    if not isinstance(sections, dict):
        return refusals + ["board.query-sections"]
    query = document.get("query")
    expected = EXPECTED_SECTIONS if query == "all" else {query}
    if set(sections) != expected:
        refusals.append("board.query-sections")

    for name, section in sections.items():
        if not isinstance(section, dict):
            refusals.append("board.absent-is-not-none")
            continue
        state = section.get("state")
        if state == "unmeasured" and "items" in section:
            refusals.append("board.absent-is-not-none")
        if state in {"measured", "cached"} and "items" not in section:
            refusals.append("board.absent-is-not-none")
        if state == "cached":
            custody = {
                "observed_at",
                "read_at",
                "age_seconds",
                "successful_observation",
                "current_observation",
            }
            if not custody.issubset(section):
                refusals.append("board.cache-custody")
        if name == "decisions" and state in {"measured", "cached"}:
            ids = [
                item.get("id")
                for item in section.get("items", [])
                if isinstance(item, dict)
            ]
            if len(ids) != len(set(ids)):
                refusals.append("board.owner-decision-id")

    evidence_holders: list[dict[str, Any]] = []
    repository = document.get("repository")
    if isinstance(repository, dict):
        evidence_holders.append(repository)
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        evidence_holders.append(section)
        for item in section.get("items", []):
            if isinstance(item, dict):
                evidence_holders.append(item)
    known = set(observations)
    missing_evidence = any(not evidence_refs(holder) for holder in evidence_holders)
    unknown_evidence = any(
        ref not in known
        for holder in evidence_holders
        for ref in evidence_refs(holder)
    )
    if missing_evidence or unknown_evidence:
        refusals.append("board.evidence-reference")

    return list(dict.fromkeys(refusals))


def owner_markers(todo: Path) -> list[str]:
    found: dict[str, str] = {}
    duplicate = False
    for line in todo.read_text(encoding="utf-8").splitlines():
        match = MARKER.match(line)
        if match is None:
            continue
        decision_id, question = match.groups()
        duplicate = duplicate or decision_id in found
        found[decision_id] = question
    if duplicate or found != EXPECTED_OWNER_DECISIONS:
        return ["board.owner-decision-id"]
    return []


def todo_text() -> str:
    return "".join(
        f"- [ ] <!-- owner-decision:{decision_id} --> {question}\n"
        for decision_id, question in EXPECTED_OWNER_DECISIONS.items()
    )


CONTROLS = (
    ("missing-command", "board.observation-command", False),
    ("cached-without-age", "board.cache-custody", False),
    ("unmeasured-with-items", "board.absent-is-not-none", False),
    ("duplicate-owner-id", "board.owner-decision-id", True),
)


class BoardContract(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="board-contract-")
        self.addCleanup(self.world.close)
        self.world.require("cue")
        self.contract = self.world.stage(CONTRACT, "board.cue")
        self.example_path = self.world.stage(EXAMPLE, "board.json")
        self.faults = {
            name: self.world.stage(FAULTS / f"{name}.json", f"{name}.json")
            for name, _, _ in CONTROLS
        }
        self.todo = self.world.plant_text("TODO.md", todo_text())

    def test_measured_empty(self):
        example = load(self.example_path)
        accepted, diagnostic = cue_accepts(
            self.world, self.contract, self.example_path,
        )
        self.assertTrue(accepted, diagnostic)
        self.assertEqual(relational_refusals(example), [])
        measured_empty = any(
            section.get("state") == "measured" and section.get("items") == []
            for section in example.get("sections", {}).values()
            if isinstance(section, dict)
        )
        self.assertTrue(measured_empty, "clean example has no measured empty control")

    def test_missing_command(self):
        self._fault("missing-command", "board.observation-command", False)

    def test_cached_without_age(self):
        self._fault("cached-without-age", "board.cache-custody", False)

    def test_unmeasured_with_items(self):
        self._fault("unmeasured-with-items", "board.absent-is-not-none", False)

    def test_duplicate_owner_id(self):
        self._fault("duplicate-owner-id", "board.owner-decision-id", True)

    def test_owner_markers(self):
        self.assertEqual(owner_markers(self.todo), [])

    def _fault(self, name: str, expected_refusal: str, cue_should_accept: bool):
        path = self.faults[name]
        document = load(path)
        cue_ok, diagnostic = cue_accepts(self.world, self.contract, path)
        self.assertEqual(
            cue_ok,
            cue_should_accept,
            f"{name}: unexpected CUE result ({diagnostic or 'accepted'})",
        )
        actual = relational_refusals(document)
        self.assertEqual(
            actual, [expected_refusal],
            f"{name}: expected {expected_refusal}, got {actual}",
        )


if __name__ == "__main__":
    unittest.main()
