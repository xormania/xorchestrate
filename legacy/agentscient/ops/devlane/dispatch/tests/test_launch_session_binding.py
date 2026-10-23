"""The session id the launcher mints must reach the worker that runs under it.

Package C slice two, the launcher half. The acting coordination MCP server
resolves who is calling from the session id in its own environment and
refuses class `session` when there is none. A dispatched worker therefore
holds a participant only if the id the launcher minted for it is in its
environment, under the key `instrument.session()` reads.

Measured before this was written, read-only off the live store at
/home/scientdb/.local/state/agentscient/state.db on 2026-09-16: five
participants, and every one of them has a null `bound_for` and a null
`bound_by`. No row in that store was ever written by `bind-for`. The
launcher already mints a uuid per dispatch (launch.py, `uuid.uuid4()`) and
already hands it to a claude child as `--session-id`, so the id exists and
reaches the argv; what it does not do is put it in the child's environment,
where a worker of any harness could read it.

  L1  the minted session id is in the worker's environment
  L2  it is the same id the record and the argv carry, not a second one
  L3  the witness would see the key if it were set — the guard that keeps
      L1 from passing because the fixture is blind to it rather than
      because the launcher set anything

The second half is the binding itself. The core's `bind-for` is the only
verb that mints a participant for a session that cannot bind itself, and
it records who issued it and for which assignment, so a participant nobody
dispatched is visible as such. The launcher reaches it as a lever run as a
subprocess whose refusal wire is read, never as an import: at 2eeb4707
launch.py names ops/conductor seven times and every one is a path it
points a worker at, and an import would put the core's module state and
the store inside every case of this suite.

  L4  a dispatch that names no assignment is unchanged, and binds nothing
  L5  a bind-for that refuses stops the launch: the refusal is the
      launcher's own, on the wire, and no worker ran
  L6  a dispatch that names an assignment leaves a participant in the
      store for the id it minted, carrying bound_for and bound_by
  L7  a write job that names no assignment is refused before anything is
      minted: no job directory, no record, no child (LD-116)
  L8  a write job that names one reaches bind-for, as L6 does for a read
      job
"""

from __future__ import annotations

import os

import launch_support as ls

from ops.testkit import World  # noqa: F401 - _TempLaunch owns the world

SESSION_KEY = ls.SESSION_KEY


class TheWorkerLearnsTheSessionIdItWasMintedFor(ls._TempLaunch):
    """L1 L2 L3."""

    def test_the_fixture_can_see_the_key_at_all(self):
        """L3, and it runs first for a reason.

        The witness records an allowlist of environment keys, not the whole
        environment. If SESSION_KEY is not in that list, the assertion in
        the next case reads an absent value whether the launcher set one or
        not, and a green there would mean nothing. This case fails loudly
        instead.

        Planted fault: remove SESSION_KEY from `env_keys` in
        launch_support's fake CLI and this fails while the other two still
        pass, which is exactly the blindness it exists to catch.
        """
        source = ls._FAKE_CLI
        self.assertIn("env_keys", source,
                      "the fake CLI no longer builds an env allowlist")
        _before, _, tail = source.partition("env_keys")
        window, _, _ = tail.partition("]")
        self.assertIn(
            SESSION_KEY, window,
            f"the witness allowlist does not carry {SESSION_KEY}, so an "
            f"assertion about the worker's environment cannot fail for the "
            f"right reason")

    def test_the_minted_session_id_reaches_the_worker_s_environment(self):
        """L1."""
        rec, witness, _out, _err = self.launch_ok(harness="claude")
        minted = (rec.get("session") or {}).get("id")
        self.assertTrue(minted, "the record carries no minted session id")
        env = witness.get("env") or {}
        self.assertEqual(
            env.get(SESSION_KEY), minted,
            f"the worker ran without {SESSION_KEY} in its environment, so "
            f"it could hold no participant and every mutation it made would "
            f"refuse class session; the record minted {minted!r} and the "
            f"child saw {env.get(SESSION_KEY)!r}")

    def test_the_environment_and_the_argv_name_one_session(self):
        """L2.

        Two ids would be worse than none: the worker would act under one and
        be recorded under the other, and the store's provenance would point
        at a session that never ran.
        """
        rec, witness, _out, _err = self.launch_ok(harness="claude")
        minted = (rec.get("session") or {}).get("id")
        argv = [str(a) for a in witness.get("argv") or []]
        self.assertIn("--session-id", argv,
                      "a claude child is launched without --session-id")
        on_argv = argv[argv.index("--session-id") + 1]
        env = witness.get("env") or {}
        self.assertEqual(
            on_argv, minted,
            "the argv names a session the record does not")
        self.assertEqual(
            env.get(SESSION_KEY), on_argv,
            f"the child was told one session on its argv ({on_argv!r}) and "
            f"another in its environment ({env.get(SESSION_KEY)!r})")


