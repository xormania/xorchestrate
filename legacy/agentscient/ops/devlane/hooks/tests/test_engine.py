"""The boundary engine: one hook, every lifecycle event, rows as data.

Owner, 2026-09-12: "build the engine - just make it a specific registry
of intentional conditions that can be added to any hook in the session
lifecycle"; "its about a registry that dictates the matrix of conditions
used in pre-tool - post-tool - whatever tool state". Law: docs/workflow.md.

engine.py reads the registry at every event it is bound to and evaluates
the rows bound to that event, in order; the first refusing row wins. A
row is data: event, tools, a `when` tree over a fixed predicate
vocabulary (all / any / not / pred), the refusal's expected and needed,
an escape. A predicate the engine does not know is could-not-look and
refuses hook-error; an event with no rows passes. Adding a row with no
code is the acceptance test, and it is the last test in this file.

One producer (Claude Fable 5.1) under hand grant 163; the second
producer is absent by the owner's word.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import support

from ops.testkit import World, run

HOOKS = Path(__file__).resolve().parents[1]
ENGINE = HOOKS / "claude" / "engine.py"
LIVE_REGISTRY = HOOKS / "boundaries.json"


def row(klass, event="PreToolUse", when=None, tools=("Bash",), escape="none", **extra):
    base = {"class": klass, "boundary": "write", "actor": "session", "trigger": klass,
            "evidence": "the row", "gate": "engine", "tests": ["ops/devlane/hooks/tests/test_engine.py::test_a_row_added_as_data_alone_refuses"],
            "escape": escape, "engine": True, "event": event, "tools": list(tools),
            "when": when or {"pred": "command_matches", "args": {"pattern": "^never-matches$"}},
            "refuse": {"expected": f"not {klass}", "needed": f"avoid {klass}"}}
    base.update(extra)
    return base


class _Engine(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="engine-")
        self.addCleanup(self.world.close)
        self.tmp = self.world.root
        self.home = self.world.home
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = support.isolated_env(self.home)
        support.git(self.repo, self.env, "-c", "init.defaultBranch=dev", "init", "-q")
        support.configure_identity(self.repo, self.env)
        (self.repo / "ops").mkdir()
        (self.repo / "ops" / "lane.conf").write_text("LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\nRELEASE_BRANCH='master'\nSCOPE_CAP='1024'\n")
        support.git(self.repo, self.env, "add", "-A")
        support.git(self.repo, self.env, "commit", "-q", "-m", "seed")
        self.registry = self.tmp / "boundaries.json"
        self.env["BOUNDARIES_PATH"] = str(self.registry)

    def plant(self, *rows):
        self.registry.write_text(json.dumps({"schema": "agentscient.boundaries/2", "rows": list(rows)}, indent=1))

    def run_event(self, event, tool_name="Bash", tool_input=None, tool_response=None, extra=None, env=None):
        payload = {"hook_event_name": event, "cwd": str(self.repo), "session_id": "s1"}
        if tool_name:
            payload["tool_name"] = tool_name
            payload["tool_input"] = tool_input or {}
        if tool_response is not None:
            payload["tool_response"] = tool_response
        payload.update(extra or {})
        return run([sys.executable, str(ENGINE)], cwd=self.repo, env=env or self.env,
                              stdin=json.dumps(payload), timeout=60)

    def bash(self, command, event="PreToolUse", **kw):
        return self.run_event(event, "Bash", {"command": command}, **kw)

    def refusal(self, proc, klass, event="PreToolUse"):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "engine let it through")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertEqual(decision, "warn")
        self.assertTrue(reason.startswith(f"[engine:{klass}] {klass}: expected "), reason)
        self.assertIn("; found ", reason)
        self.assertIn("; needed ", reason)
        return reason

    def allowed(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "", proc.stdout)


class RowsAreDataOverPredicates(_Engine):
    def test_a_pre_tool_row_refuses_on_its_predicate_and_passes_otherwise(self):
        self.plant(row("no-rm-rf", when={"pred": "command_matches", "args": {"pattern": r"\brm -rf\b"}}))
        reason = self.refusal(self.bash("rm -rf build"), "no-rm-rf")
        self.assertIn("rm -rf build", reason)
        self.allowed(self.bash("ls -la"))

    def test_composition_all_any_not(self):
        self.plant(row("both", when={"all": [{"pred": "command_matches", "args": {"pattern": "^git push"}},
                                              {"not": {"pred": "command_matches", "args": {"pattern": "lane/"}}}]}),
                   row("either", when={"any": [{"pred": "command_matches", "args": {"pattern": "^npm publish"}},
                                                {"pred": "command_matches", "args": {"pattern": "^docker push"}}]}))
        self.refusal(self.bash("git push origin dev"), "both")
        self.allowed(self.bash("git push origin lane/session-x"))
        self.refusal(self.bash("docker push ghcr.io/x"), "either")
        self.allowed(self.bash("docker build ."))

    def test_rows_bind_to_tools_and_events_and_the_first_refusal_wins(self):
        self.plant(row("first", when={"pred": "command_matches", "args": {"pattern": "x"}}),
                   row("second", when={"pred": "command_matches", "args": {"pattern": "x"}}),
                   row("writes", tools=("Write", "Edit"), when={"pred": "file_written_under", "args": {"prefix": "ops/"}}),
                   row("after", event="PostToolUse", when={"pred": "response_matches", "args": {"pattern": "Traceback"}}))
        self.refusal(self.bash("echo x"), "first")
        self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(self.repo / "notes.md")}))
        self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(self.repo / "ops" / "x.py")}), "writes")
        self.allowed(self.bash("echo x", event="PostToolUse", tool_response={"stdout": "fine"}))
        self.refusal(self.bash("python3 t.py", event="PostToolUse", tool_response={"stdout": "Traceback (most recent call last)"}),
                     "after", event="PostToolUse")

    def test_an_event_with_no_rows_passes(self):
        self.plant(row("only-pre"))
        self.allowed(self.run_event("Stop", tool_name=None))
        self.allowed(self.run_event("SessionStart", tool_name=None, extra={"source": "startup"}))

    def test_session_start_rows_emit_context_not_refusals(self):
        self.plant(row("greet", event="SessionStart", tools=(), when={"pred": "always"},
                       context="the boundary engine is bound to this session; rows: {rows}"))
        proc = self.run_event("SessionStart", tool_name=None, extra={"source": "startup"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("boundary engine", out["hookSpecificOutput"]["additionalContext"])

    def test_an_unknown_predicate_is_could_not_look(self):
        self.plant(row("bogus", when={"pred": "no_such_predicate"}))
        proc = self.bash("ls")
        out = json.loads(proc.stdout)
        reason = out["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(reason.startswith("Diagnostic (advisory): [engine:hook-error]"), reason)
        self.assertIn("no_such_predicate", reason)

    def test_a_missing_or_malformed_registry_is_could_not_look(self):
        proc = self.bash("ls")
        self.assertIn("[engine:hook-error]", proc.stdout)
        self.registry.write_text("{not json")
        self.assertIn("[engine:hook-error]", self.bash("ls").stdout)



class AHardRowAnswersAsk(_Engine):
    """Owner, 2026-09-12: a hard row stays refused for an agent; the owner
    authorizes the action by a harness approval click. escape: ask. Later
    that day: "i don't want asks unless they're critical", so only the two
    rows that destroy history ask; the three whose remedy is the agent's
    own (a branch, a reading, the proof) refuse."""

    def test_an_ask_row_answers_ask_with_the_wire_line(self):
        self.plant(row("hard", escape="ask", when={"pred": "command_matches", "args": {"pattern": "^git push --force"}}),
                   row("plain", escape="none", when={"pred": "command_matches", "args": {"pattern": "^rm -rf"}}))
        proc = self.bash("git push --force origin x")
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", out)
        self.assertTrue(out["additionalContext"].startswith("Diagnostic (advisory): [engine:hard] hard: expected "))
        self.refusal(self.bash("rm -rf build"), "plain")

    def test_only_the_history_destroying_rows_ask(self):
        data = json.loads(LIVE_REGISTRY.read_text(encoding="utf-8"))
        escapes = {r["class"]: r["escape"] for r in data["rows"]}
        for klass in ("force-push", "prune"):
            self.assertEqual(escapes[klass], "ask", klass)
        for klass in ("trunk-commit", "claim-unmeasured", "pr-proof"):
            self.assertEqual(escapes[klass], "none", klass)


class TheVocabularyCoversTheLaneRows(_Engine):
    """The predicates the migrated lane rows use, each proved on its own."""

    def test_git_write_on_protected_and_push_to_protected(self):
        self.plant(row("trunk", when={"any": [{"pred": "git_write_on_protected"}, {"pred": "git_push_to_protected"}]}))
        reason = self.refusal(self.bash("git commit -q -m x"), "trunk")
        self.assertIn("git commit on dev", reason)
        self.refusal(self.bash("git push origin HEAD:dev"), "trunk")
        support.git(self.repo, self.env, "checkout", "-q", "-b", "lane/session-x")
        self.allowed(self.bash("git commit -q -m x"))
        self.allowed(self.bash("git push -u origin lane/session-x"))

    def test_a_cd_inside_the_command_moves_the_directory_the_row_reads(self):
        """2026-09-12: the code form of this row refused `cd <worktree> &&
        git commit` on a session branch, reading the session's directory."""
        self.plant(row("trunk", when={"pred": "git_write_on_protected"}))
        other = self.tmp / "other"
        support.git(self.tmp, self.env, "clone", "-q", str(self.repo), str(other))
        support.configure_identity(other, self.env)
        support.git(other, self.env, "checkout", "-q", "-b", "lane/session-other")
        self.allowed(self.bash(f"cd {other} && git commit -q --allow-empty -m x"))
        self.allowed(self.bash(f"git -C {other} commit -q --allow-empty -m x"))
        self.allowed(self.bash(f"WF_AGENT='A <a@b>' git -C {other} commit -q -F /tmp/msg.txt && git -C {other} push -q -u origin lane/session-other"))
        self.refusal(self.bash(f"git -C {self.repo} commit -q -m x"), "trunk")
        self.refusal(self.bash(f"cd {other} && git checkout -q dev && cd {self.repo} && git commit -q -m x"), "trunk")
        self.refusal(self.bash("git commit -q -m x"), "trunk")

    def test_claim_without_reading(self):
        self.plant(row("claim", when={"pred": "claim_without_reading"}))
        support.git(self.repo, self.env, "checkout", "-q", "-b", "lane/session-x")
        reason = self.refusal(self.bash('git commit -q -m "the gate is enforced"'), "claim")
        self.assertIn("the gate is enforced", reason)
        self.allowed(self.bash('git commit -q -m "the gate is enforced (test_x 3 OK)"'))
        self.refusal(self.bash("cat > ops/conductor/AAR-x.md <<'EOF'\nall done\nEOF"), "claim")

    def test_prune_and_path_form_unittest(self):
        self.plant(row("prune", when={"pred": "prune_target"}), row("form", when={"pred": "unittest_path_form"}))
        self.refusal(self.bash("rm -rf .dev/records"), "prune")
        self.refusal(self.bash("python3 -m unittest ops/x/tests/test_a.py"), "form")
        self.allowed(self.bash("python3 -m unittest discover -s ops/x/tests -p 'test_a.py'"))

    def test_a_fast_forward_of_the_trunk_from_its_remote_is_allowed_and_a_merge_commit_is_refused(self):
        """LD-38 (2026-09-14): nothing moved the main checkout's dev when a
        PR landed, and this row read the fast-forward as a trunk commit with
        no escape. A merge of origin/<trunk> into the trunk that git reports
        as a fast-forward (--ff-only, or HEAD an ancestor of the remote ref)
        creates no commit and passes; every other merge on the trunk is
        refused: --no-ff, a merge of any other ref, a bare merge, and a
        merge onto a trunk that has diverged."""
        self.plant(row("trunk", when={"pred": "git_write_on_protected"}))
        bare = self.tmp / "origin.git"
        support.git(self.tmp, self.env, "clone", "-q", "--bare", str(self.repo), str(bare))
        support.git(self.repo, self.env, "remote", "add", "origin", str(bare))
        ahead = self.tmp / "ahead"
        support.git(self.tmp, self.env, "clone", "-q", str(bare), str(ahead))
        support.configure_identity(ahead, self.env)
        support.git(ahead, self.env, "commit", "-q", "--allow-empty", "-m", "landed on the trunk")
        support.git(ahead, self.env, "push", "-q", "origin", "dev")
        support.git(self.repo, self.env, "fetch", "-q", "origin")
        self.allowed(self.bash("git merge --ff-only origin/dev"))
        self.allowed(self.bash(f"git -C {self.repo} merge --ff-only origin/dev"))
        self.allowed(self.bash("git fetch -q origin && git merge origin/dev"))
        reason = self.refusal(self.bash("git merge --no-ff origin/dev"), "trunk")
        self.assertIn("git merge on dev", reason)
        support.git(self.repo, self.env, "branch", "-q", "lane/session-x", "origin/dev")
        self.refusal(self.bash("git merge --ff-only lane/session-x"), "trunk")
        self.refusal(self.bash("git merge"), "trunk")
        support.git(self.repo, self.env, "commit", "-q", "--allow-empty", "-m", "diverged")
        self.refusal(self.bash("git merge origin/dev"), "trunk")
        # --ff-only on a diverged trunk: git refuses it itself and no commit can result
        self.allowed(self.bash("git merge --ff-only origin/dev"))

    def test_a_file_store_under_the_state_directory_or_a_placement_declaring_file_is_refused(self):
        """T-5 (2026-09-13): the state gate is the CI wall; this is the same
        rule in session. State is rows in state.db through the state engine;
        a JSON or text file written under the lane's state directory, or a
        placement file declaring state.engine file, is refused. A tool
        writing its own store, a .db, a read, and a file in the tree pass."""
        self.plant(row("state", tools=("Bash", "Write", "Edit"), when={"pred": "file_state_store"}))
        state = self.home / ".local" / "state" / "agentscient"
        state.mkdir(parents=True)
        reason = self.refusal(self.bash(f"echo '{{}}' > {state}/claims.json"), "state")
        self.assertIn("claims.json", reason)
        self.refusal(self.bash("cat > ~/.local/state/agentscient/notes.txt <<'EOF'\nx\nEOF"), "state")
        self.refusal(self.bash(f"cp lock.json {state}/deliverable.json"), "state")
        self.refusal(self.bash(f"tee -a {state}/threads.jsonl < row.json"), "state")
        self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(state / "threads.jsonl"), "content": "{}"}), "state")
        self.allowed(self.bash("python3 ops/conductor/tools/handoff.py take T-1 --branch lane/session-x"))
        self.allowed(self.bash(f"sqlite3 {state}/state.db .tables"))
        self.allowed(self.bash(f"cat {state}/deliverable.json"))
        self.allowed(self.bash(f"echo x > {self.repo}/notes.json"))
        self.allowed(self.bash(f"echo x >> {state}/graft-first.log"))
        placement = self.repo / "ops" / "conductor" / "placement" / "new.py.json"
        self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(placement),
                                                            "content": '{"placement": "migrated", "state": {"engine": "file"}}'}), "state")
        self.refusal(self.run_event("PreToolUse", "Edit", {"file_path": str(placement), "old_string": "sqlite",
                                                           "new_string": '"engine": "file"'}), "state")
        self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(placement),
                                                            "content": '{"placement": "migrated", "state": {"engine": "sqlite"}}'}))
        self.refusal(self.bash("cat > ops/conductor/placement/new.py.json <<'EOF'\n{\"state\": {\"engine\": \"file\"}}\nEOF"), "state")


    def test_kit_import_refuses_a_new_test_module_without_the_kit_and_passes_import_exemption_and_existing_files(self):
        """docs/tests.md rule 1 (plan slice 16): a new test module imports
        the kit or declares its exemption; an existing file, a module that
        imports ops.testkit, one carrying the exemption in its first ten
        lines, and a file outside tests/ pass. On the planted registry, then
        on the live one."""
        tests = self.repo / "ops" / "x" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_old.py").write_text("import unittest\n")
        # grandfathered means tracked: a file a touch made is still new
        support.git(self.repo, self.env, "add", "-A")
        support.git(self.repo, self.env, "commit", "-q", "-m", "an old test module")
        bare = "import unittest\n\nclass T(unittest.TestCase):\n    pass\n"
        docstring = '"""\nfrom ops.testkit import World\n"""\nimport unittest\n'
        late_import = ("\n" * 12) + "from ops.testkit import World\n"
        with_kit = "import unittest\n\nfrom ops.testkit import World\n\nclass T(unittest.TestCase):\n    pass\n"
        exempt = "# testkit: exempt reads a hook payload shape only\nimport unittest\n"
        late_exempt = ("\n" * 12) + "# testkit: exempt too late\nimport unittest\n"
        for registry in ("planted", "live"):
            with self.subTest(registry=registry):
                env = dict(self.env)
                if registry == "planted":
                    self.plant(row("kit-import", tools=("Bash", "Write", "Edit", "MultiEdit"), when={"pred": "test_file_without_kit"}))
                else:
                    env.pop("BOUNDARIES_PATH", None)
                reason = self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_new.py"), "content": bare}, env=env), "kit-import")
                self.assertIn("test_new.py", reason)
                self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_late.py"), "content": late_exempt}, env=env), "kit-import")
                self.refusal(self.bash("cat > ops/x/tests/test_here.py <<'EOF'\nimport unittest\nEOF", env=env), "kit-import")
                self.refusal(self.bash("printf 'import unittest' > ops/x/tests/test_redirect.py", env=env), "kit-import")
                self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_kit.py"), "content": with_kit}, env=env))
                self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_exempt.py"), "content": exempt}, env=env))
                self.allowed(self.run_event("PreToolUse", "Edit", {"file_path": str(tests / "test_old.py"), "old_string": "import unittest", "new_string": "import unittest, os"}, env=env))
                self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "support.py"), "content": with_kit}, env=env))
                self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(self.repo / "ops" / "x" / "tool.py"), "content": bare}, env=env))
                self.allowed(self.bash("cat > ops/x/tests/test_heredoc_kit.py <<'EOF'\nfrom ops.testkit import World\nEOF", env=env))
                # Ruled rank 6 (record 20260915T025600Z-adjudicate-codex-68d3d9):
                # a nested tests/ path, an import quoted in a docstring, an
                # exemption without a reason, and a touch followed by a write
                # all slipped the row; a real import after line ten passes.
                self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "fakes" / "test_nested.py"), "content": bare}, env=env), "kit-import")
                self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_docstring.py"), "content": docstring}, env=env), "kit-import")
                self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_no_reason.py"), "content": "# testkit: exempt\nimport unittest\n"}, env=env), "kit-import")
                touched = tests / "test_touched.py"
                touched.write_text("")
                self.refusal(self.run_event("PreToolUse", "Write", {"file_path": str(touched), "content": bare}, env=env), "kit-import")
                self.refusal(self.run_event("PreToolUse", "Edit", {"file_path": str(touched), "old_string": "", "new_string": bare}, env=env), "kit-import")
                self.refusal(self.run_event("PreToolUse", "MultiEdit", {"file_path": str(touched), "edits": [{"old_string": "", "new_string": bare}]}, env=env), "kit-import")
                self.allowed(self.run_event("PreToolUse", "Edit", {"file_path": str(touched), "old_string": "", "new_string": with_kit}, env=env))
                self.allowed(self.run_event("PreToolUse", "Write", {"file_path": str(tests / "test_late_import.py"), "content": late_import}, env=env))


