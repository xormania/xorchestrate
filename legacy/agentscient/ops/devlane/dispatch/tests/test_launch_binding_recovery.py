"""Stable dispatch bindings, distinct from interactive native sessions.

Grant 309; one dev-lane producer; independent test author absent.
Failure cases derive from the independent review of PR #262, preserved in
the recovery archive's review-262-evidence/probe_boundaries.py.
"""

import json
import os
import shutil
from pathlib import Path

import launch_support as ls
from test_launch_coordination_mcp import _Acting

from ops.testkit import World, run  # noqa: F401 - _Acting owns the World


class BindingRecovery(_Acting):
    def useful_call(self, rec, binding, env=None):
        if env is None:
            env = self.launch._child_env(dict(os.environ), {}, rec["id"], binding)
            env["AGENTSCIENT_HOOK_PROFILE"] = str(Path(rec["snapshot"]["root"]).parent / "enforcement/binding.json")
        env.pop("WF_AGENT", None)
        argv = list(self.acting(rec)["server"])
        argv[-1:] = ["call", "coord_brief", "{}"]
        result = run(argv, env=env, cwd=ls.REPO, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def check_resume(self, *, relocate=False, legacy=False):
        rec, witness, _assignment = self.dispatched()
        minted = witness["env"][ls.SESSION_KEY]
        self.useful_call(rec, minted)
        old_job = Path(rec["snapshot"]["root"]).parent
        if legacy:
            path = old_job / "record.json"
            data = json.loads(path.read_text())
            data["session"].pop("binding_id", None)
            path.write_text(json.dumps(data))
        if relocate:
            store = self.home / "relocated"
            store.mkdir()
            shutil.move(str(old_job), str(store / rec["id"]))
            os.environ["DISPATCH_JOBS"] = str(store)
            self.addCleanup(os.environ.__setitem__, "DISPATCH_JOBS", str(self.jobs_root))
        self.witness.unlink()
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, self.combined(out, err))
        seen = self.read_witness()["env"][ls.SESSION_KEY]
        self.assertEqual(seen, minted, "native conversation id replaced the lane binding")
        self.useful_call(self.read_record(), seen)

    def test_resume_keeps_the_participant_binding(self):
        self.check_resume()

    def test_relocated_resume_keeps_the_participant_binding(self):
        self.check_resume(relocate=True)

    def test_legacy_record_recovers_the_original_binding(self):
        self.check_resume(legacy=True)

    def test_parent_session_does_not_become_the_worker(self):
        dispatcher = self.dispatcher_session()
        assignment = self.assignment(dispatcher)
        os.environ["CLAUDE_CODE_SESSION_ID"] = dispatcher
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        rec, witness, _out, _err = self.launch_ok(argv=self.argv_for(
            harness="codex", job="plan", stage="plan", extra=("--for", assignment)))
        self.useful_call(rec, witness["env"][ls.SESSION_KEY])

    def test_mcp_transport_preserves_the_binding_and_shared_store(self):
        rec, witness, _assignment = self.dispatched()
        binding = witness["env"][ls.SESSION_KEY]
        child = self.launch._child_env(dict(os.environ), {}, rec["id"], binding)
        child["AGENTSCIENT_HOOK_PROFILE"] = witness["env"]["AGENTSCIENT_HOOK_PROFILE"]
        # Verified against installed Codex 0.155.0: custom variables are
        # filtered unless the server registration explicitly forwards them.
        prefix = "mcp_servers.coordination.env_vars="
        flags = rec["harness"]["argv"]
        names = next((json.loads(flag[len(prefix):]) for flag in flags
                      if flag.startswith(prefix)), [])
        forwarded = {key: child[key] for key in ("HOME", "PATH", *names) if key in child}
        self.useful_call(rec, binding, forwarded)

    def test_child_environment_removes_parent_native_aliases(self):
        launch = ls.require_module(self, "launch")
        aliases = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                   "CODEX_THREAD_ID", "CODEX_SESSION_ID")
        base = dict.fromkeys(aliases, "parent-terminal")
        base["PAGER"] = "parent-pager"
        child = launch._child_env(base, dict.fromkeys(aliases, "stale-overlay"),
                                  "job-fixture", "worker-binding")
        self.assertEqual(child[ls.SESSION_KEY], "worker-binding")
        for key in aliases:
            if key != ls.SESSION_KEY:
                self.assertNotIn(key, child)
        self.assertEqual(base["CODEX_THREAD_ID"], "parent-terminal")
