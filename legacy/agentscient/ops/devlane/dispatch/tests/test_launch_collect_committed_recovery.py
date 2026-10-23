"""Preserved committed recovery: CONTRACT.md paired acceptance matrix.

Written from ops/devlane/dispatch/CONTRACT.md §Preserved committed recovery
and in/collect-tests-brief.md. launch.py was not read.

The additional collect arm admits a closed write whose attempt exited 0
with an invalid or tripped envelope, a recorded descendant equal to
snapshot HEAD, a nonempty committed delta, and empty residuals. Explicit
verification that leaves that candidate exact reuses the original source
commit. Lane-policy / review / land gates are a separate pending runtime
and are not duplicated here.
"""

from __future__ import annotations

import json
import os
import time

import launch_support as ls

from ops.testkit import World, run

WORKER_REL = "worker.py"
VERIFY_OK = "test -f worker.py"
COLLECT_SUBJECT = "lane: collect the residual tree"
COLLECT_MSG = (
    COLLECT_SUBJECT
    + "\n\nThe tripped run left this file uncommitted.\n"
)
PRESERVED_REASON = "preserved-committed-delta"


class _KitLaunch(ls._TempLaunch):
    """The shared fixture's hermetic world, made explicit in this family."""

    world: World

    def setUp(self):
        super().setUp()
        self.assertIsInstance(self.world, World)
        self.world.require("git")

    def _collect(self, job_id, verify, extra=()):
        argv = ["collect", job_id, "--verify", verify, *extra]
        return self.run_main(argv)

    def _record_path(self, job_id=None):
        if job_id is None:
            return self.the_record_path()
        path = self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        self.assertTrue(path.is_file(), f"record missing: {path}")
        return path

    def _record_bytes(self, job_id=None):
        return self._record_path(job_id).read_bytes()

    def _snap_head(self, rec):
        return self._git(
            "rev-parse", "HEAD", repo=self.snapshot_of(rec),
        ).stdout.strip()

    def _measured_delta(self, rec, head=None):
        snap = self.snapshot_of(rec)
        head = head or self._snap_head(rec)
        raw = self._git(
            "diff", "--name-only", rec["snapshot"]["ref_sha"], head, "--",
            repo=snap,
        ).stdout
        return [ln for ln in raw.splitlines() if ln.strip()]

    def _is_ancestor(self, rec, ancestor, head=None):
        snap = self.snapshot_of(rec)
        head = head or self._snap_head(rec)
        result = run(
            ["git", "merge-base", "--is-ancestor", ancestor, head],
            cwd=snap,
            env=self.env,
            timeout=60,
        )
        return result.exit == 0

    def _rewrite_record(self, rec, mutate):
        path = self._record_path(rec["id"])
        before = path.read_bytes()
        self.assertTrue(before, "plant: record was empty before rewrite")
        data = json.loads(before)
        mutate(data)
        content = json.dumps(data) + "\n"
        self.world.plant_text(path, content)
        after = path.read_bytes()
        self.assertNotEqual(after, before, "plant: record rewrite changed nothing")
        landed = json.loads(after)
        self.assertEqual(landed.get("id"), rec["id"])
        return landed


