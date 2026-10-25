"""Job-entry pins for check-tests on the wire.

Written from contract items 1 and 2 of
`.dev/ladder-inputs/scope-item31-20260911.md`. Reads jobs.json as the
job catalogue, not the driver.

Item 1: check-tests delivers the JSON envelope on stdout, artifacts
empty, no file written; the prompt asks for exactly that. Item 2: each
finding is a quotation pair; the prompt asks for both quotes.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

OTHER_JOBS = (
    "adjudicate",
    "adversarial-review",
    "author-tests",
    "implement",
    "plan",
    "security-audit",
    "sweep",
    "verify",
)
# Canonical dump of the eight entries other than check-tests; re-pinned by
# the implementer under grant 149 after the plan and adjudicate entries
# declared their outputs (item 5 as amended), and again under grant 155
# (2026-09-12) after caps.turns joined five entries, said in the commit.
# Re-pinned 2026-09-22 (grant 332): every job's caps.timeout moved to
# 3600 (dispatch CONTRACT.md, owner 2026-09-22). LD-31 stays open: this
# digest moves with any entry, a representation pin the contract does not own.
OTHERS_SHA256 = (
    "7c436d7a0a4c25eed0310d86cec674a74e278badd286068b4871e5d016d1cc05"  # previous: 1a6e9f32 re-pinned 2026-09-23 (grant 332): adversarial-review and security-audit ask for the practical label; before that, previous: d9c37a95 re-pinned 2026-09-16 (grant 251) twice in one change: every job declares caps (verify, sweep, plan, adjudicate joined), then the author-tests brief gained its reading discipline; through 5ccb2b8e, from ff31dad2 (2026-09-15, the author-tests spend discipline, T-56 grant 246)
)

D1_PROMPT = (
    "Adopt the role card at ops/process/roles/test-skeptic.md in the tree "
    "at {ref}. Judge the tests at {inputs} against the contract named here: "
    "{scope}. Do not read any implementation. Refute by default: a test "
    "survives only when you can name the broken implementation it would "
    "catch. Every refuted or weak test is one finding, a quotation pair: "
    "where is the test file and line; claim is `declared (<location>): "
    "<the contract or test words, verbatim>; practiced: <the test's "
    "evidence, verbatim>; misses: <the broken implementation the test "
    "would not catch>`; reproduce is a command against the tree. Verdict "
    "is changes exactly when findings is non-empty and approve otherwise; "
    "counts are the tally of findings. Write no file: artifacts is an "
    "empty object and the envelope on stdout is the whole deliverable. "
    "Review only — do not edit."
)

D1_DELIVERABLE = (
    "the skeptic's findings on the wire: the JSON envelope on stdout, "
    "verdict `changes` exactly when any finding exists, each finding "
    "a quotation pair; artifacts empty; no file written"
)

ENVELOPE_CLAUSE = (
    "Answer with a single JSON object and nothing else, carrying exactly "
    "these keys: job, status, verdict, counts, findings, artifacts, spend, "
    "stamp, note"
)

SPEND_TO_THE_LAUNCHER = (
    'Leave `spend` as `{"harness": "<harness>", "total": 0, "out": 0, '
    '"runs": 0}`; the launcher fills it from the session store. Do not '
    "read the store."
)

OLD_PROMPT_SENTENCE = (
    "Write the report to {out}/SKEPTIC.md and name that path in artifacts"
)
OLD_DELIVERABLE = (
    "a skeptic's report at the job's out/SKEPTIC.md; verdict changes "
    "when any test is refuted"
)

CONSTRAINTS = ["read only", "do not edit the snapshot"]

# Item 1 near-miss: the D1 paragraph plus a leftover {out} write.
ITEM1_OUT_MUTANT = D1_PROMPT + " " + OLD_PROMPT_SENTENCE

# Item 1 near-miss: prescribed paragraph plus a differently named file write.
# Survived the first frozen set (skeptic 20260911T235531Z, P2).
ITEM1_EXTRA_FILE_MUTANT = (
    D1_PROMPT + " " + SPEND_TO_THE_LAUNCHER + " " + ENVELOPE_CLAUSE
    + " Save the report to report.md"
)

_NEGATIONS = ("not", "no", "never", "don't", "do not", "without", "nothing")
_PATH_SHAPED = re.compile(
    r"\{out\}"
    r"|[\w.{}/-]*\/[\w.{}/-]+"
    r"|`[^`]+\.(?:md|txt|json|py|log)`"
    r"|\b[\w-]+\.(?:md|txt|json|py|log)\b"
)
_SENTENCE_SPLIT = re.compile(r"[.;!?\n]+")
_SUFFIX = re.compile(r"\.(md|txt|json|py|log)\b", re.IGNORECASE)
_WRITE_VERB = re.compile(
    r"\b(?:write|save|create|store|emit|put|append|dump)\b",
    re.IGNORECASE,
)


def _load_jobs(path) -> dict:
    raw = path.read_text(encoding="utf-8")
    jobs = json.loads(raw)
    if not isinstance(jobs, dict):
        raise AssertionError(f"jobs.json is not an object: {type(jobs)}")
    return jobs


def _others_digest(jobs: dict) -> str:
    others = {name: jobs[name] for name in jobs if name != "check-tests"}
    canonical = json.dumps(others, sort_keys=True, ensure_ascii=False, indent=2)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positive_write_to_path(text: str) -> str | None:
    """First sentence that is a positive write instruction to a path.

    Item 1: the prompt asks for the envelope on stdout and no file.
    Recognition is by instruction shape (a write verb and a path-shaped
    token, negated sentences excluded), not by a list of forbidden words.
    """
    protected = _SUFFIX.sub(r"·\1", text)
    for raw in _SENTENCE_SPLIT.split(protected):
        sent = raw.replace("·", ".").strip()
        if not sent:
            continue
        for verb_m in _WRITE_VERB.finditer(sent):
            path_m = _PATH_SHAPED.search(sent, verb_m.end())
            if path_m is None:
                continue
            between = sent[verb_m.end() : path_m.start()].lower()
            before = sent[max(0, verb_m.start() - 24) : verb_m.start()].lower()
            if any(re.search(rf"\b{re.escape(token)}\b", between) for token in _NEGATIONS):
                continue
            if any(re.search(rf"\b{re.escape(token)}\b", before) for token in _NEGATIONS):
                continue
            return sent
    return None


def _item1_prompt_holds(prompt: str) -> bool:
    if "{out}" in prompt:
        return False
    if OLD_PROMPT_SENTENCE in prompt:
        return False
    if _positive_write_to_path(prompt) is not None:
        return False
    if "envelope on stdout" not in prompt:
        return False
    if (
        "empty object" not in prompt
        and "artifacts is empty" not in prompt
        and "`artifacts` empty" not in prompt
    ):
        return False
    if "write no file" not in prompt.lower() and "no file written" not in prompt.lower():
        return False
    if SPEND_TO_THE_LAUNCHER not in prompt:
        return False
    if ENVELOPE_CLAUSE not in prompt:
        return False
    return all(token in prompt for token in ("{ref}", "{inputs}", "{scope}"))


def _item1_deliverable_holds(cell: str) -> bool:
    if "SKEPTIC.md" in cell:
        return False
    if OLD_DELIVERABLE in cell:
        return False
    if "the JSON envelope on stdout" not in cell:
        return False
    if "quotation pair" not in cell:
        return False
    if "artifacts empty" not in cell and "`artifacts` empty" not in cell:
        return False
    return "no file written" in cell


class _JobsPin(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="check-tests-job-")
        self.addCleanup(self.world.close)
        self.jobs_path = self.world.stage(JOBS_PATH)
        self.assertTrue(self.jobs_path.is_file(), f"jobs.json missing: {self.jobs_path}")
        self.jobs = _load_jobs(self.jobs_path)
        self.assertIn("check-tests", self.jobs)
        spec = self.jobs["check-tests"]
        self.assertIsInstance(spec, dict)
        self.spec = spec


class CheckTestsKeepsItsRole(_JobsPin):
    """Item 1 — adapter, role, snapshot, caps, and constraints stay."""

    def test_check_tests_keeps_role_read_snapshot_whole_caps_3600(self):
        mutant = {
            "role": "write",
            "snapshot": "diff",
            "caps": {"timeout": 90},
        }
        self.assertNotEqual(mutant.get("role"), "read")
        self.assertEqual(self.spec.get("role"), "read")
        self.assertEqual(self.spec.get("snapshot"), "whole")
        caps = self.spec.get("caps")
        self.assertIsInstance(caps, dict, "check-tests must declare caps")
        # Every job's caps.timeout is 3600 (dispatch CONTRACT.md, owner 2026-09-22).
        self.assertEqual(caps.get("timeout"), 3600)

    def test_check_tests_keeps_adapter_harness_and_read_constraints(self):
        mutant = {"adapter": "direct", "constraints": ["write the report"]}
        self.assertNotEqual(mutant.get("adapter"), "harness")
        self.assertEqual(self.spec.get("adapter"), "harness")
        self.assertEqual(self.spec.get("constraints"), CONSTRAINTS)

    def test_check_tests_declares_no_outputs(self):
        mutant = {
            "outputs": [
                {
                    "kind": "file",
                    "path": "{out}/SKEPTIC.md",
                    "artifact": "skeptic",
                }
            ]
        }
        self.assertTrue(mutant.get("outputs"))
        self.assertNotIn(
            "outputs",
            self.spec,
            "item 5: check-tests declares none; an outputs array is a file",
        )


class CheckTestsPromptIsTheEnvelopeOnStdout(_JobsPin):
    """Item 1 / item 2 — the prompt asks for the envelope, no file."""

    def test_a_prompt_that_still_names_out_does_not_hold(self):
        mutant = ITEM1_OUT_MUTANT
        self.assertIn(D1_PROMPT, mutant)
        self.assertIn(OLD_PROMPT_SENTENCE, mutant)
        self.assertIn("{out}", mutant)
        self.assertFalse(
            _item1_prompt_holds(mutant),
            "item 1: the D1 paragraph plus a leftover {out} write "
            "must not hold",
        )
        prompt = self.spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertNotIn(
            "{out}",
            prompt,
            "item 1: the live check-tests prompt still names {out}",
        )
        self.assertNotIn(
            OLD_PROMPT_SENTENCE,
            prompt,
            "item 1: the live prompt still instructs a SKEPTIC.md write",
        )

    def test_the_prompt_drops_out_and_asks_for_no_file(self):
        planted = ITEM1_EXTRA_FILE_MUTANT
        self.assertIn("Save the report to report.md", planted)
        self.assertNotIn("{out}", planted)
        self.assertNotIn(OLD_PROMPT_SENTENCE, planted)
        self.assertIsNotNone(
            _positive_write_to_path(planted),
            "plant must land: Save the report to report.md is a file write",
        )
        self.assertFalse(
            _item1_prompt_holds(planted),
            "item 1: a prompt that appends a file write must not hold",
        )
        prompt = self.spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip(), "check-tests prompt is empty")
        self.assertNotIn(
            "{out}",
            prompt,
            "item 1: the prompt drops {out}; a remaining slot is a file",
        )
        self.assertNotIn(
            OLD_PROMPT_SENTENCE,
            prompt,
            "item 1: the prompt must not instruct a SKEPTIC.md write",
        )
        self.assertIsNone(
            _positive_write_to_path(prompt),
            "item 1: the prompt asks for no file; found "
            f"{_positive_write_to_path(prompt)!r}",
        )
        self.assertTrue(
            _item1_prompt_holds(prompt),
            "item 1: prompt must ask for the envelope on stdout, "
            f"artifacts empty, no file; found {prompt[:240]!r}",
        )

    def test_the_prompt_keeps_ref_inputs_and_scope(self):
        prompt = self.spec.get("prompt")
        self.assertIsInstance(prompt, str)
        for token in ("{ref}", "{inputs}", "{scope}"):
            self.assertIn(token, prompt, f"check-tests prompt missing {token}")

    def test_the_prompt_carries_the_quotation_pair_grammar(self):
        mutant = "every finding is prose naming a contract item"
        self.assertNotIn("quotation pair", mutant)
        self.assertNotIn("declared", mutant)
        prompt = self.spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertIn(
            "quotation pair",
            prompt,
            "item 2: the prompt asks for quotation-pair findings",
        )
        self.assertIn(
            "declared",
            prompt,
            "item 2: the prompt asks for the declared words with location",
        )
        self.assertIn(
            "practiced",
            prompt,
            "item 2: the prompt asks for the practiced evidence with location",
        )

    def test_the_prompt_keeps_the_spend_and_envelope_clauses(self):
        prompt = self.spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertIn(SPEND_TO_THE_LAUNCHER, prompt)
        self.assertIn(ENVELOPE_CLAUSE, prompt)


class CheckTestsDeliverable(_JobsPin):
    """Item 1 — deliverable is the envelope, not a skeptic file."""

    def test_a_deliverable_that_still_names_skeptic_md_does_not_hold(self):
        mutant = OLD_DELIVERABLE
        self.assertIn("SKEPTIC.md", mutant)
        self.assertFalse(
            _item1_deliverable_holds(mutant),
            "item 1: a SKEPTIC.md path is not the envelope on stdout",
        )
        cell = self.spec.get("deliverable")
        self.assertIsInstance(cell, str)
        self.assertNotIn(
            "SKEPTIC.md",
            cell,
            "item 1: the live deliverable still names SKEPTIC.md",
        )
        self.assertNotEqual(
            cell,
            OLD_DELIVERABLE,
            "item 1: the live deliverable is still the SKEPTIC.md form",
        )

    def test_the_deliverable_is_the_envelope_on_stdout_with_no_file(self):
        cell = self.spec.get("deliverable")
        self.assertIsInstance(cell, str)
        self.assertNotIn("SKEPTIC.md", cell)
        self.assertIn("the JSON envelope on stdout", cell)
        self.assertIn("quotation pair", cell)
        self.assertTrue(
            _item1_deliverable_holds(D1_DELIVERABLE),
            "plant: the item-1 deliverable sentence must itself hold",
        )
        self.assertTrue(
            _item1_deliverable_holds(cell),
            "item 1: deliverable is the envelope on stdout, artifacts "
            f"empty, no file written; found {cell!r}",
        )


class OtherJobsUnchanged(_JobsPin):
    """Item 1 — no other job entry changes."""

    def test_the_other_eight_entries_match_the_ref(self):
        others = {name: self.jobs[name] for name in self.jobs if name != "check-tests"}
        self.assertEqual(sorted(others), sorted(OTHER_JOBS))
        digest = _others_digest(self.jobs)
        self.assertEqual(
            digest,
            OTHERS_SHA256,
            "item 1: the other eight jobs.json entries are byte-for-byte "
            f"the ref; got {digest}",
        )


if __name__ == "__main__":
    unittest.main()