class TheDoneGate(_Engine):
    """Owner, 2026-09-15: a session reported a plan's slices "all done"
    with 210 of 226 test modules still off the kit; "whatever it says -
    is the problem we to solve - right fucking now"; "I need confidence
    that my orders are being carried out". No gate read a done said in
    chat. This row reads the reply at Stop: a reply that says done,
    complete, finished, landed, merged or solved carries one of three
    deliverable.py lines or is sent back with the lock's target lines in
    the reason. stop_hook_active passes; three blocks per session_id
    and the fourth passes, the target lines logged."""

    def stop(self, message, session="s1", active=False):
        return self.run_event("Stop", tool_name=None,
                              extra={"session_id": session, "stop_hook_active": active,
                                     "last_assistant_message": message})

    def gate(self):
        self.plant(row("done-gate", event="Stop", tools=(), when={"pred": "reply_claims_done"}))

    def test_a_reply_that_says_done_without_a_lock_line_is_sent_back_with_the_lock_reading(self):
        self.gate()
        reason = self.refusal(self.stop("All 21 slices landed; the plan is done."), "done-gate", event="Stop")
        self.assertIn("deliverable.py done", reason)
        self.assertIn("the plan is done", reason)
        self.assertIn("lock", reason)

    def test_routine_stop_lookup_never_executes_the_real_target_measure(self):
        self.gate()
        source = HOOKS.parents[2] / "ops/conductor/tools"
        tools = self.repo / "ops/conductor/tools"
        tools.mkdir(parents=True)
        for name in ("deliverable.py", "instrument.py", "laneconf.py"):
            (tools / name).write_bytes((source / name).read_bytes())
        measure = self.world.tools.plant("target-measure", stdout="ok\n")
        env = {**self.env, "WF_AGENT": "Fixture <fixture@example.test>"}
        seeded = run([sys.executable, str(tools / "deliverable.py"), "add", "lane/target",
                      "--why", "test cheap Stop lookup", "--measure", f"{sys.executable} {measure}",
                      "--until", "ok"], cwd=self.repo, env=env, timeout=30)
        self.assertEqual(seeded.exit, 0, seeded.stderr)
        reason = self.refusal(self.stop("The work is done."), "done-gate", event="Stop")
        self.assertIn("unmeasured", reason)
        self.assertEqual(self.world.tools.calls("target-measure"), [])

    def test_a_sha_or_pr_number_is_not_the_lock_reading(self):
        """The reply that started this carried "#216 at aba8600a" and no
        lock line; the claim law's readings do not satisfy this row."""
        self.gate()
        self.refusal(self.stop("The kit plan is complete: #216 merged at aba8600a1234."), "done-gate", event="Stop")

    def test_the_three_deliverable_lines_pass(self):
        self.gate()
        for line in (
            "done lane/session-x: modules=222 on_kit=222 exempt=0 off_kit=0",
            "target: expected the measure to read off_kit=0; found modules=222 on_kit=15 off_kit=205; needed keep going",
            "target lane/session-x: until off_kit=0; reading modules=222 on_kit=15 exempt=2 off_kit=205; short",
        ):
            with self.subTest(line=line):
                self.allowed(self.stop(f"The migration is done for this family.\n{line}\nNext family follows."))

    def test_a_reply_that_claims_nothing_passes_and_a_quoted_claim_is_not_the_writers(self):
        self.gate()
        self.allowed(self.stop("Reading the launcher's review base now; nothing to report yet."))
        self.allowed(self.stop('The owner said "get it done"; I have taken T-5 and started the red.'))

    def lever(self, stdout):
        """A deliverable.py stub in the repo: show prints ``stdout``."""
        tools = self.repo / "ops" / "conductor" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "deliverable.py").write_text(f"import sys\nsys.stdout.write({stdout!r})\n", encoding="utf-8")

    def test_without_a_lock_completion_needs_no_record_or_special_phrase(self):
        self.gate()
        self.lever("no lock\n")
        self.allowed(self.stop("Every deliverable is landed and lifted."))
        self.allowed(self.stop("Every deliverable is landed and lifted.\nno lock\nNothing is held."))
        self.lever("{\"branches\": [\"lane/x\"]}\ntarget lane/x: until off_kit=0; reading off_kit=5; short\n")
        self.refusal(self.stop("Every deliverable is landed and lifted.\nno lock\n"), "done-gate", event="Stop")

    def test_stop_hook_active_passes(self):
        self.gate()
        self.allowed(self.stop("The plan is done.", active=True))

    def test_three_blocks_per_session_then_the_fourth_passes_and_another_session_still_blocks(self):
        self.gate()
        for _ in range(3):
            self.refusal(self.stop("Everything is finished."), "done-gate", event="Stop")
        self.allowed(self.stop("Everything is finished."))
        self.refusal(self.stop("Everything is finished.", session="s2"), "done-gate", event="Stop")
        counter = self.home / ".local" / "state" / "agentscient" / "done-gate.json"
        self.assertTrue(counter.is_file(), "the cap is counted per session under the lane's state directory")
        self.allowed(self.stop("done lane/x: off_kit=0"))
        self.refusal(self.stop("Everything is finished."), "done-gate", event="Stop")
        self.assertEqual(json.loads(counter.read_text())["s1"], 1, "a pass resets the session's count")


    # Row 6 of A-120, 2026-09-16, hand grant 295 (wall). One producer; the
    # second producer is absent by the owner's word, said in the commit and
    # the record. Calibration stands in for it: every case below is a planted
    # fault the row must catch or a clean neighbour it must pass, taken from
    # a real session's replies rather than invented.

    def test_a_stamped_show_line_is_the_lock_reading(self):
        """deliverable.py's target_line appends " (by <agent>)" after met or
        short, and this row's anchor ended at that word, so it rejected the
        very lines it told a session to paste: 4 of show's 21 lines accepted,
        the 17 rejected being every target carrying the stamp."""
        self.gate()
        for line in (
            "target lane/x: until off_kit=0; reading off_kit=0; met (by Claude Opus 5 (1M context) <noreply@anthropic.com>)",
            "target lane/y: until landed=yes; reading landed=no; short (by Claude Fable 5.1 <noreply@anthropic.com>)",
        ):
            with self.subTest(line=line):
                self.allowed(self.stop("The row is done.\n" + line + "\n"))

    def test_a_lock_line_is_the_reading_indented_fenced_or_quoted(self):
        """The row named three remedies and accepted them at column 0 only, so
        a reading pasted the way output is normally pasted was refused. It
        refused its own `target: expected the measure to read ...` line, which
        it prints and then names as the remedy."""
        self.gate()
        refusal = ("target: expected the measure to read donegate=on-dev; "
                   "found donegate=not-on-dev; needed keep going")
        show = "target lane/x: until a=b; reading a=c; short (by Claude Opus 5 <n@a.com>)"
        for pasted in (
            "    " + refusal,
            "  " + show,
            "```\n" + show + "\n```",
            "> " + refusal,
            "\t" + "done lane/x: modules=1",
        ):
            with self.subTest(pasted=pasted):
                self.allowed(self.stop("The row is done.\n" + pasted + "\n"))

    def test_the_row_passes_prose_that_carries_a_completion_word_but_makes_no_claim(self):
        """Measured on one session's 73 replies: 15 firings, 3 of them claims.
        Each line below fired the word-set predicate and claims nothing. The
        second and fourth are the family that made the defect unreportable:
        the gate fired on its own name and on the report of its own misfire."""
        self.gate()
        for quiet in (
            "brief complete, 1 absence(s)",
            "The done gate fired again on a sentence about finishing.",
            "nothing of mine is complete",
            "zero of which claim a deliverable is done",
            "the package C contract against what #253 landed and #254 carries",
            "the job contract demands a fixed-shape result from eight job types",
            "The rule requiring a report before a worker finishes would fire here.",
            "Both corrections verified at my own hand.",
            "the cap row's dependency stated so it can't be landed alone",
            "Without the grant this cannot be finished today.",
        ):
            with self.subTest(quiet=quiet):
                self.allowed(self.stop(quiet, session="q%d" % abs(hash(quiet)) ))

    def test_the_row_still_catches_a_claim_in_the_writers_own_voice(self):
        """A fresh session id per fault: the cap passes the fourth consecutive
        block, so this asserts the predicate and not the cap."""
        self.gate()
        for n, fault in enumerate((
            "All done.",
            "The branch is done and pushed.",
            "I've finished the row and the suite is green.",
            "The deliverable is complete.",
            "We're done here, and the wall slice is complete.",
            "The assignment A-120 is done.",
            "PR #260 is ready for review.",
        )):
            with self.subTest(fault=fault):
                self.refusal(self.stop(fault, session="f%d" % n), "done-gate", event="Stop")

    def test_the_refusal_quotes_the_span_it_matched(self):
        """The row matched the quote-stripped line and quoted line[:160]: two
        different strings, so a trigger word past character 160 produced a
        refusal carrying no trigger word at all and a session could not tell
        what to change. docs/wire.md: found states what was found."""
        self.gate()
        reason = self.refusal(self.stop("x" * 170 + " and the branch is done."),
                              "done-gate", event="Stop")
        self.assertIn("is done", reason,
                      "the refusal quotes the match, not the line's first 160 characters")

    def test_a_completion_word_inside_code_formatting_is_not_the_writers_claim(self):
        """matrix.QUOTED strips double and curly quotes only, so a word in
        backticks or inside a fenced block was still read as the writer's."""
        self.gate()
        self.allowed(self.stop("The scope says `the branch is done` and I am reading it now."))
        self.allowed(self.stop("Quoting the brief:\n```\nthe migration is complete\n```\nStill reading."))