class PreservedCommittedRecovery(_KitLaunch):
    """Closed write + exit 0 + invalid/tripped envelope + exact delta."""

    def _assert_committed_candidate_fixture(self, rec, *, envelope_status):
        """Prove the candidate before collect. Failure here is a bad plant."""
        self.assertEqual(rec.get("status"), "closed", rec)
        self.assertEqual(rec.get("role"), "write", rec)
        result = rec.get("result") or {}
        envelope = result.get("envelope") or {}
        attempts = rec.get("attempts") or []
        self.assertTrue(attempts, "plant: closed write has an attempt")
        last = attempts[-1]
        self.assertEqual(last.get("exit"), 0, last)
        self.assertFalse(bool(last.get("tripped")), last)
        self.assertEqual(envelope.get("status"), envelope_status, envelope)
        self.assertNotEqual(
            envelope_status, "ok",
            "plant: committed recovery requires invalid or tripped, not ok",
        )
        self.assertIsNone(result.get("collected"), rec)
        snap = self.snapshot_of(rec)
        snap_head = self._snap_head(rec)
        recorded_head = result.get("head")
        ref_sha = rec["snapshot"]["ref_sha"]
        self.assertEqual(snap_head, recorded_head, rec)
        self.assertNotEqual(recorded_head, ref_sha, rec)
        self.assertTrue(
            self._is_ancestor(rec, ref_sha, snap_head),
            f"plant: HEAD {snap_head} must descend from ref_sha {ref_sha}",
        )
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertEqual(
            residual, [],
            f"plant: committed arm requires empty residuals, got {residual!r}",
        )
        self.assertEqual(
            self.porcelain(repo=snap), "",
            "plant: snapshot worktree is clean",
        )
        measured = self._measured_delta(rec, snap_head)
        self.assertGreater(
            len(measured), 0,
            f"plant: nonempty committed delta, got {measured!r}",
        )
        self.assertIn(WORKER_REL, measured, measured)
        recorded = ls.changed_entries(result.get("changed_paths"))
        self.assertEqual(
            recorded, measured,
            f"plant: recorded changed_paths must equal measured delta: "
            f"recorded={recorded!r} measured={measured!r}",
        )
        self.assertTrue(
            (snap / WORKER_REL).is_file(),
            f"plant: {WORKER_REL} exists in the snapshot",
        )
        self.assertEqual(
            self.refs_map().get(f"refs/dispatch/{rec['id']}"),
            recorded_head,
        )
        return {
            "head": recorded_head,
            "ref_sha": ref_sha,
            "attempts": attempts,
            "envelope": envelope,
            "cause": result.get("cause"),
            "message": self._git(
                "log", "-1", "--format=%B", recorded_head, repo=snap,
            ).stdout,
        }

    def _plant_committed_failed_write(self, *, envelope_status="invalid"):
        os.environ["TASK_LAUNCH_COMMIT"] = WORKER_REL
        os.environ["TASK_LAUNCH_STATUS"] = envelope_status
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        os.environ["TASK_LAUNCH_EXIT"] = "0"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        os.environ["TASK_LAUNCH_JOB"] = "implement"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        plant = self._assert_committed_candidate_fixture(
            rec, envelope_status=envelope_status,
        )
        return rec, plant

    def _assert_unpublished(
        self, rec, *, before_record, before_refs, plant,
        snap_head=None,
    ):
        rec2 = json.loads(self._record_bytes(rec["id"]))
        result = rec2.get("result") or {}
        self.assertFalse(
            result.get("collected"),
            f"refused collect must not write result.collected: {result!r}",
        )
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)
        if result:
            self.assertEqual(result.get("head"), plant["head"], rec2)
            self.assertEqual(result.get("envelope"), plant["envelope"], rec2)
            self.assertEqual(result.get("cause"), plant["cause"], rec2)
        self.assertEqual(rec2.get("attempts"), plant["attempts"], rec2)
        if snap_head is not None:
            self.assertEqual(self._snap_head(rec2), snap_head)

    def test_collect_reuses_the_preserved_source_commit_on_invalid_envelope(
            self):
        rec, plant = self._plant_committed_failed_write(envelope_status="invalid")
        source = plant["head"]
        code, out, err = self._collect(
            rec["id"], VERIFY_OK, extra=["--reason", PRESERVED_REASON],
        )
        self.assertEqual(
            code, 0,
            "collect must recover the preserved commit without a source "
            f"replay: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        result = rec2.get("result") or {}
        collected = result.get("collected") or {}
        self.assertEqual(collected.get("head"), source, collected)
        self.assertEqual(result.get("head"), source, result)
        self.assertEqual(
            self._snap_head(rec2), source,
            "snapshot HEAD stays the original source commit",
        )
        self.assertEqual(
            self.refs_map().get(f"refs/dispatch/{rec['id']}"), source,
        )
        self.assertEqual(
            self._git(
                "log", "-1", "--format=%B", source,
                repo=self.snapshot_of(rec2),
            ).stdout,
            plant["message"],
            "original attribution is kept; collect must not rewrite source",
        )
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)
        self.assertEqual(
            (collected.get("verify") or {}).get("command"), VERIFY_OK, collected,
        )
        self.assertEqual(collected.get("reason"), PRESERVED_REASON, collected)
        self.assertEqual(collected.get("by"), ls.AGENT, collected)
        self.assertEqual(result.get("envelope"), plant["envelope"], rec2)
        self.assertEqual(result.get("cause"), plant["cause"], rec2)
        self.assertEqual(rec2.get("attempts"), plant["attempts"], rec2)
        self.assertEqual(rec2.get("status"), "closed")

    def test_collect_reuses_the_preserved_source_commit_on_tripped_envelope(
            self):
        rec, plant = self._plant_committed_failed_write(envelope_status="tripped")
        source = plant["head"]
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assertEqual(
            code, 0,
            "a tripped envelope with an exact committed candidate recovers "
            f"that commit: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        collected = (rec2.get("result") or {}).get("collected") or {}
        self.assertEqual(collected.get("head"), source, collected)
        self.assertEqual((rec2.get("result") or {}).get("head"), source)
        self.assertEqual(
            (rec2.get("result") or {}).get("envelope"), plant["envelope"], rec2,
        )

    def test_collect_refuses_the_same_candidate_with_a_successful_envelope(
            self):
        os.environ["TASK_LAUNCH_COMMIT"] = WORKER_REL
        os.environ["TASK_LAUNCH_STATUS"] = "ok"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        os.environ["TASK_LAUNCH_EXIT"] = "0"
        os.environ["TASK_LAUNCH_JOB"] = "implement"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec.get("result") or {}
        self.assertEqual((result.get("envelope") or {}).get("status"), "ok")
        self.assertEqual(ls.residual_entries(result.get("residual_paths")), [])
        self.assertGreater(len(ls.changed_entries(result.get("changed_paths"))), 0)
        self.assertIsNone(result.get("collected"))
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)
        rec2 = self.read_record()
        self.assertFalse((rec2.get("result") or {}).get("collected"))

    def test_collect_refuses_a_running_unclosed_job(self):
        rec, _plant = self._plant_committed_failed_write()

        def mark_launched(data):
            data["status"] = "launched"
            data["at"]["closed"] = None

        planted = self._rewrite_record(rec, mark_launched)
        self.assertEqual(planted.get("status"), "launched", planted)
        self.assertIsNone((planted.get("at") or {}).get("closed"), planted)
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)

    def test_collect_refuses_missing_failure_evidence(self):
        rec, _plant = self._plant_committed_failed_write()
        planted = self._rewrite_record(rec, lambda data: data["result"].pop(
            "envelope", None,
        ))
        self.assertNotIn("envelope", (planted.get("result") or {}), planted)
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)

    def test_collect_refuses_an_already_collected_result(self):
        rec, plant = self._plant_committed_failed_write()

        def add_collected(data):
            data["result"]["collected"] = {
                "at": "2026-09-01T00:00:00Z",
                "by": ls.AGENT,
                "head": plant["head"],
                "verify": {"command": VERIFY_OK, "exit": 0},
                "reason": "already",
            }

        planted = self._rewrite_record(rec, add_collected)
        self.assertIsInstance(
            (planted.get("result") or {}).get("collected"), dict, planted,
        )
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)

    def test_collect_refuses_a_different_descendant(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        extra = self.world.plant_text(snap / "extra.py", "different descendant\n")
        self.assertTrue(extra.is_file())
        self._git("add", "--", "extra.py", repo=snap)
        self._git("commit", "-m", "different descendant", repo=snap)
        new_head = self._snap_head(rec)
        self.assertNotEqual(new_head, plant["head"])
        self.assertTrue(self._is_ancestor(rec, plant["ref_sha"], new_head))
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant,
        )
        self.assertEqual(self._snap_head(rec), new_head)

    def test_collect_refuses_an_off_lineage_head(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        tree = self._git("write-tree", repo=snap).stdout.strip()
        made = self._git(
            "commit-tree", tree, "-m", "orphan head", repo=snap,
        ).stdout.strip()
        self.assertRegex(made, r"^[0-9a-f]{40}$", made)
        self._git("reset", "--soft", made, repo=snap)
        head = self._snap_head(rec)
        self.assertEqual(head, made)
        self.assertFalse(
            self._is_ancestor(rec, plant["ref_sha"], head),
            "plant: snapshot HEAD must not descend from ref_sha",
        )
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="off-lineage-head",
            phrases=["off-lineage-head"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant, snap_head=made,
        )

    def test_collect_refuses_an_unavailable_object(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        head = plant["head"]
        obj = snap / ".git" / "objects" / head[:2] / head[2:]
        self.assertTrue(obj.is_file(), f"plant: loose object at {obj}")
        obj.unlink()
        self.assertFalse(obj.is_file())
        missing = run(
            ["git", "cat-file", "-t", head],
            cwd=snap, env=self.env, timeout=60,
        )
        self.assertNotEqual(
            missing.exit, 0,
            "plant: recorded head is unavailable in the snapshot",
        )
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        rec2 = json.loads(self._record_bytes(rec["id"]))
        result = rec2.get("result") or {}
        self.assertFalse(result.get("collected"))
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)
        self.assertEqual(ls.changed_entries(result.get("changed_paths")),
                         ls.changed_entries((rec.get("result") or {}).get(
                             "changed_paths")))

    def test_collect_refuses_a_stale_changed_paths_list(self):
        rec, plant = self._plant_committed_failed_write()
        measured = self._measured_delta(rec)
        planted = self._rewrite_record(rec, lambda data: data["result"].__setitem__(
            "changed_paths", ["not-the-recorded-delta.py"],
        ))
        stale = ls.changed_entries(
            (planted.get("result") or {}).get("changed_paths"),
        )
        self.assertEqual(stale, ["not-the-recorded-delta.py"], stale)
        self.assertNotEqual(stale, measured)
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant,
        )

    def test_collect_refuses_a_base_head(self):
        os.environ["TASK_LAUNCH_STATUS"] = "invalid"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        os.environ["TASK_LAUNCH_EXIT"] = "0"
        os.environ["TASK_LAUNCH_JOB"] = "implement"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec.get("result") or {}
        self.assertEqual((result.get("envelope") or {}).get("status"), "invalid")
        self.assertEqual(result.get("head"), rec["snapshot"]["ref_sha"], rec)
        self.assertEqual(ls.residual_entries(result.get("residual_paths")), [])
        self.assertEqual(ls.changed_entries(result.get("changed_paths")), [])
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], "true")
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(self.refs_map(), before_refs)

    def test_collect_refuses_a_netzero_commit_revert(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        self._git("revert", "--no-edit", "HEAD", repo=snap)
        revert_head = self._snap_head(rec)
        self.assertNotEqual(revert_head, plant["head"])
        self.assertTrue(self._is_ancestor(rec, plant["ref_sha"], revert_head))
        measured = self._measured_delta(rec, revert_head)
        self.assertEqual(
            measured, [],
            f"plant: revert returns the base tree, got {measured!r}",
        )
        def mark_netzero(data):
            data["result"]["head"] = revert_head
            data["result"]["changed_paths"] = []

        planted = self._rewrite_record(rec, mark_netzero)
        self.assertEqual((planted.get("result") or {}).get("head"), revert_head)
        self.assertEqual(
            ls.changed_entries((planted.get("result") or {}).get("changed_paths")),
            [],
        )
        plant = dict(plant)
        plant["head"] = revert_head
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], "true")
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant, snap_head=revert_head,
        )

    def test_collect_refuses_an_empty_commit(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        self._git("reset", "--hard", plant["ref_sha"], repo=snap)
        self._git("commit", "--allow-empty", "-m", "empty commit", repo=snap)
        empty_head = self._snap_head(rec)
        self.assertNotEqual(empty_head, plant["ref_sha"])
        self.assertTrue(self._is_ancestor(rec, plant["ref_sha"], empty_head))
        self.assertEqual(self._measured_delta(rec, empty_head), [])
        def mark_empty(data):
            data["result"]["head"] = empty_head
            data["result"]["changed_paths"] = []

        planted = self._rewrite_record(rec, mark_empty)
        self.assertEqual((planted.get("result") or {}).get("head"), empty_head)
        plant = dict(plant)
        plant["head"] = empty_head
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        code, out, err = self._collect(rec["id"], "true")
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant, snap_head=empty_head,
        )

    def test_collect_refuses_a_failing_verify_without_publication(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        before_snap = plant["head"]
        code, out, err = self._collect(rec["id"], "false")
        self.assert_refusal(
            code, out, err, ident="verify",
            phrases=["verify"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant, snap_head=before_snap,
        )
        self.assertEqual(self.porcelain(repo=snap), "")

    def test_collect_refuses_a_verify_timeout_without_publication(self):
        os.environ["DISPATCH_TIMEOUT"] = "0.5"
        rec, plant = self._plant_committed_failed_write()
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        started = time.monotonic()
        code, out, err = self._collect(rec["id"], "test -f worker.py && sleep 8")
        elapsed = time.monotonic() - started
        self.assertLess(
            elapsed, 5,
            f"verify is walled by the recorded job cap: elapsed={elapsed:.3f}s "
            f"out={out!r} err={err!r}",
        )
        self.assert_refusal(
            code, out, err, ident="verify",
            phrases=["verify"],
        )
        self._assert_unpublished(
            rec, before_record=before_record, before_refs=before_refs,
            plant=plant, snap_head=plant["head"],
        )

    def test_collect_refuses_when_verify_mutates_head(self):
        rec, plant = self._plant_committed_failed_write()
        _snap = self.snapshot_of(rec)
        before_record = self._record_bytes(rec["id"])
        before_refs = self.refs_map()
        verify = (
            "git -c user.name=verify -c user.email=verify@example.test "
            "-c commit.gpgsign=false commit --allow-empty -m 'verify mutated head'"
        )
        code, out, err = self._collect(rec["id"], verify)
        after_head = self._snap_head(rec)
        self.assertNotEqual(
            after_head, plant["head"],
            "plant: verification mutated snapshot HEAD: "
            f"before={plant['head']} after={after_head} out={out!r} err={err!r}",
        )
        self.assertNotEqual(code, 0, f"mutated head must not collect: {err!r}")
        rec2 = json.loads(self._record_bytes(rec["id"]))
        result = rec2.get("result") or {}
        self.assertFalse(result.get("collected"), rec2)
        self.assertEqual(result.get("head"), plant["head"], rec2)
        self.assertEqual(result.get("envelope"), plant["envelope"], rec2)
        self.assertEqual(result.get("cause"), plant["cause"], rec2)
        self.assertEqual(rec2.get("attempts"), plant["attempts"], rec2)
        self.assertEqual(
            self.refs_map().get(f"refs/dispatch/{rec['id']}"),
            plant["head"],
        )
        self.assertEqual(self.refs_map(), before_refs)
        self.assertNotIn(b'"collected"', before_record)

    def test_collect_refuses_when_verify_mutates_source(self):
        rec, plant = self._plant_committed_failed_write()
        snap = self.snapshot_of(rec)
        before_blob = self._git(
            "show", f"{plant['head']}:{WORKER_REL}", repo=snap,
        ).stdout
        before_refs = self.refs_map()
        code, out, err = self._collect(
            rec["id"], "printf '\\nmutated\\n' >> worker.py",
        )
        after = (snap / WORKER_REL).read_text(encoding="utf-8")
        self.assertNotEqual(
            after, before_blob,
            "plant: verification mutated source "
            f"out={out!r} err={err!r}",
        )
        self.assertNotEqual(code, 0, f"mutated source must not collect: {err!r}")
        rec2 = json.loads(self._record_bytes(rec["id"]))
        result = rec2.get("result") or {}
        self.assertFalse(result.get("collected"), rec2)
        self.assertEqual(result.get("head"), plant["head"], rec2)
        self.assertEqual(result.get("envelope"), plant["envelope"], rec2)
        self.assertEqual(result.get("cause"), plant["cause"], rec2)
        self.assertEqual(
            self.refs_map().get(f"refs/dispatch/{rec['id']}"),
            plant["head"],
        )
        self.assertEqual(self.refs_map(), before_refs)

    def test_collect_preserves_attempt_envelope_and_cause_on_success(self):
        rec, plant = self._plant_committed_failed_write()
        code, out, err = self._collect(rec["id"], VERIFY_OK)
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        rec2 = self.read_record()
        result = rec2.get("result") or {}
        self.assertEqual(rec2.get("attempts"), plant["attempts"], rec2)
        self.assertEqual(result.get("envelope"), plant["envelope"], rec2)
        self.assertEqual(result.get("cause"), plant["cause"], rec2)
        self.assertEqual((result.get("collected") or {}).get("head"), plant["head"])

    def test_collect_preserves_attempt_envelope_and_cause_on_refusal(self):
        rec, plant = self._plant_committed_failed_write()
        self._rewrite_record(rec, lambda data: data["result"].__setitem__(
            "changed_paths", ["stale.py"],
        ))
        before_record = self._record_bytes(rec["id"])
        code, _out, err = self._collect(rec["id"], VERIFY_OK)
        self.assertNotEqual(code, 0, err)
        rec2 = json.loads(self._record_bytes(rec["id"]))
        self.assertEqual(self._record_bytes(rec["id"]), before_record)
        self.assertEqual(rec2.get("attempts"), plant["attempts"], rec2)
        self.assertEqual(
            (rec2.get("result") or {}).get("envelope"), plant["envelope"], rec2,
        )
        self.assertEqual(
            (rec2.get("result") or {}).get("cause"), plant["cause"], rec2,
        )
        self.assertFalse((rec2.get("result") or {}).get("collected"))


class LegacyResidueRecoveryStillHolds(_KitLaunch):
    """Existing failed/tripped residue arm, including successful resume."""

    def _trip_codex_write(self):
        os.environ["DISPATCH_TIMEOUT"] = "0.5"
        os.environ["TASK_LAUNCH_SLEEP"] = "8"
        os.environ["TASK_LAUNCH_EDIT"] = "scratch.txt"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        os.environ["TASK_LAUNCH_COMMIT_MSG"] = COLLECT_MSG
        _code, out, err = self.dispatch(self.argv_for(
            job="implement", harness="codex", stage="code",
            scope="python3 -m unittest",
        ))
        rec = self.read_record()
        snap = self.snapshot_of(rec)
        scratch = snap / "scratch.txt"
        self.assertTrue(
            scratch.is_file(),
            "plant: the tripped write left scratch.txt in the snapshot",
        )
        residual = ls.residual_entries(
            (rec.get("result") or {}).get("residual_paths"),
        )
        self.assertGreater(
            len(residual), 0,
            f"plant: residual_paths is non-empty after a trip, got "
            f"{residual!r} out={out!r} err={err!r}",
        )
        self.assertEqual(
            (rec.get("result") or {}).get("head"),
            rec["snapshot"]["ref_sha"],
            "plant: a trip does not manufacture a snapshot commit",
        )
        return rec

    def test_residue_arm_still_collects_a_tripped_write(self):
        rec = self._trip_codex_write()
        attempts = rec.get("attempts") or []
        envelope = (rec.get("result") or {}).get("envelope")
        cause = (rec.get("result") or {}).get("cause")
        code, out, err = self._collect(rec["id"], "test -f scratch.txt")
        self.assertEqual(
            code, 0,
            f"legacy residue arm must still collect: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        result = rec2.get("result") or {}
        collected = result.get("collected") or {}
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)
        self.assertEqual(rec2.get("attempts"), attempts, rec2)
        self.assertEqual(result.get("envelope"), envelope, rec2)
        self.assertEqual(result.get("cause"), cause, rec2)
        self.assertTrue(result.get("residual_patch"), result)

    def test_residue_arm_still_collects_after_a_successful_resume(self):
        rec = self._trip_codex_write()
        job_id = rec["id"]
        os.environ.pop("TASK_LAUNCH_SLEEP", None)
        os.environ.pop("DISPATCH_TIMEOUT", None)
        os.environ["TASK_LAUNCH_EXIT"] = "0"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        self.start_witness.unlink(missing_ok=True)
        self.witness.unlink(missing_ok=True)
        code, out, err = self.run_main([
            "resume", job_id, "--reason", "continue after wall",
        ])
        self.assertEqual(
            code, 0,
            f"resume of a tripped write: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        attempts = rec2.get("attempts") or []
        self.assertGreaterEqual(len(attempts), 2, rec2)
        last = attempts[-1]
        self.assertFalse(bool(last.get("tripped")), last)
        self.assertEqual(last.get("exit"), 0, last)
        residual = ls.residual_entries(
            (rec2.get("result") or {}).get("residual_paths"),
        )
        self.assertGreater(
            len(residual), 0,
            f"plant: residual still present after resume: {rec2!r}",
        )
        code, out, err = self._collect(job_id, "test -f scratch.txt")
        self.assertEqual(
            code, 0,
            "collect must still recover residue after a successful resume: "
            f"out={out!r} err={err!r}",
        )
        rec3 = self.read_record()
        collected = (rec3.get("result") or {}).get("collected") or {}
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)
        self.assertEqual(rec3.get("attempts"), attempts, rec3)

    def test_collect_refuses_when_there_is_neither_residue_nor_committed_delta(
            self):
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec.get("result") or {}
        self.assertEqual(ls.residual_entries(result.get("residual_paths")), [])
        self.assertEqual(ls.changed_entries(result.get("changed_paths")), [])
        code, out, err = self._collect(rec["id"], "true")
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )

    def test_collect_refuses_a_second_collection(self):
        rec = self._trip_codex_write()
        job_id = rec["id"]
        code, out, err = self._collect(job_id, "test -f scratch.txt")
        self.assertEqual(
            code, 0,
            f"first collect must succeed: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        self.assertIsInstance(
            (rec2.get("result") or {}).get("collected"), dict,
        )
        before_head = self._git("rev-parse", "HEAD").stdout.strip()
        before_record = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        code2, out2, err2 = self._collect(job_id, "test -f scratch.txt")
        self.assert_refusal(
            code2, out2, err2, ident="collect-state",
            phrases=["collect-state"],
        )
        self.assertEqual(
            self._git("rev-parse", "HEAD").stdout.strip(), before_head,
        )
        self.assertEqual(
            (
                self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
            ).read_bytes(),
            before_record,
        )
