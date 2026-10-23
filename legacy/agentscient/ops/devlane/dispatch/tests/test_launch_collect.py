"""Collect: ancestry, changed_paths, residual_paths, refs/dispatch.

Written from CONTRACT.md §Dispatch Collect. Plan items (r)(s).

  C1  worker HEAD that does not descend from ref_sha → invalid, no fetch
  C2  committed-and-clean → changed_paths from ref_sha..head, residual empty
  C3  edited-only → changed_paths empty, residual non-empty
  C4  read role: head equals ref_sha; a changed snapshot is invalid
"""

from __future__ import annotations

import json
import os
import time

import launch_support as ls

from ops.testkit import run

COLLECT_SUBJECT = "lane: collect the residual tree"
COLLECT_MSG = (
    COLLECT_SUBJECT
    + "\n\nThe tripped run left this file uncommitted.\n"
)


class OffLineageHeadIsInvalidAndNotFetched(ls._TempLaunch):
    """C1 / plan (r) / contract off-lineage-head."""

    def test_an_orphan_head_is_invalid_and_does_not_create_refs_dispatch(self):
        os.environ["TASK_LAUNCH_ORPHAN"] = "1"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        before_refs = self.refs_map()
        _code, out, err = self.dispatch(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        rec = self.read_record()
        blob = (self.combined(out, err) + json.dumps(rec)).lower()
        envelope = (rec.get("result") or {}).get("envelope") or {}
        self.assertEqual(envelope.get("status"), "invalid")
        self.assertIn("off-lineage-head", blob)
        after_refs = self.refs_map()
        dispatch_refs = [
            n for n in after_refs if n.startswith("refs/dispatch/")
        ]
        self.assertEqual(
            dispatch_refs, [],
            "off-lineage-head must not fetch refs/dispatch",
        )
        extra = {
            n for n in (set(after_refs) - set(before_refs))
            if n.startswith("refs/dispatch/")
        }
        self.assertEqual(extra, set())
        self.assertNotIn(f"refs/dispatch/{rec['id']}", after_refs)


class CommittedCleanVersusEditedOnly(ls._TempLaunch):
    """C2 / C3 / plan (s)."""

    def test_a_worker_that_committed_and_left_a_clean_tree(self):
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec["result"]
        self.assertIsInstance(result, dict)
        changed = ls.changed_entries(result.get("changed_paths"))
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertGreater(
            len(changed), 0,
            "committed work produces a non-empty ref_sha..head name-only diff",
        )
        self.assertIn("worker.py", " ".join(changed))
        self.assertEqual(
            residual, [],
            f"a clean snapshot tree has empty residual_paths, got {residual!r}",
        )
        self.assertEqual(
            self.porcelain(repo=self.snapshot_of(rec)), "",
        )
        self.assertTrue(result.get("head"))
        self.assertNotEqual(result["head"], rec["snapshot"]["ref_sha"])
        # Ancestry: head descends from ref_sha. _git raises on non-zero,
        # so returning is the proof.
        self._git(
            "merge-base", "--is-ancestor",
            rec["snapshot"]["ref_sha"], result["head"],
            repo=self.snapshot_of(rec),
        )
        self.assertEqual(
            self.refs_map()[f"refs/dispatch/{rec['id']}"], result["head"],
        )

    def test_a_worker_that_only_edited_leaves_residuals_and_no_changed_paths(
            self):
        os.environ["TASK_LAUNCH_EDIT"] = "scratch.txt"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec["result"]
        changed = ls.changed_entries(result.get("changed_paths"))
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertEqual(
            changed, [],
            f"no commit means empty changed_paths, got {changed!r}",
        )
        self.assertGreater(
            len(residual), 0,
            "an uncommitted edit is residual_paths, not changed_paths",
        )
        self.assertTrue(
            any("scratch.txt" in str(item) for item in residual),
            residual,
        )
        self.assertEqual(result.get("head"), rec["snapshot"]["ref_sha"])
        self.assertNotIn(
            f"refs/dispatch/{rec['id']}",
            # a write role with no new commit may still fetch HEAD==ref;
            # the pin is changed_paths empty. Fetching the same sha is
            # allowed; fetching something else is not.
            [n for n, sha in self.refs_map().items()
             if sha != rec["snapshot"]["ref_sha"] and n.startswith("refs/dispatch/")],
        )


class ReadRoleHeadEqualsRef(ls._TempLaunch):
    """C4 — read roles: head must equal ref_sha. Finding D2: a snapshot
    changed by a read role makes the envelope invalid, not merely
    recorded."""

    def test_a_read_role_records_head_equal_to_ref_sha(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        result = rec["result"]
        self.assertEqual(result["head"], rec["snapshot"]["ref_sha"])
        self.assertEqual(result["head"], self.ref)
        self.assertNotIn(f"refs/dispatch/{rec['id']}", self.refs_map())

    def test_a_read_role_with_an_uncommitted_edit_makes_the_envelope_invalid(
            self):
        os.environ["TASK_LAUNCH_EDIT"] = "notes.txt"
        os.environ["TASK_LAUNCH_VERDICT"] = "approve"
        _code, _out, _err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
        ))
        rec = self.read_record()
        self.assertEqual(rec["role"], "read")
        result = rec["result"]
        self.assertEqual(result["head"], rec["snapshot"]["ref_sha"])
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertGreater(len(residual), 0)
        self.assertTrue(
            any("notes.txt" in str(item) for item in residual),
            f"plant: notes.txt is in residual_paths, got {residual!r}",
        )
        envelope = result.get("envelope") or {}
        self.assertEqual(
            envelope.get("status"), "invalid",
            "a snapshot changed by a read role makes the envelope invalid, "
            f"not merely recorded: envelope={envelope!r}",
        )