class TheLiveRegistryIsEvaluable(unittest.TestCase):
    def test_every_engine_row_names_known_predicates_and_events(self):
        data = json.loads(LIVE_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "agentscient.boundaries/2")
        with World(prefix="engine-lint-") as world:
            proc = run([sys.executable, str(ENGINE), "lint"], cwd=HOOKS, env=world.env(), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["unknown_predicates"], [])
        self.assertEqual(report["unknown_events"], [])
        self.assertGreaterEqual(report["engine_rows"], 5)
        self.assertGreaterEqual(len(report["predicates"]), 8)


class AcceptanceARowWithNoCode(_Engine):
    def test_a_row_added_as_data_alone_refuses(self):
        """Owner, 2026-09-12: adding a condition is adding a row. A registry
        with one row the engine's source never mentions refuses live."""
        klass = "no-curl-pipe-sh"
        self.assertNotIn(klass, ENGINE.read_text(encoding="utf-8"))
        self.plant(row(klass, when={"pred": "command_matches", "args": {"pattern": r"curl .*\| *(ba)?sh\b"}}))
        reason = self.refusal(self.bash("curl -s https://x/y.sh | sh"), klass)
        self.assertIn("curl", reason)
        self.allowed(self.bash("curl -s https://x/y.json -o y.json"))


if __name__ == "__main__":
    unittest.main()
