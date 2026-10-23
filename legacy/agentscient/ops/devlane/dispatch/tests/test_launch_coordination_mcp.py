"""A dispatched codex worker is handed the acting coordination server.

The A-87 trial died on this. The launch wired one MCP server, `lane_mcp.py`,
whose tools are all reads, so the worker had no verb to claim an assignment
with and no verb to submit one with; it fell back to a shell it could not
write from, and the assignment it was dispatched for stayed unclaimed.

The acting server is the projection that carries those verbs. It resolves
who is calling from the session id in its own environment and takes the
participant, the assignment and the record from a config file the launcher
writes. Both halves of that already exist here: the launcher mints the
session id and puts it in the worker's environment, and `bind-for` returns
the participant the core minted for it. What is missing is the registration
and the config.

  C1  the capability list the launcher declares is the server's own
      catalogue, so the two cannot drift apart unseen
  C2  the config the launcher writes is one the real server accepts
  C3  it names the participant `bind-for` returned and the assignment the
      dispatch was for, never an agent string
  C4  the codex argv registers the acting server beside the lane one
  C5  a dispatch that binds no participant declares that and wires nothing
  C6  the brief names the acting tools, so the worker knows it has them
  C7  the harnesses not wired today are named as such, not left silent
  C8  the native client requires the acting server and approves exactly the
      capabilities in its issued config; the core still owns authority

One producer by hand under grant 277 at the Lead's assignment relayed by
agentscient-09 (PT-7); the second producer is absent by the owner's word.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import launch_support as ls

from ops.testkit import World, run  # noqa: F401 - _TempLaunch owns the world

SERVER = "coordination"
LANE_KEY = "agentscient_lane"
ACTING = ls.REPO / "ops" / "conductor" / "tools" / "coordination_mcp.py"
CONFIG_NAME = "coordination-mcp.json"
# The exact key set the acting config declares (coordination_mcp.CONFIG_KEYS).
CONFIG_KEYS = {"version", "participant", "assignment", "branch", "worktree",
               "record", "capabilities", "issued_at"}


def catalogue():
    """The acting server's tool names, read out of its source.

    Read, never imported: the launcher reaches this server as a lever and
    so does this suite, so nothing here puts the core's module state or its
    store inside a case.
    """
    text = ACTING.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'^    Tool\("([\w]+)"', text, re.MULTILINE)))


class _Acting(ls.CoordinationCore, ls._TempLaunch):
    """A snapshot carrying both MCP tools, and a store to bind against."""

    def setUp(self):
        super().setUp()
        self._write("ops/conductor/tools/lane_mcp.py",
                    '"""Lane MCP stub for the fixture."""\n')
        self._write("ops/conductor/tools/coordination_mcp.py",
                    ACTING.read_text(encoding="utf-8"))
        self._write("ops/conductor/tools/instrument.py",
                    (ls.REPO / "ops/conductor/tools/instrument.py")
                    .read_text(encoding="utf-8"))
        self._write("ops/conductor/tools/coordination.py",
                    (ls.REPO / "ops/conductor/tools/coordination.py")
                    .read_text(encoding="utf-8"))
        self.ref = self._commit("plant both MCP tools")

    def dispatched(self, harness="codex", **kwargs):
        """Launch with a real assignment bound, and hand back the record."""
        key = self.dispatcher_session()
        aid = self.assignment(key)
        os.environ[ls.SESSION_KEY] = key
        self.addCleanup(os.environ.pop, ls.SESSION_KEY, None)
        rec, witness, _out, _err = self.launch_ok(
            argv=self.argv_for(harness=harness, job="plan", stage="plan",
                               extra=("--for", aid), **kwargs))
        return rec, witness, aid

    def written(self, rec):
        """The config the launcher wrote, as an object.

        The assertion comes first so a head that writes no config fails on
        this case's own claim rather than raising KeyError, which is a
        fixture defect and not a red.
        """
        meta = self.acting(rec)
        self.assertIn(
            "config", meta,
            f"no acting config was written; the record says {meta!r}")
        return json.loads(
            ls.Path(meta["config"]).read_text(encoding="utf-8"))

    def core_check(self, config):
        """The real acting server's own validation of the config, as a
        lever run with the world's environment — the same way a harness
        would start it, minus the serve loop."""
        env = dict(os.environ)
        env["WF_AGENT"] = ls.FIXTURE_DISPATCHER
        return run(
            [sys.executable, str(ACTING), "--config", str(config), "check"],
            timeout=60,
            cwd=str(ls.REPO), env=env)

    def acting(self, rec):
        return (rec.get("brief") or {}).get("coordination") or {}


class TheActingServerIsSourcedFromThePinnedTreeNotTheSnapshot(_Acting):
    """Finding 1 of 20260916T151749Z-review-codex-ef6db7.

    `_coordination_mcp` read its server from the worker's own snapshot
    (`Path(snapshot) / COORD_MCP_REL`), and that script's own `import
    coordination` resolves from `sys.path[0]`, its own directory — the
    same worker-writable tree. A worker that rewrote either file before
    the server started would have the acting server, and everything it
    does with the dispatcher's own authority, run its own code instead.
    The Lead's ruling: the server runs from a path the worker cannot
    write, a frozen copy of the launcher's pinned tree rather than the
    candidate snapshot or the still-changing installation.
    """

    def test_a_worker_owned_tree_on_the_snapshot_is_not_the_source(self):
        """Red at ec736ffa/6213c35a: the planted worker-owned file is
        exactly the one the server argv names and runs.

        Planted fault: revert `_coordination_mcp`'s `tool` to
        `Path(snapshot) / COORD_MCP_REL` and this fails again.
        """
        poison = "\n# a worker-owned line, never trusted: POISON-1\n"
        self._write("ops/conductor/tools/coordination_mcp.py",
                    ACTING.read_text(encoding="utf-8") + poison)
        self._commit("plant a worker-owned coordination_mcp.py")
        rec, _witness, _aid = self.dispatched()
        server = self.acting(rec)["server"]
        script = Path(server[2])
        self.assertTrue(script.is_file(), f"the server names no file: {server!r}")
        self.assertNotIn(
            "POISON-1", script.read_text(encoding="utf-8"),
            f"the acting server ran the worker's own planted tree: {script}")
        binding_path = Path(rec["snapshot"]["root"]).parent / "enforcement/binding.json"
        binding = json.loads(binding_path.read_text())
        self.assertEqual(script.resolve(), Path(binding["source"]) / "ops/conductor/tools/coordination_mcp.py")
        self.assertEqual(script.read_bytes(), ACTING.read_bytes())
        self.assertNotEqual(script.resolve(), ACTING.resolve())


class TheEdgeToTheActingServerIsDeclaredAndChecked(_Acting):
    """C1."""

    def test_the_declared_capabilities_are_the_servers_own_catalogue(self):
        """A capability the server does not carry refuses class `config`
        and kills the launch; one it carries but the launcher omits is a
        verb the worker silently cannot reach. Either way the two lists are
        one edge, and this is the gate on it.

        Planted fault: drop a name from COORD_MCP_TOOLS and this fails.
        """
        launch = ls.require_module(self, "launch")
        declared = sorted(getattr(launch, "COORD_MCP_TOOLS", ()))
        self.assertEqual(
            declared, catalogue(),
            "the launcher's declared capability list and the acting "
            "server's catalogue have drifted apart")


class TheConfigIsOneTheRealServerAccepts(_Acting):
    """C2 C3."""

    def test_the_written_config_checks_clean_against_the_real_server(self):
        rec, _witness, _aid = self.dispatched()
        meta = self.acting(rec)
        self.assertIn(
            "config", meta,
            f"no acting config was written; the record says {meta!r}")
        done = self.core_check(meta["config"])
        self.assertEqual(
            done.returncode, 0,
            f"the real server refused the launcher's config: {done.stderr!r}")

    def test_the_config_carries_exactly_the_keys_the_server_declares(self):
        rec, _witness, _aid = self.dispatched()
        written = self.written(rec)
        self.assertEqual(set(written), CONFIG_KEYS)

    def test_it_names_the_participant_bind_for_returned(self):
        """C3, and the reason the whole binding half came first.

        `participant` is the id the core minted, not an agent string: the
        server refuses class `config` on anything that is not PT-n, and a
        config naming the dispatcher would give the worker the dispatcher's
        authority.
        """
        rec, _witness, aid = self.dispatched()
        written = self.written(rec)
        bound = (rec.get("session") or {}).get("bound") or ""
        self.assertRegex(written["participant"], r"^PT-[1-9][0-9]*$")
        self.assertIn(
            written["participant"], bound,
            f"the config names {written['participant']!r} but bind-for "
            f"returned {bound!r}")
        self.assertEqual(written["assignment"], aid)
        self.assertEqual(written["record"], rec["id"])


class EachHarnessCarriesTheActingServerOrSaysItDoesNot(_Acting):
    """C4 C7."""

    def test_codex_registers_the_acting_server_beside_the_lane_one(self):
        rec, witness, _aid = self.dispatched()
        argv = [str(a) for a in witness["argv"]]
        flags = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-c"]
        self.assertIn(f'mcp_servers.{LANE_KEY}.command="python3"', flags,
                      "the lane server stopped being registered")
        self.assertIn(f'mcp_servers.{SERVER}.command="python3"', flags,
                      f"codex was not handed the acting server; flags {flags!r}")
        args_flag = next(
            f for f in flags if f.startswith(f"mcp_servers.{SERVER}.args="))
        self.assertEqual(json.loads(args_flag.split("=", 1)[1]),
                         self.acting(rec)["server"][1:])

    def test_codex_client_permissions_match_the_issued_capabilities(self):
        """M-233/A-270: discovery alone cannot admit a native acting call.

        Red: the actual generated argv omits required/enabled/per-tool flags.
        Negative neighbors below retain no acting registration when unbound,
        and the real server still refuses a foreign issued-session binding.
        """
        rec, witness, _aid = self.dispatched()
        argv = [str(arg) for arg in witness["argv"]]
        flags = dict(argv[index + 1].split("=", 1)
                     for index, token in enumerate(argv) if token == "-c")
        self.assertEqual(flags.get(f"mcp_servers.{SERVER}.required"), "true")
        enabled = json.loads(flags[f"mcp_servers.{SERVER}.enabled_tools"])
        capabilities = self.written(rec)["capabilities"]
        self.assertEqual(enabled, capabilities)
        approvals = {key: value for key, value in flags.items()
                     if "approval_mode" in key}
        self.assertEqual(approvals, {
            f"mcp_servers.{SERVER}.tools.{name}.approval_mode": '"approve"'
            for name in capabilities})
        self.assertNotIn("*", enabled)

    def test_a_harness_this_does_not_wire_yet_is_named_not_silent(self):
        """C7. Absence is declared, never inferred from silence."""
        rec, witness, _aid = self.dispatched(harness="claude")
        meta = self.acting(rec)
        self.assertIn(
            "unresolved", meta,
            f"claude is not wired for the acting server but the record "
            f"does not say so: {meta!r}")
        self.assertIn("claude", meta["unresolved"])
        argv = " ".join(str(a) for a in witness["argv"])
        self.assertNotIn(f"mcp_servers.{SERVER}", argv)


class ADispatchThatBoundNothingWiresNothing(_Acting):
    """C5."""

    def test_no_assignment_means_a_declared_absence_and_no_registration(self):
        """The acting server cannot be handed a config without a
        participant, and most of this lane's dispatches are reviews that
        name no assignment at all. Those must launch exactly as before.
        """
        rec, witness, _out, _err = self.launch_ok(
            argv=self.argv_for(harness="codex", job="plan", stage="plan"))
        meta = self.acting(rec)
        self.assertIn("unresolved", meta, f"wired without a binding: {meta!r}")
        self.assertIsNone((rec.get("session") or {}).get("bound"))
        argv = " ".join(str(a) for a in witness["argv"])
        self.assertNotIn(f"mcp_servers.{SERVER}", argv)
        self.assertIn(f"mcp_servers.{LANE_KEY}", argv,
                      "the lane server was dropped along with the acting one")


class TheActingBindingResolvesTheDispatchedWorkersOwnSession(_Acting):
    """Finding 2 of 20260916T151749Z-review-codex-ef6db7.

    `resolve_binding` found the participant row a config named and then
    refused unconditionally, citing the slice-one gap (contract section 9,
    divergence 1) — but package B's core verb, `core_bind_for`, had already
    landed at b1d868b0, so the worker `bind-for` minted a participant for
    could never call a verb through the very config the launcher wrote it.
    The fix checks the row's session against the caller's own rather than
    refusing every config-bound call outright.
    """

    def call(self, config_path, session_id, tool="coord_brief", arguments=None):
        env = dict(os.environ)
        env["WF_AGENT"] = ls.FIXTURE_DISPATCHER
        if session_id is None:
            env.pop(ls.SESSION_KEY, None)
        else:
            env[ls.SESSION_KEY] = session_id
        # The worker's private profile is the existing custody carrier. This
        # helper deliberately uses raw synthetic test custody so the foreign
        # session control still reaches core without profile identity rewriting.
        binding = json.loads((Path(config_path).parent / "enforcement/binding.json").read_text())
        env.pop("AGENTSCIENT_HOOK_PROFILE", None)
        env["COORDINATION_CAPABILITY"] = binding["coordination_capability"]
        return run(
            [sys.executable, str(ACTING), "--config", str(config_path),
             "call", tool, json.dumps(arguments or {})],
            timeout=60,
            cwd=str(ls.REPO), env=env)

    def test_the_worker_its_own_config_names_can_call_through_it(self):
        """Red at ec736ffa/6213c35a: this refused class state unconditionally
        for every config-bound call, including the worker's own.

        Planted fault: restore the unconditional `refuse("state", ...)` in
        `resolve_binding`'s config branch and this call refuses again.
        """
        rec, witness, _aid = self.dispatched()
        # Not `rec["session"]["id"]`: for a codex worker that field is
        # overwritten with the harness's own self-reported rollout id once
        # the stream is read (launch.py, `found_id`), while `bind-for` was
        # given the id minted before the child ran, and that id lands in
        # the child's own environment under this same key. That is the
        # session the store bound the participant to.
        minted = (witness.get("env") or {}).get(ls.SESSION_KEY)
        self.assertTrue(minted, "the worker's own environment carries no "
                                 "minted session id")
        config_path = self.acting(rec)["config"]
        done = self.call(config_path, minted)
        self.assertEqual(
            done.returncode, 0,
            f"the worker's own config, called under the session it was "
            f"minted for, still refused: {done.stderr!r}")

    def test_a_config_named_for_a_different_session_still_refuses(self):
        """The fix checks the session rather than dropping the check."""
        rec, _witness, _aid = self.dispatched()
        config_path = self.acting(rec)["config"]
        done = self.call(config_path, str(uuid.uuid4()))
        self.assertEqual(
            done.returncode, 2,
            f"a config bound to one session answered under another: "
            f"stdout={done.stdout!r} stderr={done.stderr!r}")
        self.assertEqual(done.stderr.split(":", 1)[0], "state")

    def test_the_issued_worker_can_claim_before_start_and_submit_through_its_mcp(self):
        lock = run([sys.executable, str(ACTING.with_name("deliverable.py")),
                    "add", "work", "--why", "dispatch handoff regression"],
                   cwd=self.repo, env={**self.env, "WF_AGENT": ls.FIXTURE_DISPATCHER})
        self.assertEqual(lock.exit, 0, lock.stderr)
        launch = self.load_launch()
        attempt = launch._run_attempt
        claims = []

        def before_start(rec, job_dir, **kwargs):
            config = self.written(rec)
            session = rec["session"]["binding_id"]
            path = self.acting(rec)["config"]
            shown = self.call(path, session, "coord_show", {"assignment": config["assignment"]})
            self.assertEqual(shown.returncode, 0, shown.stderr)
            offered = json.loads(shown.stdout)
            self.assertEqual((offered["place"], offered["recipient"]),
                             ("offered", config["participant"]))
            claimed = self.call(path, session, "coord_claim", {
                "assignment": config["assignment"], "branch": config["branch"],
                "generation": offered["generation"]})
            self.assertEqual(claimed.returncode, 0, claimed.stderr)
            claims.append(json.loads(claimed.stdout))
            return attempt(rec, job_dir, **kwargs)

        with patch.object(launch, "_run_attempt", side_effect=before_start):
            rec, witness, aid = self.dispatched()
        self.assertEqual(len(claims), 1)
        self.assertTrue(witness.get("argv"), "the worker did not run")
        submitted = self.call(self.acting(rec)["config"], rec["session"]["binding_id"],
                              "coord_submit", {"assignment": aid, "evidence": self.ref,
                                               "revision": self.ref,
                                               "generation": claims[0]["generation"]})
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertEqual(json.loads(submitted.stdout)["place"], "submitted")


class ARelocatedCoordinationConfigIsRewiredOnResume(_Acting):
    """Finding 3 of 20260916T151749Z-review-codex-ef6db7.

    Same defect class as PR #158's lane-mcp.json (Codex review,
    2026-09-12) — a retained job moved to another dispatch store keeps a
    config naming the old store's path — left unfixed in this PR's own
    sibling: `_relocate_coordination` byte-copied coordination-mcp.json
    instead of parsing and rebasing its `worktree` field the way
    `_relocate_mcp` already rebases lane-mcp.json's `dispatch_store`.
    """

    def relocate(self, rec):
        old_job = Path(rec["snapshot"]["root"]).parent
        new_store = self.home / "jobs-moved"
        new_store.mkdir()
        shutil.move(str(old_job), str(new_store / rec["id"]))
        self.assertFalse(old_job.exists(), "plant: the job left the old store")
        new_job = new_store / rec["id"]
        self.assertTrue(
            (new_job / "coordination-mcp.json").is_file(),
            "plant: the acting config moved with the job")
        os.environ["DISPATCH_JOBS"] = str(new_store)
        self.addCleanup(os.environ.__setitem__, "DISPATCH_JOBS", str(self.jobs_root))
        self.witness.unlink(missing_ok=True)
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, self.combined(out, err))
        return old_job, new_job

    def test_the_configs_worktree_field_names_the_new_job(self):
        """Red at ec736ffa/6213c35a: the byte-copy leaves the old job's
        path inside `worktree` after the resume rewires everything else.

        Planted fault: restore the plain `cfg.write_text(prior.read_text(...))`
        copy in `_relocate_coordination` and this fails again.
        """
        rec, _witness, _aid = self.dispatched()
        old_job, new_job = self.relocate(rec)
        cfg = new_job / "coordination-mcp.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertNotIn(
            str(old_job), data["worktree"],
            f"the acting config still names the old job's path: "
            f"{data['worktree']!r}")
        self.assertTrue(
            Path(data["worktree"]).is_relative_to(new_job),
            f"the acting config's worktree left the new job's tree "
            f"entirely: {data['worktree']!r}")
        record = self.read_record()
        coord = record["brief"]["coordination"]
        self.assertEqual(coord["config"], str(cfg.resolve()))
        self.assertEqual(coord["sha256"], ls.sha256_file(cfg))


class TheBriefNamesTheActingTools(_Acting):
    """C6."""

    def test_a_wired_worker_is_told_which_verbs_it_holds(self):
        """A registered server the brief never mentions is what the trial
        already proved insufficient: the worker reached for a shell.
        """
        rec, _witness, _aid = self.dispatched()
        text = (ls.Path(rec["snapshot"]["root"]).parent / "prompt.txt").read_text(
            encoding="utf-8")
        self.assertIn(SERVER, text)
        for verb in ("coord_claim", "coord_submit", "coord_brief"):
            self.assertIn(verb, text, f"the brief never names {verb}")
