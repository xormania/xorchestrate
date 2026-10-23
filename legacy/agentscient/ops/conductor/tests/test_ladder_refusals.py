"""The ladder driver's refusals as one case table (plan slice 11): every
row is a precondition the driver refuses on the wire before any
dispatch, addressed by id with its origin, judged by the kit's
refusal_wire. The subject is the staged driver on _LadderFixture and is
not read here; the rows are the sentences of ops/conductor/ladder-lever.md
§Preconditions and refusals, scope-stage-set-20260912.md and the
2026-09-10 --input brief, gathered from RefusalsBeforeLaunch in
test_ladder_lever.py, test_ladder_lever_inputs.py,
test_ladder_stage_set.py and test_ladder_skeptic_custody.py. One test
method per row, generated from the table, so each row runs on a fresh
fixture and CI reads one line per row. The planted faults are
ops/conductor/tests/ladder_faults_refusals.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from test_ladder_lever import (  # noqa: E402
    BRANCH,
    SKEPTIC_BODY,
    SKEPTIC_FINDINGS,
    WELL_FORMED_SKEPTIC_COUNTS,
    WELL_FORMED_SKEPTIC_FINDING,
    WORKTREE_ROOT,
    _LadderFixture,
)

from ops.testkit import RefusalCase, refusal_wire  # noqa: E402

START_FORM = "SCOPE BRANCH [PLAN] [--input PATH]... or resume LADDER_ID"
NEEDED_INPUT = "supply a readable input file"
NEEDED_RECORD = "restore the recorded bytes, or resume with --rescope to record the change"
NINE = ["tests", "check-tests", "tests-fix", "code", "security", "adversarial",
        "fix-tests", "fix-code", "security-re"]


def _plan(cls: str, name: str = "slice-x") -> str:
    return f"# plan\n\nslice\t{name}\tS\t{cls}\nbucket\t{name}\tS\tops/conductor/tools/ladder.py\n"


@dataclass(frozen=True)
class RefusalRow:
    id: str
    origin: str
    cls: str
    plant: tuple = ()               # (verb, *args): a method of the table's fixture, run before the call
    argv: tuple | None = None       # the driver's argv; None means run_ladder(*extra)
    extra: tuple = ()
    named: tuple = ()               # strings the line must carry
    lower_named: tuple = ()         # words the line must carry, case-insensitive
    absent: tuple = ()              # strings the line must not carry
    with_gh: bool = True
    dispatched: tuple = ()          # (stage, count) pairs the dispatch log must show; () means no dispatch at all
    note: str | None = None


LEVER = "ladder-lever.md §Preconditions and refusals"
INPUTS = "the 2026-09-10 --input brief"
SETS = "scope-stage-set-20260912.md"

REFUSAL_CASES = [
    RefusalRow("arguments-none", LEVER, "arguments", argv=(), named=(START_FORM,)),
    RefusalRow("arguments-input-flag-bare", INPUTS, "arguments", argv=("@scope", BRANCH, "@plan", "--input"),
               named=(START_FORM,), absent=(NEEDED_INPUT,)),
    RefusalRow("arguments-override-without-because", SETS + " item 5", "arguments", extra=("--stage-set", "contract")),
    RefusalRow("arguments-resume-without-id", LEVER, "arguments", argv=("resume",)),
    RefusalRow("scope-absent", LEVER, "scope", plant=("absent_scope",), named=("@missing",)),
    RefusalRow("input-missing", INPUTS, "input", plant=("missing_input",), named=("@missing", NEEDED_INPUT)),
    RefusalRow("input-unreadable", INPUTS, "input", plant=("unreadable_input",), named=("@input", NEEDED_INPUT)),
    RefusalRow("worktree-branch-without-one", LEVER, "worktree", plant=("branch_without_worktree",),
               named=("git worktree add", WORKTREE_ROOT, "ag-", "no-wt-aa32")),
    RefusalRow("dirty-worktree", LEVER, "dirty", plant=("dirty",)),
    RefusalRow("conf-key-missing", LEVER, "conf-key", plant=("conf_without", "ADDRESS_GROK"), named=("ADDRESS_GROK", "@conf")),
    RefusalRow("gh-missing", LEVER, "gh-missing", plant=("no_gh",), with_gh=False),
    RefusalRow("plan-unknown-class", SETS + " item 3", "plan", plant=("plan_class", "nope"), named=("nope",)),
    RefusalRow("stage-set-override-unknown", SETS + " item 5", "stage-set", extra=("--stage-set", "nope", "--because", "x"), named=("nope",)),
    RefusalRow("stage-set-floor-check-tests", SETS + " item 6", "stage-set",
               plant=("table_and_plan", {"bad": ["tests", "code", "adversarial"], "lane": NINE}), named=("check-tests",)),
    RefusalRow("stage-set-floor-review", SETS + " item 6", "stage-set",
               plant=("table_and_plan", {"bad": ["tests", "check-tests", "code"], "lane": NINE}), named=("security or adversarial",)),
    RefusalRow("stage-set-floor-fix-round", SETS + " item 6", "stage-set",
               plant=("table_and_plan", {"bad": ["tests", "check-tests", "code", "adversarial", "fix-tests"], "lane": NINE}),
               named=("fix-tests and fix-code",)),
    RefusalRow("stage-set-floor-security-re", SETS + " item 6", "stage-set",
               plant=("table_and_plan", {"bad": ["tests", "check-tests", "code", "adversarial", "security-re"], "lane": NINE}),
               named=("security-re",)),
    RefusalRow("stage-set-unknown-stage", SETS + " item 6", "stage-set",
               plant=("table_and_plan", {"bad": ["tests", "check-tests", "code", "adversarial", "deploy"], "lane": NINE}), named=("deploy",)),
    RefusalRow("stage-set-table-absent", SETS + " item 7", "stage-set", plant=("table_and_plan", None)),
    RefusalRow("stage-set-verb-table-absent", SETS + " item 9", "stage-set", argv=("stage-sets", "@nowhere")),
    RefusalRow("record-input-bytes-changed", INPUTS, "record", plant=("input_changed",), named=("@digest", NEEDED_RECORD),
               dispatched=(("tests", 1), ("check-tests", 0))),
    RefusalRow("custody-symlink-at-the-skeptic-name", "scope-item31-20260911.md item 4", "custody", plant=("custody_symlink",),
               lower_named=("symlink",), dispatched=(("tests", 1), ("tests-fix", 0))),
]


class LadderRefusals(_LadderFixture):
    """One fresh fixture per row; the row's plant verb runs, the driver is
    invoked, the wire is asserted through the kit."""

    # -- plant verbs: each returns the substitutions its row's argv and named strings use --

    def plant_absent_scope(self):
        missing = self.repo / "no-such-scope-aa32.md"
        self.assertFalse(missing.exists(), "plant: scope must be absent")
        return {"argv": (str(missing), BRANCH), "@missing": str(missing)}

    def plant_missing_input(self):
        missing = self.repo / "no-such-input-aa32.md"
        self.assertFalse(missing.exists(), "plant: input path must be absent")
        return {"argv": (str(self.scope_path), BRANCH, "--input", str(missing)), "@missing": str(missing)}

    def plant_unreadable_input(self):
        path = self.repo / "wo-alpha-aa32.md"
        self._plant_bytes(path, b"start-input-alpha-aa32\n")
        self.git("add", "--", path.name)
        self.git("commit", "-m", "plant start input\n")
        mode = path.stat().st_mode
        os.chmod(path, 0)
        # the world is gone by cleanup time; restore the mode only while the file is
        self.addCleanup(lambda: path.exists() and os.chmod(path, stat.S_IMODE(mode) or 0o644))
        self.assertFalse(os.access(path, os.R_OK), f"plant: input still readable: {path}")
        return {"argv": (str(self.scope_path), BRANCH, "--input", str(path)), "@input": str(path)}

    def plant_branch_without_worktree(self):
        other = "lane/no-wt-aa32"
        self.git("branch", other)
        listed = self.git("worktree", "list", "--porcelain").stdout
        self.assertNotIn("refs/heads/" + other, listed)
        return {"argv": (str(self.scope_path), other)}

    def plant_dirty(self):
        tracked = self.repo / "README"
        before = tracked.read_bytes()
        self._plant_bytes(tracked, b"dirty-uncommitted-aa32\n")
        self.assertNotEqual(tracked.read_bytes(), before)
        self.assertTrue(self.porcelain().strip(), "plant: tree still clean")
        return {}

    def plant_conf_without(self, key):
        self.plant_conf(omit=(key,))
        self.assertNotIn(f"{key}=", self.conf_path.read_text(encoding="utf-8"))
        self.git("add", str(self.conf_path.relative_to(self.repo)))
        self.git("commit", "-m", f"drop {key}\n")
        self.assertEqual(self.porcelain(), "")
        return {"@conf": str(self.conf_path)}

    def plant_no_gh(self):
        self.gh.unlink()
        self.assertFalse(self.gh.exists(), "plant: gh stub still present")
        return {}

    def plant_plan_class(self, cls):
        self.plant_plan(_plan(cls))
        return {}

    def plant_table_and_plan(self, table):
        rel = "ops/conductor/stage-sets.json"
        path = self.repo / rel
        if table is None:
            self.git("rm", "-q", rel)
            self.git("commit", "-m", "remove table\n")
            self.assertFalse(path.exists())
            return {}
        self._plant_bytes(path, (json.dumps(table, indent=2) + "\n").encode("utf-8"))
        self.git("add", rel)
        self.git("commit", "-m", "plant table\n")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), table)
        self.plant_plan(_plan("bad"))
        return {}

    def plant_input_changed(self):
        path = self.repo / "wo-alpha-aa32.md"
        self._plant_bytes(path, b"start-input-alpha-aa32\n")
        self.git("add", "--", path.name)
        self.git("commit", "-m", "plant start input\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.patch_plant(trip_stages=["tests"])
        first = self.invoke([str(self.scope_path), BRANCH, "--input", str(path)])
        self.assertEqual(first.returncode, 3, self.combined(first))
        self.assertTrue(self.close_tripped_record())
        ladder_id = self.ladder_record()["id"]
        self.patch_plant(trip_stages=[])
        self._plant_bytes(path, b"start-input-alpha-changed-aa32\n")
        self.assertNotEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.git("add", "--", path.name)
        self.git("commit", "-m", "change start input bytes\n")
        return {"argv": ("resume", str(ladder_id)), "@digest": digest}

    def plant_custody_symlink(self):
        envelope = {"verdict": "changes", "findings": [dict(WELL_FORMED_SKEPTIC_FINDING)],
                    "counts": dict(WELL_FORMED_SKEPTIC_COUNTS), "artifacts": {}}
        self.patch_plant(verdicts={"check-tests": "changes"}, skeptic_md=True, skeptic_text=SKEPTIC_BODY,
                         skeptic_envelope=envelope, trip_stages=["tests"])
        first = self.run_ladder()
        rec_id = self.record_id_for("tests")
        self.assert_stopped(first, stage="tests", record=rec_id, reason="tripped")
        self.close_tripped_record()
        ladder_id = self.ladder_id()
        inputs_dir = self.ladder_inputs_dir(ladder_id)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        target = self.root / "outside-skeptic-aa32.md"
        target.write_text("CUSTODY_TARGET_UNTOUCHED_aa32\n", encoding="utf-8")
        link = inputs_dir / SKEPTIC_FINDINGS
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        self.assertTrue(link.is_symlink(), link)
        self.git("add", "-A", "--", ".dev")
        self.git("commit", "-m", "plant custody symlink trap\n")
        self.assertEqual(self.porcelain(), "")
        self.patch_plant(trip_stages=[])
        self._custody_link, self._custody_target = link, target
        return {"argv": ("resume", ladder_id)}

    # -- the runner --

    def run_row(self, row: RefusalRow) -> None:
        subs = {"@scope": str(self.scope_path), "@plan": str(self.plan_path),
                "@nowhere": str(self.repo / "nowhere" / "stage-sets.json")}
        if row.plant:
            verb, *args = row.plant
            subs.update(getattr(self, f"plant_{verb}")(*args) or {})
        argv = row.argv if row.argv is not None else subs.get("argv")
        if argv is None:
            proc = self.run_ladder(*row.extra, with_gh=row.with_gh)
        else:
            proc = self.invoke([subs.get(a, a) for a in argv], with_gh=row.with_gh)
        named = tuple(subs.get(n, n) for n in row.named)
        case = RefusalCase(row.id, tuple(argv or row.extra), row.cls, named=named, origin=row.origin)
        refusal = refusal_wire(case, proc)
        for word in row.lower_named:
            self.assertIn(word, refusal.line.lower(), refusal.line)
        for text in row.absent:
            self.assertNotIn(text, refusal.line, refusal.line)
        names = self.dispatch_names()
        if not row.dispatched:
            self.assertEqual(self.dispatch_calls(), [], f"{row.id}: a refusal dispatches nothing")
        for stage, count in row.dispatched:
            self.assertEqual(names.count(stage), count, f"{row.id}: {stage} dispatched {names.count(stage)}x: {names}")
        if row.id.startswith("custody"):
            self.assertTrue(self._custody_link.is_symlink(), "item 4: the symlink stays in place")
            self.assertEqual(self._custody_target.read_text(encoding="utf-8"), "CUSTODY_TARGET_UNTOUCHED_aa32\n")


def _method(row: RefusalRow):
    def test(self):
        self.run_row(row)
    test.__name__ = "test_" + row.id.replace("-", "_")
    test.__doc__ = f"{row.origin}: refuses {row.cls}"
    return test


_ids = [row.id for row in REFUSAL_CASES]
assert len(_ids) == len(set(_ids)), "rows are addressed by id"
for _row in REFUSAL_CASES:
    setattr(LadderRefusals, "test_" + _row.id.replace("-", "_"), _method(_row))