class CollectAfterATrip(ls._TempLaunch):
    """launch.py collect ID --verify CMD commits a tripped write job's
    residual tree with the harness's message and attribution, moves
    refs/dispatch/<id>, and marks result.collected."""

    def _collect(self, job_id, verify, extra=()):
        argv = ["collect", job_id, "--verify", verify, *extra]
        return self.run_main(argv)

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

    def test_collect_commits_the_residual_and_moves_the_dispatch_ref(self):
        rec = self._trip_codex_write()
        job_id = rec["id"]
        ref_sha = rec["snapshot"]["ref_sha"]
        reason = "tripped-by-wall"
        verify = "test -f scratch.txt"
        code, out, err = self._collect(
            job_id, verify, extra=["--reason", reason],
        )
        self.assertEqual(
            code, 0,
            f"collect must succeed: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        result = rec2.get("result") or {}
        collected = result.get("collected") or {}
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)
        self.assertEqual((collected.get("verify") or {}).get("command"), verify)
        self.assertEqual(collected.get("reason"), reason)
        self.assertEqual(collected.get("by"), ls.AGENT)
        self.assertRegex(
            str(collected.get("at") or ""),
            r"^\d{4}-\d{2}-\d{2}T",
            f"collected.at is UTC: {collected!r}",
        )
        self.assertEqual(
            ls.residual_entries(result.get("residual_paths")),
            [],
            result,
        )
        changed = ls.changed_entries(result.get("changed_paths"))
        self.assertTrue(
            any("scratch.txt" in str(item) for item in changed),
            f"changed_paths names the collected file: {changed!r}",
        )
        self.assertTrue(
            result.get("residual_patch"),
            f"residual_patch is kept as evidence: {result!r}",
        )
        self.assertEqual(rec2.get("status"), "closed")
        refs = self.refs_map()
        dispatch_ref = f"refs/dispatch/{job_id}"
        self.assertIn(dispatch_ref, refs)
        head = refs[dispatch_ref]
        self.assertEqual(head, collected.get("head"), collected)
        self.assertNotEqual(head, ref_sha)
        parent = self._git("rev-parse", f"{head}^").stdout.strip()
        self.assertEqual(parent, ref_sha)
        msg = self._git("log", "-1", "--format=%B", head).stdout
        self.assertTrue(
            msg.startswith(COLLECT_SUBJECT),
            f"collected commit starts with out/COMMIT_MSG subject: {msg!r}",
        )
        self.assertIn(
            "The tripped run left this file uncommitted.", msg,
            f"harness COMMIT_MSG body is kept: {msg!r}",
        )
        ran = (rec2.get("model") or {}).get("ran") or ls.RAN_MODEL
        trailer_lines = [
            ln for ln in msg.splitlines() if ln.startswith("Co-Authored-By:")
        ]
        self.assertTrue(trailer_lines, f"commit has Co-Authored-By trailers: {msg!r}")
        self.assertTrue(
            any(ran in ln for ln in trailer_lines),
            f"Co-Authored-By trailer names the running model {ran!r}: {msg!r}",
        )
        self.assertEqual(
            self.porcelain(), "",
            "collect commits the record (status closed, tree clean)",
        )
        subject = self._git("log", "-1", "--format=%s").stdout.strip()
        self.assertEqual(
            subject, f"dispatch: record {job_id} after collection",
        )

    def test_collect_refuses_a_failing_verify_and_commits_nothing(self):
        rec = self._trip_codex_write()
        job_id = rec["id"]
        snap = self.snapshot_of(rec)
        before_refs = self.refs_map()
        before_record = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        before_lineage = self._git("rev-parse", "HEAD").stdout.strip()
        before_snap = self._git("rev-parse", "HEAD", repo=snap).stdout.strip()
        before_porc = self.porcelain(repo=snap)
        code, out, err = self._collect(job_id, "false")
        text = self.assert_refusal(
            code, out, err, ident="verify",
            phrases=["verify"],
        )
        self.assertRegex(text, r"exit|false|1")
        after_refs = self.refs_map()
        self.assertEqual(after_refs, before_refs)
        after_record = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        self.assertEqual(after_record, before_record)
        self.assertEqual(
            self._git("rev-parse", "HEAD").stdout.strip(), before_lineage,
        )
        self.assertEqual(
            self._git("rev-parse", "HEAD", repo=snap).stdout.strip(),
            before_snap,
            "failing verify must not commit the snapshot",
        )
        self.assertEqual(self.porcelain(repo=snap), before_porc)

    def test_collect_verify_is_walled_by_the_job_cap_in_the_snapshot(self):
        rec = self._trip_codex_write()
        snap = self.snapshot_of(rec)
        before_snap = self._git("rev-parse", "HEAD", repo=snap).stdout.strip()
        started = time.monotonic()
        code, out, err = self._collect(rec["id"], "test -f scratch.txt && sleep 8")
        elapsed = time.monotonic() - started
        self.assertLess(
            elapsed, 5,
            f"verify runs under the job cap, not the 8s sleep: "
            f"elapsed={elapsed:.3f}s out={out!r} err={err!r}",
        )
        self.assert_refusal(
            code, out, err, ident="verify",
            phrases=["verify"],
        )
        self.assertEqual(
            self._git("rev-parse", "HEAD", repo=snap).stdout.strip(),
            before_snap,
        )

    def test_collect_refuses_a_read_role(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["role"], "read")
        code, out, err = self._collect(rec["id"], "true")
        self.assert_refusal(
            code, out, err, ident="collect-role",
            phrases=["collect-role"],
        )

    def test_collect_refuses_a_job_that_finished_normally(self):
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        residual = ls.residual_entries(
            (rec.get("result") or {}).get("residual_paths"),
        )
        self.assertEqual(residual, [], "plant: a clean finish has no residual")
        code, out, err = self._collect(rec["id"], "true")
        self.assert_refusal(
            code, out, err, ident="collect-state",
            phrases=["collect-state"],
        )

    def test_collect_refuses_twice(self):
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
        code2, out2, err2 = self._collect(job_id, "test -f scratch.txt")
        self.assert_refusal(
            code2, out2, err2, ident="collect-state",
            phrases=["collect-state"],
        )
        after_head = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(after_head, before_head)

    def test_collect_verify_does_not_see_operator_environment(self):
        """Security finding 3: verify runs in the snapshot, not with the
        operator's ambient environment."""
        rec = self._trip_codex_write()
        sentinel = "visible-to-verify"
        os.environ["AUDIT_SENTINEL"] = sentinel
        self.assertEqual(os.environ.get("AUDIT_SENTINEL"), sentinel)
        verify = (
            'if [ -n "$AUDIT_SENTINEL" ]; then '
            'echo leaked >&2; exit 1; '
            'fi; test -f scratch.txt'
        )
        try:
            code, out, err = self._collect(rec["id"], verify)
        finally:
            os.environ.pop("AUDIT_SENTINEL", None)
        self.assertEqual(
            code, 0,
            "collect verify must not see AUDIT_SENTINEL from the operator "
            f"environment: out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        collected = (rec2.get("result") or {}).get("collected") or {}
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)

    def test_collect_refuses_an_off_lineage_snapshot_head(self):
        """Security finding 4: collect must not advance refs/dispatch
        when snapshot HEAD does not descend from ref_sha."""
        rec = self._trip_codex_write()
        job_id = rec["id"]
        snap = self.snapshot_of(rec)
        ref_sha = rec["snapshot"]["ref_sha"]
        scratch = snap / "scratch.txt"
        self.assertTrue(scratch.is_file(), "plant: residual scratch.txt")
        scratch_body = scratch.read_text(encoding="utf-8")
        tree = self._git("write-tree", repo=snap).stdout.strip()
        self.assertTrue(tree, "plant: write-tree")
        made = self._git(
            "commit-tree", tree, "-m", "orphan head", repo=snap,
        ).stdout.strip()
        self.assertRegex(made, r"^[0-9a-f]{40}$", made)
        self._git("reset", "--soft", made, repo=snap)
        head = self._git("rev-parse", "HEAD", repo=snap).stdout.strip()
        self.assertEqual(head, made)
        ancestor = run(
            ["git", "merge-base", "--is-ancestor", ref_sha, head],
            cwd=snap,
            env=self.env,
            timeout=60,
        )
        self.assertNotEqual(
            ancestor.exit, 0,
            "plant: snapshot HEAD must not descend from ref_sha",
        )
        self.assertEqual(scratch.read_text(encoding="utf-8"), scratch_body)
        before_refs = self.refs_map()
        before_record = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        code, out, err = self._collect(job_id, "test -f scratch.txt")
        self.assert_refusal(
            code, out, err, ident="off-lineage-head",
            phrases=["off-lineage-head"],
        )
        self.assertEqual(self.refs_map(), before_refs)
        after_record = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        self.assertEqual(after_record, before_record)
        self.assertEqual(
            self._git("rev-parse", "HEAD", repo=snap).stdout.strip(),
            made,
            "refused collect must not rewrite the off-lineage snapshot HEAD",
        )

    def test_collect_after_a_successful_resume_of_a_tripped_write_still_collects(
            self):
        """Adversarial finding 1: a resume that exits 0 must not make
        collect treat the tripped write as a normal finish."""
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
            f"resume of a tripped write with a reason: out={out!r} err={err!r}",
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
        snap = self.snapshot_of(rec2)
        self.assertTrue(
            (snap / "scratch.txt").is_file(),
            "plant: scratch.txt still in the snapshot",
        )
        dispatch_ref = f"refs/dispatch/{job_id}"
        refs = self.refs_map()
        if dispatch_ref in refs:
            self.assertEqual(
                refs[dispatch_ref], rec2["snapshot"]["ref_sha"],
                "plant: resume did not collect; ref still at the base",
            )
        code, out, err = self._collect(job_id, "test -f scratch.txt")
        self.assertEqual(
            code, 0,
            "collect must still recover a tripped write after a successful "
            f"resume: out={out!r} err={err!r}",
        )
        rec3 = self.read_record()
        collected = (rec3.get("result") or {}).get("collected") or {}
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)
        self.assertEqual(
            ls.residual_entries((rec3.get("result") or {}).get("residual_paths")),
            [],
            rec3,
        )

    def test_collect_verify_timeout_flag_raises_the_recorded_wall(self):
        """Adversarial opinion 6, pinned: a timeout-tripped implement
        cannot re-run suites.sh under the wall that already killed it.
        collect --timeout raises the verify wall."""
        rec = self._trip_codex_write()
        snap = self.snapshot_of(rec)
        before_snap = self._git("rev-parse", "HEAD", repo=snap).stdout.strip()
        started = time.monotonic()
        code, out, err = self._collect(
            rec["id"],
            "test -f scratch.txt && sleep 2",
            extra=["--timeout", "8"],
        )
        elapsed = time.monotonic() - started
        self.assertEqual(
            code, 0,
            "collect --timeout must raise the verify wall above the recorded "
            f"job cap: elapsed={elapsed:.3f}s out={out!r} err={err!r}",
        )
        self.assertGreaterEqual(
            elapsed, 1.5,
            f"verify was allowed to sleep 2s: elapsed={elapsed:.3f}s "
            f"out={out!r} err={err!r}",
        )
        rec2 = self.read_record()
        collected = (rec2.get("result") or {}).get("collected") or {}
        self.assertEqual((collected.get("verify") or {}).get("exit"), 0, collected)
        self.assertNotEqual(
            self._git("rev-parse", "HEAD", repo=snap).stdout.strip(),
            before_snap,
        )