class TheLauncherBindsTheWorkerItMints(ls.CoordinationCore, ls._TempLaunch):
    """L4 L5 L6."""

    def test_a_dispatch_that_names_no_assignment_is_unchanged(self):
        """L4.

        The binding is an addition, not a toll. A dispatch with no --for
        must launch exactly as it did before, and must not create a store:
        most of this lane's dispatches are reviews with no assignment
        anywhere near them.
        """
        rec, witness, _out, _err = self.launch_ok(harness="claude")
        self.assertTrue(witness.get("argv"), "the worker did not run")
        self.assertIsNone(
            (rec.get("session") or {}).get("bound"),
            "a dispatch naming no assignment recorded a binding")

    def test_a_refused_binding_stops_the_launch(self):
        """L5.

        The refusal is provoked by an assignment that does not exist in a
        store the caller is not bound to, which is the state every fresh
        world is in. What matters is not which class comes back but that
        the launcher hands it on and the worker never starts: a worker
        running unbound is the thing this whole change exists to prevent,
        and a bind that failed silently would leave exactly that.
        """
        code, out, err = self.dispatch(
            self.argv_for(harness="claude", extra=("--for", "A-1")))
        text = self.combined(out, err)
        self.assertNotEqual(code, 0, f"a refused binding still launched: {text!r}")
        self.assertIn("expected", text.lower(), f"no wire line: {text!r}")
        self.assertIn("found", text.lower(), f"no wire line: {text!r}")
        self.assertIn("needed", text.lower(), f"no wire line: {text!r}")
        self.assertFalse(
            self.witness.is_file(),
            "the harness CLI ran although the binding was refused")
        self.assertEqual(list(self.jobs_root.iterdir()), [],
                         "a refused handoff left a staged job and private worker home")
        records = self.repo / ".dev" / "records" / "dispatches"
        self.assertEqual(list(records.glob("*.json")), [],
                         "a refused handoff left a dispatch record")

    def test_a_dispatch_naming_an_assignment_leaves_a_bound_participant(self):
        """L6, the whole point: dispatched=1 rather than dispatched=0."""
        key = self.dispatcher_session()
        aid = self.assignment(key)
        before = self.participants(key)
        os.environ[SESSION_KEY] = key
        self.addCleanup(os.environ.pop, SESSION_KEY, None)
        rec, witness, _out, _err = self.launch_ok(
            argv=self.argv_for(harness="claude", extra=("--for", aid)))
        minted = (rec.get("session") or {}).get("id")
        self.assertTrue(minted, "the record carries no minted session id")
        after = self.participants(key)
        self.assertNotEqual(
            before, after,
            "the store holds the same participants as before the dispatch")
        env = witness.get("env") or {}
        self.assertNotIn(minted, after, "public participant list disclosed a session")
        own = self.core("who", session=minted, profile=env["AGENTSCIENT_HOOK_PROFILE"])
        self.assertEqual(own.returncode, 0, own.stderr)
        self.assertIn(minted, own.stdout)
        self.assertEqual(
            env.get(SESSION_KEY), minted,
            "the worker was bound under one session and run under another")

    def test_a_write_job_that_names_no_assignment_is_refused_before_the_mint(self):
        """L7. origin: LD-116 (ops/conductor/handoff/lane-defects/LD-116.md).

        Every job installs a worker-mode hook profile, and a worker profile
        with no issued participant denies every hook call. Record
        20260922T212631Z-code-grok-123236, author-tests with no --for, spent
        196,151 tokens that way and closed tripped. A write job without an
        assignment is refused on the wire before the launcher mints an id,
        a job directory, a profile or a record.
        """
        for job in ("implement", "author-tests"):
            with self.subTest(job=job):
                code, out, err = self.dispatch(self.argv_for(job, harness="grok"), assign=False)
                lines = err.strip().splitlines()
                self.assertEqual(code, 2, f"a write job with no --for exited {code}: {err!r}")
                self.assertEqual(out, "", "a refusal prints nothing on stdout")
                self.assertEqual(len(lines), 1, f"one refusal line on stderr: {err!r}")
                self.assertRegex(
                    lines[0],
                    r"^participant: expected an assignment \(--for A-n\) to issue this worker a "
                    r"participant through bind-for; found none; needed .*--for")
                self.assertFalse(self.witness.is_file(), "the worker ran with no assignment")
                self.assertEqual(list(self.jobs_root.iterdir()), [],
                                 "a job directory or worker home was created before the refusal")
                records = self.repo / ".dev" / "records" / "dispatches"
                self.assertEqual(sorted(records.glob("*.json")) if records.is_dir() else [], [],
                                 "a dispatch record was written before the refusal")

    def test_a_write_job_naming_an_assignment_reaches_bind_for(self):
        """L8, the clean neighbour of L7."""
        key = self.dispatcher_session()
        aid = self.assignment(key)
        os.environ[SESSION_KEY] = key
        self.addCleanup(os.environ.pop, SESSION_KEY, None)
        rec, witness, _out, _err = self.launch_ok(
            argv=self.argv_for("implement", harness="grok", extra=("--for", aid)))
        minted = (rec.get("session") or {}).get("id")
        self.assertTrue(minted, "the record carries no minted session id")
        env = witness.get("env") or {}
        own = self.core("who", session=minted, profile=env["AGENTSCIENT_HOOK_PROFILE"])
        self.assertEqual(own.returncode, 0, own.stderr)
        self.assertIn(minted, own.stdout)
