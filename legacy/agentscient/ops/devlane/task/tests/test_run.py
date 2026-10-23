"""run: snapshot, launch, supervise, collect — never judge the work.

Written from SPEC.md, before the module had behaviour. Each test names
the contract rule it pins:

  R1  `run` builds its snapshot with fileset.snapshot; never copies a
      tree itself and never git-checkouts
  R2  stamp.ref comes from the snapshot manifest; an unresolvable ref
      is invalid, never a launch
  R3  runtime.role selects the adapter sandbox map; write must not get
      the read-only value (measured: empty deliverable, zero exit)
  R4  an unknown harness is invalid with a note naming it
  R5  a harness whose CLI is not on PATH is invalid with a note —
      never a verdict about the work
  R6  `direct` delegates to verify.check and returns that envelope;
      zero tokens, spend.harness None
  R7  `stub` replays a file named in runtime and launches nothing
  R8  the battery is armed with --cap-out from runtime.caps; a --cap
      that counts re-sent cache is not a default
  R9  a battery trip is status tripped, verdict None, a note; a killed
      run is never a completed one
  R10 a stream_names_cwd candidate must name this snapshot; two runs
      of one harness must not resolve to the same stream
  R11 no stream found means spend is unknown, not zero
  R12 raw output is a file inside the snapshot, named by artifacts.raw,
      never inlined
  R13 run may parse a structured envelope; it must not interpret prose
  R14 a job absent from the table is invalid
  R15 main is a CLI: JSON on stdout; 0 / 1 / 2 / 64

The stub returns {}, "" and 0. Every test below fails on its own
assertion against that, not on an import error and not on a crash.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shlex
import signal
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

import support

from ops.testkit import World
from ops.testkit import run as run_command

run = support.load("run")

# Loaded the way run.py will load them (`import fileset`), not under the
# task_ prefix support.load uses. Wrapping these is how R1 and R6 pin
# delegation without caring how run.py spelled the import.
import envelope  # noqa: E402
import fileset  # noqa: E402
import verify  # noqa: E402


class SharedEnvelopeAcceptance(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="run-envelope-")
        self.addCleanup(self.world.close)

    def test_bare_and_wrapped_results_use_the_shared_validator(self):
        valid = envelope.build("implement", status="ok", verdict="changes",
                               findings=[envelope.finding("p1", "a.py", "fault", "repro")],
                               stamp={"ref": "candidate"})
        valid["commit"] = {"subject": "Repair", "body": "Original change"}
        valid = dict(reversed(list(valid.items())))
        for wrapper in (None, "envelope", "result", "output"):
            for tally in (1, 0, True):
                with self.subTest(wrapper=wrapper, tally=tally):
                    candidate = json.loads(json.dumps(valid))
                    candidate["counts"]["p1"] = tally
                    raw = candidate if wrapper is None else {wrapper: candidate}
                    parsed = run._parse_envelope(json.dumps(raw).encode())
                    if type(tally) is int and tally == 1:
                        self.assertEqual(parsed, candidate)
                    else:
                        self.assertIsNone(parsed)

    def test_later_semantic_failure_does_not_restore_an_earlier_approval(self):
        clean = envelope.build("verify", status="ok", verdict="approve",
                               stamp={"ref": "candidate"})
        broken = json.loads(json.dumps(clean))
        broken["findings"] = [envelope.finding("p1", "a.py", "fault", "repro")]
        raw = (json.dumps(clean) + "\n" + json.dumps(broken)).encode()
        self.assertIsNone(run._parse_envelope(raw))


def _fake_stdout():
    """The fake harness answer as data for ``world.tools``."""
    stdout_mode = os.environ.get("TASK_RUN_STDOUT") or "envelope"
    token = os.environ.get("TASK_RUN_TOKEN") or ""
    claim = os.environ.get("TASK_RUN_CLAIM") or "the battery is never armed"
    verdict = os.environ.get("TASK_RUN_VERDICT") or "changes"
    job = os.environ.get("TASK_RUN_JOB") or "author-tests"
    if stdout_mode == "prose":
        return "VERDICT: approve\nThe change is correct and complete.\n"
    if stdout_mode == "token":
        return token + "\n"
    findings = []
    if verdict != "approve":
        findings.append({
            "severity": "p2",
            "where": "alpha.py §top",
            "claim": claim,
            "reproduce": "python3 -m unittest",
        })
    counts = {"p1": 0, "p2": 0, "p3": 0, "opinions": 0}
    counts["p2"] = len(findings)
    env = {
        "job": job,
        "status": "ok",
        "verdict": verdict,
        "counts": counts,
        "findings": findings,
        "artifacts": {},
        "spend": {"harness": "codex", "total": 0, "out": 0, "runs": 1},
        "stamp": {"ref": "harness-placeholder", "started": None, "ended": None},
        "note": None,
    }
    return json.dumps(env) + "\n"


def write_codex_stream(world, path, *, total, out):
    """One Codex token_count event. breaker.py reads this shape."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "total_tokens": total,
                    "output_tokens": out,
                }
            },
        }
    }
    world.plant_text(path, json.dumps(event) + "\n")


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = run.main(argv)
        except SystemExit as exc:
            # argparse's default is SystemExit(2). The contract is 64
            # for usage; tests pin that by comparing the code, not by
            # letting the exception look like a collection error.
            code = int(exc.code) if exc.code is not None else 0
    return code, out.getvalue(), err.getvalue()


class _TempRun(unittest.TestCase):
    """A throwaway repo, HOME, PATH and fake CLIs. Never the snapshot tree."""

    def setUp(self):
        self.world = World(prefix="task-run-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.home = self.world.home
        self.repo = self.world.cwd / "repo"
        self.bin = self.world.bin
        self.codex_store = self.home / ".codex" / "sessions"
        self.grok_store = self.home / ".grok" / "sessions"
        self.repo.mkdir()
        self.codex_store.mkdir(parents=True)
        self.grok_store.mkdir(parents=True)

        self._git("init")
        self._git("config", "user.name", "run-test")
        self._git("config", "user.email", "run-test@example.test")
        self._git("config", "commit.gpgsign", "false")
        self._write("alpha.py", "alpha v1\n")
        self._write("beta.py", "unchanged\n")
        self.base = self._commit("base")
        self._write("alpha.py", "alpha v2\n")
        self.ref = self._commit("ref")

        self.active_harness = "codex"
        self.record_path = self.world.tools.log_path(self.active_harness)
        os.environ.pop("TASK_RUN_DONE", None)
        os.environ.pop("TASK_RUN_SLEEP", None)
        os.environ["TASK_RUN_STDOUT"] = "envelope"
        os.environ.pop("TASK_RUN_TOKEN", None)
        os.environ.pop("TASK_RUN_CLAIM", None)
        os.environ.pop("TASK_RUN_VERDICT", None)

        self._install_cli("codex")
        self._install_cli("grok")

        self.jobs = {
            "author-tests": {
                "adapter": "harness",
                "deliverable": "a test file that has been run and is red",
                "role": "write",
                "prompt": (
                    "Write tests from the contract at {scope}. "
                    "Ref {ref} base {base}."
                ),
                "constraints": ["do not edit the implementation"],
            },
            "adversarial-review": {
                "adapter": "harness",
                "role": "read",
                "prompt": "Review {ref} against {base}. Aim at: {scope}",
                "constraints": ["read only"],
            },
            "verify": {
                "adapter": "direct",
                "deliverable": "one claim, executed",
                "prompt": None,
                "constraints": ["read only"],
            },
        }
        self.require = {"scope": "pin the runner", "constraints": ["no network"]}
        self.adapters = self.make_adapters()
        self.last_into = None
        self._patches = []
        self._saved_adapters = run.ADAPTERS
        self._saved_comm_path = run.JOBS_PATH

    def tearDown(self):
        self._unpatch()
        run.ADAPTERS = self._saved_adapters
        run.JOBS_PATH = self._saved_comm_path

    def _git(self, *args):
        return self.world.git(*args, cwd=self.repo, timeout=30)

    def _write(self, rel, content):
        p = self.repo / rel
        return self.world.plant_text(p, content)

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("commit", "-m", msg)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _fingerprint(self):
        files = {}
        for p in self.repo.rglob("*"):
            if ".git" in p.parts or not p.is_file():
                continue
            rel = p.relative_to(self.repo).as_posix()
            files[rel] = (
                p.stat().st_mode,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        return {
            "files": files,
            "head": self._git("rev-parse", "HEAD").stdout,
            "status": self._git("status", "--porcelain=v1", "-uall").stdout,
            "index": self._git("ls-files", "-s").stdout,
            "diff": self._git("diff").stdout,
            "staged": self._git("diff", "--cached").stdout,
        }

    def _install_cli(self, name):
        return self.world.tools.plant(
            name,
            stdout=_fake_stdout(),
            delay=float(os.environ.get("TASK_RUN_SLEEP") or "0"),
        )

    def make_adapters(self, *, sandbox=None, stream_names_cwd=True, store=None):
        sandbox = sandbox or {"read": "read-only", "write": "workspace-write"}
        store = str(store or self.codex_store)
        return {
            "codex": {
                "argv": [str(self.bin / "codex"), "exec",
                         "--sandbox", "{sandbox}", "-"],
                "prompt": "stdin",
                "sandbox": dict(sandbox),
                "store": store,
                "stream": "rollout-*.jsonl",
                "stream_names_cwd": stream_names_cwd,
                # The fakes declare dials for the same reason the real
                # adapters do: a runtime dial with nowhere to go is now
                # refused, so a fake without one would refuse every
                # launch in this fixture rather than record an argv.
                "dials": {"model": ["-m", "{model}"]},
            },
            "grok": {
                "argv": [str(self.bin / "grok"), "--prompt-file", "{prompt}",
                         "--output-format", "plain",
                         "--permission-mode", "{sandbox}"],
                "prompt": "file",
                "sandbox": {"read": "plan", "write": "auto"},
                "store": str(self.grok_store),
                "stream": "updates.jsonl",
                "stream_names_cwd": False,
                "dials": {"model": ["-m", "{model}"],
                          "effort": ["--reasoning-effort", "{effort}"]},
            },
            "stub": {"replay": True},
            "direct": {"direct": True},
        }

    def make_context(self, **overrides):
        snap_id = uuid.uuid4().hex
        into = overrides.pop("into", None) or (self.home / f"snap-{snap_id}")
        ctx = {
            "repo": str(self.repo),
            "ref": self.ref,
            "base": self.base,
            "include": ["alpha.py"],
            "into": str(into),
        }
        ctx.update(overrides)
        return ctx

    def call_run(self, job="author-tests", *, context=None, require=None,
                 runtime=None, jobs=None, adapters=None):
        context = context if context is not None else self.make_context()
        self.last_context = context
        self.last_into = Path(context["into"])
        rt = {
            "harness": "codex",
            "model": "test-model",
            # No effort here: `codex --help` offers `-m/--model` and no
            # effort flag, so the adapter declares no way to pass one and
            # a set effort is REFUSED rather than silently dropped. The
            # refusal and the two harnesses that do take an effort dial
            # have their own cases in TheConductorsDialReachesTheHarness.
            "role": "write",
            "caps": {"cap-out": 100000},
            "timeout": 30,
        }
        if runtime:
            rt.update(runtime)
        self.active_harness = str(rt.get("harness") or "")
        self.record_path = self.world.tools.log_path(self.active_harness)
        if self.active_harness in ("codex", "grok"):
            self._install_cli(self.active_harness)
        actual_require = require if require is not None else self.require
        try:
            env = run.run(
                job,
                context=context,
                require=actual_require,
                runtime=rt,
                jobs=jobs if jobs is not None
                else self.jobs,
                adapters=adapters if adapters is not None else self.adapters,
            )
        except Exception as exc:
            self.fail(
                f"run must return an envelope, not raise: "
                f"{type(exc).__name__}: {exc}")
        self.assertIsInstance(env, dict, "run returns an envelope dict")
        return env

    def read_record(self):
        # Presence of the record is the proof the fake CLI started.
        # Asserting only that a launch side-effect is absent is green
        # against the stub, which launches nothing.
        self.assertTrue(
            self.record_path.is_file(),
            "the harness CLI must have been launched",
        )
        calls = self.world.tools.calls(self.active_harness)
        self.assertTrue(calls, "the harness call log is empty")
        call = calls[-1]
        argv = [str(self.world.bin / self.active_harness), *call["argv"]]
        prompt_file = None
        prompt_text = ""
        if "--prompt-file" in argv:
            index = argv.index("--prompt-file")
            if index + 1 < len(argv):
                prompt_file = argv[index + 1]
                prompt_path = Path(prompt_file)
                if prompt_path.is_file():
                    prompt_text = prompt_path.read_text(encoding="utf-8")
        return {
            "argv": argv,
            "cwd": call["cwd"],
            "stdin_sha256": call.get("stdin_sha256"),
            "prompt_file": prompt_file,
            "prompt_text": prompt_text,
        }

    def recorded_argv(self):
        rec = self.read_record()
        argv = rec.get("argv")
        self.assertIsInstance(argv, list)
        self.assertTrue(argv, "recorded argv is empty")
        return [str(part) for part in argv]

    def parse_stdout(self, out):
        # An empty stdout is not an envelope. Failing here against the
        # stub (which prints nothing) is the honest red for CLI tests
        # whose exit code is 0, because that 0 is also the stub's return.
        self.assertTrue(out.strip(), "envelope JSON on stdout")
        try:
            env = json.loads(out)
        except json.JSONDecodeError:
            self.fail(f"stdout was not JSON: {out!r}")
        self.assertIsInstance(env, dict)
        return env

    def assert_invalid(self, env, *, naming=None):
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "invalid")
        # verdict None is the contract's null. Pinning only
        # `is not "changes"` would pass against the stub's {}.
        self.assertIsNone(env.get("verdict"))
        self.assertNotEqual(env.get("verdict"), "changes")
        self.assertNotEqual(env.get("verdict"), "approve")
        note = env.get("note")
        self.assertIsInstance(note, str)
        self.assertTrue(note.strip(), "invalid must say why it could not run")
        if naming is not None:
            self.assertIn(naming, note)

    def _patch_attr(self, obj, name, replacement):
        """Wrap a function on a module run.py may have imported already.

        `from fileset import snapshot` binds a name on run; `import fileset`
        leaves the function on that module. Patching both is how R1/R6 pin
        delegation without guessing the import spelling. Unique (id, name)
        so wrapping fileset and run.fileset (the same object) cannot
        restore a wrapped function over the original.
        """
        if obj is None or not hasattr(obj, name):
            return
        if any(id(o) == id(obj) and n == name for o, n, _ in self._patches):
            return
        self._patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, replacement)

    def _unpatch(self):
        while self._patches:
            obj, name, original = self._patches.pop()
            setattr(obj, name, original)


# ---------------------------------------------------------------------------
# R1
# ---------------------------------------------------------------------------


class RunBuildsTheSnapshotWithFileset(_TempRun):
    """R1 — the snapshot is fileset.snapshot's, not cp -r and not a checkout.

    Hand-rolled copies on 2026-08-22 produced eight snapshots and a
    launch parameter wrong on six of them. fileset.snapshot is the
    one call that writes FILESET.md and reads blobs at ref without
    touching the source worktree.
    """

    def test_the_snapshot_is_a_fileset_snapshot_of_the_ref_not_the_worktree(self):
        self._write("alpha.py", "DIRTY VERSION\n")
        calls = []
        original = fileset.snapshot

        def wrapped(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        self._patch_attr(fileset, "snapshot", wrapped)
        self._patch_attr(getattr(run, "fileset", None), "snapshot", wrapped)
        if getattr(run, "snapshot", None) is original:
            self._patch_attr(run, "snapshot", wrapped)

        env = self.call_run()
        into = self.last_into
        md = into / "FILESET.md"
        written = into / "alpha.py"
        self.assertTrue(
            md.is_file(),
            "FILESET.md is how a fileset.snapshot is told from cp -r",
        )
        self.assertTrue(written.is_file(), "the include set must be written")
        self.assertEqual(written.read_text(encoding="utf-8"), "alpha v2\n")
        self.assertNotEqual(
            written.read_text(encoding="utf-8"), "DIRTY VERSION\n")
        self.assertIn(self.ref, md.read_text(encoding="utf-8"))
        self.assertFalse(
            (into / "beta.py").exists(),
            "include is a closed set; a bystander must not appear",
        )
        self.assertTrue(
            calls,
            "run must call fileset.snapshot, not copy a tree itself",
        )
        # The envelope is the proof the run used the snapshot, not
        # that it merely built one and threw it away.
        stamp = env.get("stamp") if isinstance(env.get("stamp"), dict) else {}
        self.assertEqual(stamp.get("ref"), self.ref)

    def test_the_source_repo_is_not_checked_out_or_mutated(self):
        # A clean repo cannot show checkout damage. The pin is a dirty one.
        self._write("alpha.py", "DIRTY VERSION\n")
        self._write("untracked.txt", "do not delete me\n")
        self._write("to_stage.py", "staged new file\n")
        self._git("add", "to_stage.py")
        before = self._fingerprint()

        env = self.call_run()
        into = self.last_into
        written = into / "alpha.py"
        self.assertTrue(
            written.is_file(),
            "the snapshot must still be produced from a dirty repo",
        )
        self.assertEqual(written.read_text(encoding="utf-8"), "alpha v2\n")
        self.assertEqual(self._fingerprint(), before)
        self.assertIsInstance(env.get("status"), str)
        self.assertNotEqual(env.get("status"), "")


# ---------------------------------------------------------------------------
# R2
# ---------------------------------------------------------------------------


class StampRefComesFromTheManifest(_TempRun):
    """R2 — without a ref the envelope names no state. The manifest is
    the state that was actually snapshotted; the harness's own stamp
    is not that."""

    def test_stamp_ref_is_the_sha_the_manifest_names(self):
        env = self.call_run()
        md = self.last_into / "FILESET.md"
        self.assertTrue(md.is_file(), "the manifest must exist to supply the ref")
        self.assertIn(self.ref, md.read_text(encoding="utf-8"))
        stamp = env.get("stamp") if isinstance(env.get("stamp"), dict) else {}
        self.assertEqual(stamp.get("ref"), self.ref)
        # The fake CLI prints a placeholder. Overlaying it is the rule.
        self.assertNotEqual(stamp.get("ref"), "harness-placeholder")

    def test_an_unresolvable_ref_is_invalid_and_never_launches(self):
        missing = "no-such-ref-7e1c9a3d"
        env = self.call_run(context=self.make_context(ref=missing))
        self.assert_invalid(env)
        note = env.get("note")
        self.assertTrue(
            missing in note or "ref" in note.lower(),
            f"invalid ref must be explained: {note!r}",
        )
        self.assertFalse(
            self.record_path.exists(),
            "an unresolvable ref is never a launch",
        )


# ---------------------------------------------------------------------------
# R3
# ---------------------------------------------------------------------------


class RoleSelectsTheSandbox(_TempRun):
    """R3 — a WRITE role must not receive the adapter's read-only value.

    Measured: `--sandbox read-only` for a role whose deliverable is a
    file produced an empty deliverable and a zero exit — success-shaped
    failure. The map is data; the role is the lookup key.
    """

    def test_a_write_role_receives_the_write_sandbox_not_the_read_value(self):
        # Unique tokens, not the builtin strings: so a hardcoded
        # "workspace-write" cannot satisfy a map it never consulted.
        write_tok = "WRITE-" + uuid.uuid4().hex
        read_tok = "READ-" + uuid.uuid4().hex
        adapters = self.make_adapters(
            sandbox={"read": read_tok, "write": write_tok})
        env = self.call_run(
            runtime={"harness": "codex", "role": "write"},
            adapters=adapters,
        )
        argv = self.recorded_argv()
        self.assertIn(write_tok, argv)
        self.assertNotIn(read_tok, argv)
        self.assertIsInstance(env.get("status"), str)

    def test_a_read_role_receives_the_read_sandbox(self):
        # Contrast: if write-not-readonly were implemented as "never
        # pass a sandbox", this would fail too, and should.
        write_tok = "WRITE-" + uuid.uuid4().hex
        read_tok = "READ-" + uuid.uuid4().hex
        adapters = self.make_adapters(
            sandbox={"read": read_tok, "write": write_tok})
        env = self.call_run(
            job="adversarial-review",
            runtime={"harness": "codex", "role": "read"},
            adapters=adapters,
        )
        argv = self.recorded_argv()
        self.assertIn(read_tok, argv)
        self.assertNotIn(write_tok, argv)
        self.assertIsInstance(env.get("status"), str)

    def test_a_grok_write_role_is_auto_not_plan(self):
        env = self.call_run(
            runtime={"harness": "grok", "role": "write"},
        )
        argv = self.recorded_argv()
        self.assertIn("auto", argv)
        self.assertNotIn("plan", argv)
        self.assertIsInstance(env.get("status"), str)

    def test_builtin_write_values_are_not_the_read_only_ones(self):
        # Measured facts in the adapter table. .get so a missing
        # key is an assertion failure, not a KeyError against the stub.
        cases = (
            ("codex", "workspace-write", "read-only"),
            ("grok", "auto", "plan"),
        )
        for name, write, read in cases:
            with self.subTest(harness=name):
                sandbox = (run.ADAPTERS.get(name) or {}).get("sandbox") or {}
                self.assertEqual(sandbox.get("write"), write)
                self.assertEqual(sandbox.get("read"), read)
                self.assertNotEqual(sandbox.get("write"), sandbox.get("read"))


# ---------------------------------------------------------------------------
# R4
# ---------------------------------------------------------------------------


class AnUnknownHarnessIsInvalid(_TempRun):
    """R4 — never a default, never a guess. The note names the stranger."""

    def test_an_unknown_harness_is_invalid_with_a_note_naming_it(self):
        name = "not-a-real-harness-7e1c9a3d"
        env = self.call_run(runtime={"harness": name})
        self.assert_invalid(env, naming=name)
        self.assertFalse(
            self.record_path.exists(),
            "an unknown harness must not launch a different one",
        )


# ---------------------------------------------------------------------------
# R5
# ---------------------------------------------------------------------------


class AMissingCliIsInvalidNotAVerdict(_TempRun):
    """R5 — 'the harness is missing' and 'the work found nothing' must
    not produce the same envelope. Assert the note, not just the status."""

    def test_a_missing_cli_is_invalid_with_a_note_naming_the_gap(self):
        missing = "run-test-no-such-cli-7e1c9a3d"
        adapters = self.make_adapters()
        adapters["ghost"] = {
            "argv": [missing, "exec", "--sandbox", "{sandbox}", "-"],
            # A dial the fixture sets must have somewhere to go, or the
            # launch is refused for THAT and never reaches the missing
            # CLI this case is about.
            "dials": {"model": ["-m", "{model}"]},
            "prompt": "stdin",
            "sandbox": {"read": "read-only", "write": "workspace-write"},
            "store": str(self.codex_store),
            "stream": "rollout-*.jsonl",
            "stream_names_cwd": True,
        }
        env = self.call_run(runtime={"harness": "ghost", "role": "write"},
                            adapters=adapters)
        self.assert_invalid(env)
        note = env.get("note")
        # The note is the pin. Status-only would treat a silent skip
        # the same as a missing binary.
        self.assertTrue(
            missing in note
            or "PATH" in note
            or "not found" in note.lower()
            or "missing" in note.lower()
            or "absent" in note.lower()
            or "no such" in note.lower(),
            f"note must explain the missing CLI: {note!r}",
        )
        self.assertIsNone(env.get("verdict"))
        findings = env.get("findings") if isinstance(env.get("findings"), list) else []
        self.assertEqual(findings, [])
        self.assertFalse(self.record_path.exists())

    def test_a_missing_cli_is_not_ok_changes_about_the_work(self):
        missing = "run-test-no-such-cli-aa11bb22"
        adapters = self.make_adapters()
        adapters["ghost"] = {
            "argv": [missing],
            "dials": {"model": ["-m", "{model}"]},
            "prompt": "stdin",
            "sandbox": {"read": "read-only", "write": "workspace-write"},
            "store": str(self.codex_store),
            "stream": "rollout-*.jsonl",
            "stream_names_cwd": True,
        }
        env = self.call_run(runtime={"harness": "ghost"}, adapters=adapters)
        self.assertEqual(env.get("status"), "invalid")
        self.assertNotEqual(env.get("status"), "ok")
        self.assertNotEqual(env.get("verdict"), "changes")
        self.assertNotEqual(env.get("verdict"), "approve")
        self.assertIsInstance(env.get("note"), str)
        self.assertTrue(env.get("note", "").strip())


# ---------------------------------------------------------------------------
# R6
# ---------------------------------------------------------------------------


class DirectDelegatesToVerify(_TempRun):
    """R6 — direct runs no harness. It calls verify.check and returns
    that envelope unchanged. require carries claim and command because
    those are verify.check's inputs and the require schema is otherwise
    deferred for this slice.
    """

    def _direct_require(self, command=None):
        return {
            "scope": "python exits 0",
            "constraints": ["read only"],
            "claim": "python exits 0",
            "command": command or [sys.executable, "-c", "pass"],
        }

    def test_direct_delegates_to_verify_check_and_returns_that_envelope(self):
        calls = []
        original = verify.check

        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append((args, kwargs, result))
            return result

        self._patch_attr(verify, "check", wrapped)
        self._patch_attr(getattr(run, "verify", None), "check", wrapped)
        if getattr(run, "check", None) is original:
            self._patch_attr(run, "check", wrapped)

        env = self.call_run(
            "verify",
            require=self._direct_require(),
            runtime={"harness": "direct", "role": "read"},
        )

        self.assertTrue(calls, "direct must delegate to verify.check")
        returned = calls[-1][2]
        # Unchanged: not rebuilt, not re-stamped, not re-spent.
        self.assertEqual(env, returned)
        self.assertEqual(env.get("job"), "verify")
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "approve")

    def test_direct_spend_is_zero_tokens_and_harness_none(self):
        env = self.call_run(
            "verify",
            require=self._direct_require(),
            runtime={"harness": "direct", "role": "read"},
        )
        # runs is 1 because verify DID run. The default 0 would tell
        # worth.py that nothing happened; the stub's missing spend
        # cannot satisfy the full dict.
        self.assertEqual(
            env.get("spend"),
            {"harness": None, "total": 0, "out": 0, "runs": 1},
        )
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(env.get("status"), "ok")

    def test_direct_does_not_launch_a_harness_cli(self):
        env = self.call_run(
            "verify",
            require=self._direct_require(),
            runtime={"harness": "direct", "role": "read"},
        )
        self.assertEqual(env.get("status"), "ok")
        self.assertFalse(
            self.record_path.exists(),
            "direct runs no harness",
        )


class TheDirectJobIsBoundedByItsCap(_TempRun):
    """20260923T000804Z-review-codex-da66dd#3 and dispatch CONTRACT.md: a
    direct job is bounded by its caps.timeout. At cc28e441 verify.check
    got runtime.get("timeout", 300), so a runtime of 7200 ran under a 3600
    cap, and a runtime naming none fell back to 300, never to the cap.
    A runtime below the cap is the clean neighbour."""

    def _applied(self, runtime_timeout):
        self.jobs["verify"]["caps"] = {"timeout": 3600, "turns": 150}
        seen = []
        original = verify.check

        def wrapped(*args, **kwargs):
            seen.append(kwargs.get("timeout"))
            return original(*args, **kwargs)

        self._patch_attr(verify, "check", wrapped)
        self._patch_attr(getattr(run, "verify", None), "check", wrapped)
        env = self.call_run(
            "verify",
            jobs=self.jobs,
            require={"scope": "python exits 0", "constraints": [],
                     "claim": "python exits 0", "command": [sys.executable, "-c", "pass"]},
            runtime={"harness": "direct", "role": "read", "timeout": runtime_timeout},
        )
        self.assertEqual(env.get("status"), "ok", env.get("note"))
        self.assertTrue(seen, "direct must delegate to verify.check")
        return seen[-1]

    def test_a_runtime_above_the_cap_applies_the_cap(self):
        self.assertEqual(self._applied(7200), 3600)

    def test_a_runtime_naming_no_timeout_applies_the_cap(self):
        self.assertEqual(self._applied(None), 3600)

    def test_a_runtime_below_the_cap_is_applied(self):
        self.assertEqual(self._applied(30), 30)


# ---------------------------------------------------------------------------
# R7
# ---------------------------------------------------------------------------


class StubReplaysWithoutLaunching(_TempRun):
    """R7 — the suite must be able to exercise the runner with no CLI
    installed. The recorded stream is named in runtime['replay']."""

    def test_stub_replays_the_named_file_and_does_not_launch(self):
        unique = "REPLAY-CLAIM-" + uuid.uuid4().hex
        recorded = envelope.build(
            "author-tests",
            status="ok",
            verdict="changes",
            findings=[envelope.finding(
                "p2", "alpha.py §top", unique,
                reproduce="python3 -m unittest")],
            spend={"harness": "stub", "total": 11, "out": 4, "runs": 1},
            stamp={"ref": self.ref},
        )
        replay = self.home / "recorded-stream.jsonl"
        # jsonl: a spend event plus the structured envelope, so either
        # a stream parser or an envelope parser can see the recording.
        self.world.plant_text(
            replay,
            json.dumps({
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {
                        "total_tokens": 11, "output_tokens": 4}},
                }
            }) + "\n" + json.dumps(recorded) + "\n",
        )
        env = self.call_run(
            runtime={"harness": "stub", "role": "write",
                     "replay": str(replay)},
        )
        findings = env.get("findings") if isinstance(env.get("findings"), list) else []
        self.assertTrue(findings, "replayed envelope must carry the recorded finding")
        self.assertEqual(findings[0].get("claim"), unique)
        self.assertEqual(env.get("verdict"), "changes")
        self.assertEqual(env.get("status"), "ok")
        self.assertFalse(
            self.record_path.exists(),
            "stub must not launch a harness CLI",
        )

    def test_builtin_stub_adapter_is_replay_data(self):
        self.assertEqual(run.ADAPTERS.get("stub"), {"replay": True})
        self.assertEqual(run.ADAPTERS.get("direct"), {"direct": True})


# ---------------------------------------------------------------------------
# R8
# ---------------------------------------------------------------------------


class TheBatteryIsArmedWithCapOut(_TempRun):
    """R8 — --cap-out is the runaway wire. A --cap that counts re-sent
    cache killed a review (7.0M tokens to redo). The complementary
    pin is R9: when output *does* exceed cap-out, the run trips.
    """

    def test_a_cache_heavy_total_does_not_trip_when_output_is_under_cap_out(self):
        snap_id = uuid.uuid4().hex
        dirname = f"snap-{snap_id}"
        into = self.home / dirname
        stream = self.codex_store / f"rollout-{dirname}.jsonl"
        # 7.0M total is the measured figure. 50 output is under the cap.
        write_codex_stream(self.world, stream, total=7000000, out=50)
        env = self.call_run(
            context=self.make_context(into=into),
            runtime={"harness": "codex", "role": "write",
                     "caps": {"cap-out": 1000}},
        )
        self.assertTrue(
            self.record_path.is_file(),
            "the launch must have happened for the battery to supervise it",
        )
        self.assertEqual(env.get("status"), "ok")
        self.assertNotEqual(env.get("status"), "tripped")
        spend = env.get("spend") if isinstance(env.get("spend"), dict) else {}
        # Accounting may count the cache; the cap must not trip on it.
        self.assertEqual(spend.get("total"), 7000000)
        self.assertEqual(spend.get("out"), 50)


# ---------------------------------------------------------------------------
# R9
# ---------------------------------------------------------------------------


class ABatteryTripIsNotACompletedRun(_TempRun):
    """R9 — a killed run is never reported as a completed one.

    The fake CLI writes the over-budget stream, then sleeps. If the
    battery is armed with --terminate, the COMPLETED marker is never
    written. If the runner just waits for the process, it is.
    """

    def test_a_battery_trip_is_status_tripped_with_no_verdict_and_a_note(self):
        snap_id = uuid.uuid4().hex
        dirname = f"snap-{snap_id}"
        into = self.home / dirname
        stream = self.codex_store / f"rollout-{dirname}.jsonl"
        write_codex_stream(self.world, stream, total=5000, out=5000)
        done = self.home / "completed.marker"
        os.environ["TASK_RUN_SLEEP"] = "8"
        os.environ["TASK_RUN_DONE"] = str(done)
        env = self.call_run(
            context=self.make_context(into=into),
            runtime={"harness": "codex", "role": "write",
                     "caps": {"cap-out": 100}, "timeout": 60},
        )
        self.assertEqual(env.get("status"), "tripped")
        self.assertIsNone(env.get("verdict"))
        self.assertNotEqual(env.get("verdict"), "approve")
        self.assertNotEqual(env.get("verdict"), "changes")
        note = env.get("note")
        self.assertIsInstance(note, str)
        self.assertTrue(note.strip(), "a trip must say which wire fired")
        self.assertFalse(
            done.is_file(),
            "a killed run must not be allowed to complete",
        )


# ---------------------------------------------------------------------------
# R10
# ---------------------------------------------------------------------------


class StreamDiscoveryNamesTheSnapshot(_TempRun):
    """R10 — two concurrent runs of one harness must not resolve to the
    same stream. Measured: a lookup took a sibling session's file and
    one review was supervised against the wrong evidence.

    Plant two candidates; only one names the snapshot. The wrong one
    is newer, so an mtime/latest rule picks it and this fails.
    """

    def test_the_stream_that_names_the_snapshot_is_chosen_over_a_newer_sibling(self):
        snap_id = uuid.uuid4().hex
        sibling_id = uuid.uuid4().hex
        dirname = f"snap-{snap_id}"
        into = self.home / dirname
        winner = self.codex_store / f"rollout-{dirname}.jsonl"
        loser = self.codex_store / f"rollout-snap-{sibling_id}.jsonl"
        write_codex_stream(self.world, winner, total=42, out=7)
        time.sleep(0.05)
        write_codex_stream(self.world, loser, total=999, out=888)
        env = self.call_run(
            context=self.make_context(into=into),
            runtime={"harness": "codex", "role": "write"},
            adapters=self.make_adapters(stream_names_cwd=True),
        )
        self.assertTrue(
            self.record_path.is_file(),
            "the launch must have happened so a stream can be discovered",
        )
        spend = env.get("spend") if isinstance(env.get("spend"), dict) else {}
        self.assertEqual(spend.get("total"), 42)
        self.assertEqual(spend.get("out"), 7)
        self.assertNotEqual(spend.get("total"), 999)


# ---------------------------------------------------------------------------
# R11
# ---------------------------------------------------------------------------


class AbsentSpendIsNotZeroSpend(_TempRun):
    """R11 — if no stream is found the run still completes, and the
    envelope says the spend is unknown. A genuine zero is a stream
    that recorded zero; those two must not look the same.

    envelope.py requires spend.total to be a non-negative integer, so
    unknown cannot live there as None. The note is how the envelope
    *says* unknown; the pair of envelopes is how we tell them apart.
    """

    def test_no_stream_is_unknown_spend_not_a_measured_zero(self):
        unknown = self.call_run(
            runtime={"harness": "codex", "role": "write"},
            adapters=self.make_adapters(stream_names_cwd=True),
        )
        self.assertTrue(
            self.record_path.is_file(),
            "the run still completes — the CLI launched; only the stream is missing",
        )
        self.assertEqual(unknown.get("status"), "ok")
        self.assertNotEqual(unknown.get("status"), "tripped")

        u_note = unknown.get("note")
        u_spend = unknown.get("spend") if isinstance(unknown.get("spend"), dict) else {}
        u_text = u_note.lower() if isinstance(u_note, str) else ""
        says_unknown = any(
            word in u_text
            for word in (
                "unknown", "no stream", "absent", "missing stream",
                "stream not found", "without a stream", "no session",
            )
        )
        self.assertTrue(
            says_unknown or u_spend.get("total") not in (0, None),
            "the envelope must say the spend is unknown, not look like "
            f"a zero: spend={u_spend!r} note={u_note!r}",
        )

        # Genuine zero: a stream that recorded 0/0, named for this snapshot.
        if self.record_path.exists():
            self.record_path.unlink()
        snap_id = uuid.uuid4().hex
        dirname = f"snap-{snap_id}"
        into = self.home / dirname
        stream = self.codex_store / f"rollout-{dirname}.jsonl"
        write_codex_stream(self.world, stream, total=0, out=0)
        zero = self.call_run(
            context=self.make_context(into=into),
            runtime={"harness": "codex", "role": "write"},
            adapters=self.make_adapters(stream_names_cwd=True),
        )
        z_spend = zero.get("spend") if isinstance(zero.get("spend"), dict) else {}
        self.assertEqual(z_spend.get("total"), 0)
        self.assertEqual(z_spend.get("out"), 0)
        z_text = (zero.get("note") or "").lower() if isinstance(zero.get("note"), str) else ""
        self.assertFalse(
            any(word in z_text for word in ("unknown", "no stream", "missing stream")),
            f"a measured zero must not be labelled unknown: {zero.get('note')!r}",
        )
        self.assertNotEqual(
            (u_spend, u_note),
            (z_spend, zero.get("note")),
            "unknown spend and a genuine zero must be distinguishable",
        )


# ---------------------------------------------------------------------------
# R12
# ---------------------------------------------------------------------------


class RawOutputLivesInsideTheSnapshot(_TempRun):
    """R12 — artifacts are handles. The output text belongs in a file
    inside the snapshot, not in /tmp and not in the envelope. Inlining
    it is how iteration N starts costing more than iteration 1.
    """

    def test_raw_output_is_inside_the_snapshot_and_not_inlined(self):
        token = "RAWOUT-" + uuid.uuid4().hex
        os.environ["TASK_RUN_STDOUT"] = "token"
        os.environ["TASK_RUN_TOKEN"] = token
        env = self.call_run()
        into = self.last_into.resolve()
        artifacts = env.get("artifacts") if isinstance(env.get("artifacts"), dict) else {}
        raw = artifacts.get("raw")
        self.assertIsInstance(raw, str)
        self.assertTrue(raw.strip(), "artifacts.raw must name a file")
        raw_path = Path(raw)
        if not raw_path.is_absolute():
            raw_path = into / raw_path
        raw_path = raw_path.resolve()
        self.assertTrue(raw_path.is_file(), f"artifacts.raw is not a file: {raw!r}")
        self.assertTrue(
            raw_path == into or into in raw_path.parents,
            f"raw {raw_path} is not inside the snapshot {into}",
        )
        self.assertNotEqual(
            raw_path.parent,
            Path(tempfile.gettempdir()).resolve(),
            "raw output must not be a /tmp tempfile; it belongs in the snapshot",
        )
        body = raw_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn(token, body)
        # The handle may appear in the envelope; the output must not.
        blob = json.dumps(env)
        self.assertNotIn(token, blob)


# ---------------------------------------------------------------------------
# R13
# ---------------------------------------------------------------------------


class RunDoesNotInterpretProse(_TempRun):
    """R13 — run may parse a structured envelope the harness emitted.
    It must not interpret prose. Unparseable output is invalid
    (CONTRACT §Statuses), never an approving verdict read off the page.
    """

    def test_a_structured_envelope_from_the_harness_is_parsed(self):
        unique = "STRUCTURED-CLAIM-" + uuid.uuid4().hex
        os.environ["TASK_RUN_STDOUT"] = "envelope"
        os.environ["TASK_RUN_CLAIM"] = unique
        os.environ["TASK_RUN_VERDICT"] = "changes"
        env = self.call_run()
        findings = env.get("findings") if isinstance(env.get("findings"), list) else []
        claims = [f.get("claim") for f in findings if isinstance(f, dict)]
        self.assertIn(unique, claims)
        self.assertEqual(env.get("verdict"), "changes")
        self.assertEqual(env.get("status"), "ok")

    def test_prose_that_says_approve_does_not_become_a_verdict(self):
        os.environ["TASK_RUN_STDOUT"] = "prose"
        env = self.call_run()
        self.assertNotEqual(env.get("verdict"), "approve")
        self.assertEqual(env.get("status"), "invalid")
        self.assertIsNone(env.get("verdict"))
        note = env.get("note")
        self.assertIsInstance(note, str)
        self.assertTrue(note.strip())


# ---------------------------------------------------------------------------
# R14
# ---------------------------------------------------------------------------


class AnUnknownJobIsInvalid(_TempRun):
    """R14 — a job is data in the table. A name that is not
    there is a refusal, not a chance to improvise a prompt."""

    def test_an_absent_job_is_invalid_with_a_note_naming_it(self):
        name = "no-such-job-7e1c9a3d"
        env = self.call_run(name)
        self.assert_invalid(env, naming=name)
        self.assertFalse(
            self.record_path.exists(),
            "an unknown job must not launch",
        )

    def test_load_jobs_reads_the_json_table(self):
        data = run.load_jobs()
        self.assertIsInstance(data, dict)
        self.assertIn("verify", data)
        self.assertIn("author-tests", data)
        self.assertIn("adversarial-review", data)

    def test_load_jobs_reads_a_path_it_is_given(self):
        path = self.home / "only.json"
        self.world.plant_text(
            path, json.dumps({"only-me": {"adapter": "direct"}}))
        data = run.load_jobs(path)
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), {"only-me"})


# ---------------------------------------------------------------------------
# R15
# ---------------------------------------------------------------------------


class MainIsACli(_TempRun):
    """R15 — stdout is the envelope; the exit code is the verdict
    routed for a caller that does not parse JSON. 64 is usage, not
    argparse's 2, because 2 is already taken by invalid/tripped.
    """

    def setUp(self):
        super().setUp()
        run.ADAPTERS = self.make_adapters()
        comm = self.home / "jobs.json"
        self.world.plant_text(comm, json.dumps(self.jobs))
        run.JOBS_PATH = comm

    def test_a_changes_run_prints_json_and_returns_one(self):
        into = self.home / f"cli-{uuid.uuid4().hex}"
        os.environ["TASK_RUN_VERDICT"] = "changes"
        self._install_cli("codex")
        code, out, _err = run_main([
            "author-tests",
            "--repo", str(self.repo),
            "--ref", self.ref,
            "--base", self.base,
            "--harness", "codex",
            "--role", "write",
            "--into", str(into),
        ])
        self.assertEqual(code, 1)
        env = self.parse_stdout(out)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "changes")

    def test_an_approving_run_prints_json_and_returns_zero(self):
        into = self.home / f"cli-{uuid.uuid4().hex}"
        os.environ["TASK_RUN_VERDICT"] = "approve"
        self._install_cli("codex")
        code, out, _err = run_main([
            "author-tests",
            "--repo", str(self.repo),
            "--ref", self.ref,
            "--harness", "codex",
            "--role", "write",
            "--into", str(into),
        ])
        env = self.parse_stdout(out)
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(code, 0)

    def test_an_invalid_run_prints_json_and_returns_two(self):
        into = self.home / f"cli-{uuid.uuid4().hex}"
        code, out, _err = run_main([
            "no-such-job-7e1c9a3d",
            "--repo", str(self.repo),
            "--ref", self.ref,
            "--into", str(into),
        ])
        self.assertEqual(code, 2)
        env = self.parse_stdout(out)
        self.assertEqual(env.get("status"), "invalid")
        self.assertIsNone(env.get("verdict"))
        self.assertIn("no-such-job-7e1c9a3d", env.get("note") or "")

    def test_a_usage_error_returns_sixty_four(self):
        for argv in (
            [],
            ["author-tests"],
            ["author-tests", "--repo", str(self.repo)],
            ["--bogus"],
        ):
            with self.subTest(argv=argv):
                code, _out, _err = run_main(argv)
                self.assertEqual(code, 64)


# ---------------------------------------------------------------------------
# Prompt delivery (render): the harness sees the filled job.
# ---------------------------------------------------------------------------


class TheHarnessReceivesTheRenderedPrompt(_TempRun):
    """render is the prompt. A launch that drops {scope} or {ref} is
    how a task gets a blank instruction and still exits 0."""

    def test_the_harness_receives_the_scope_and_the_ref(self):
        scope = "SCOPE-" + uuid.uuid4().hex
        self.world.tools.wrap("tee")
        adapters = self.make_adapters()
        adapters["codex"]["argv"] = [str(self.world.bin / "tee")]
        adapters["codex"]["dials"] = {"model": []}
        env = self.call_run(
            require={"scope": scope, "constraints": ["no network"]},
            runtime={"model": None},
            adapters=adapters,
        )
        artifacts = env.get("artifacts") if isinstance(env.get("artifacts"), dict) else {}
        raw = Path(artifacts.get("raw") or "")
        if not raw.is_absolute():
            raw = self.last_into / raw
        self.assertTrue(
            raw.is_file(),
            f"the wrapped capture tool must receive stdin: env={env!r} "
            f"calls={self.world.tools.calls('tee')!r}",
        )
        blob = raw.read_text(encoding="utf-8")
        self.assertIn(scope, blob)
        self.assertIn(self.ref, blob)
        self.assertEqual(len(self.world.tools.calls("tee")), 1)
        self.assertIsInstance(env.get("status"), str)

    def test_render_fills_the_job_template(self):
        scope = "RENDER-" + uuid.uuid4().hex
        text = run.render(
            "author-tests",
            {"repo": str(self.repo), "ref": self.ref, "base": self.base,
             "include": ["alpha.py"], "into": str(self.home / "x")},
            {"scope": scope, "constraints": []},
        )
        self.assertIsInstance(text, str)
        self.assertIn(scope, text)
        self.assertIn(self.ref, text)


@unittest.skipUnless(os.name == "posix", "process groups are posix")
class ATrippedRunLeavesNothingBehind(unittest.TestCase):
    """The runaway guard has to outlive the harness parent.

    Verified as broken before the fix: with the parent already reaped,
    _terminate returned early and a `sleep 60` grandchild was still
    running afterwards (Codex, PR #49).
    """

    @staticmethod
    def _parent(pid):
        try:
            text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            return int(text.rsplit(")", 1)[1].split()[1])
        except (FileNotFoundError, IndexError, OSError, ValueError):
            return -1

    @staticmethod
    def _cmdline(pid):
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes() \
                .replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return ""

    def _orphan_maker(self, world):
        """A kit-run parent that exits fast, leaving one descendant behind."""
        marker = "task-run-orphan-" + uuid.uuid4().hex
        child_path = world.root / "orphan.pid"
        script = (
            f"sleep 60 </dev/null >/dev/null 2>&1 & "
            f"echo $! > {shlex.quote(str(child_path))}; "
            f"sleep 0.2; : # {marker}"
        )
        outcome = {}

        def launch():
            try:
                outcome["result"] = run_command(
                    ["sh", "-c", script],
                    cwd=world.cwd,
                    env=world.env(),
                    timeout=10,
                )
            except BaseException as exc:  # surfaced on the test thread
                outcome["error"] = exc

        thread = threading.Thread(target=launch, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        parent = None
        while time.monotonic() < deadline:
            candidates = [
                int(pid) for pid in os.listdir("/proc") if pid.isdigit()
                and self._parent(pid) == os.getpid()
                and marker in self._cmdline(pid)
            ]
            if candidates and child_path.is_file():
                parent = candidates[0]
                break
            time.sleep(0.01)
        self.assertIsNotNone(parent, "INVALID: kit run did not launch the orphan maker")
        proxy = SimpleNamespace(pid=parent)
        proxy.poll = lambda: outcome.get("result").exit if "result" in outcome else None
        proxy.wait = lambda timeout=None: proxy.poll()
        proxy.terminate = lambda: None
        proxy.kill = lambda: None
        run._remember_group(proxy)
        child = int(child_path.read_text(encoding="utf-8").strip())
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "INVALID: the orphan parent was not reaped")
        if "error" in outcome:
            raise outcome["error"]
        self.assertEqual(outcome["result"].exit, 0, outcome["result"].stderr)
        return proxy, child

    @staticmethod
    def _alive(pid):
        # kill(0) also reports zombies as present.  A killed orphan can stay
        # zombied until PID 1 reaps it in a container, but it is no longer a
        # runnable descendant and must not make this isolation check flaky.
        try:
            if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                return False
        except (FileNotFoundError, IndexError, OSError):
            pass
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_the_group_dies_even_when_the_parent_was_already_reaped(self):
        with World(prefix="task-run-group-") as world:
            proc, child = self._orphan_maker(world)
            self.addCleanup(
                lambda: self._alive(child) and os.kill(child, signal.SIGKILL))
            # the fixture must actually present the condition under test
            self.assertIsNotNone(proc.poll(), "INVALID: parent not reaped")
            self.assertTrue(self._alive(child), "INVALID: no orphan to clean up")
            run._terminate(proc)
            deadline = time.time() + 5
            while self._alive(child) and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(self._alive(child),
                             "the descendant survived a tripped run")

    def test_the_group_is_recorded_at_launch(self):
        with World(prefix="task-run-group-") as world:
            proc, child = self._orphan_maker(world)
            self.addCleanup(
                lambda: self._alive(child) and os.kill(child, signal.SIGKILL))
            self.assertEqual(getattr(proc, "_task_pgid", None), proc.pid,
                             "start_new_session makes the child its own leader")
            run._terminate(proc)


class TheConductorsDialReachesTheHarness(unittest.TestCase):
    """runtime.model and runtime.effort must arrive, or be refused.

    Verified as dropped before the fix: `_adapter_argv` formatted both
    into a mapping no template referenced and no env carried, so every
    launch used harness defaults while CONTRACT.md sold the dial as what
    makes "eight cheap ones" and "one careful one" the same job
    (Codex + Grok, PR #49).

    Every flag below was read from the CLI's own --help, not from a
    review: a review reported grok's as `--effort`; it is
    `--reasoning-effort`.
    """

    def rendered(self, harness, runtime):
        return run._adapter_argv(run.ADAPTERS[harness], sandbox="plan",
                                 prompt="/tmp/p", root="/tmp/r", runtime=runtime)

    def test_grok_receives_both_dials(self):
        argv = self.rendered("grok", {"model": "M", "effort": "E"})
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "M")
        self.assertIn("--reasoning-effort", argv)
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "E")

    def test_claude_receives_both_dials(self):
        argv = self.rendered("claude", {"model": "M", "effort": "E"})
        self.assertEqual(argv[argv.index("--model") + 1], "M")
        self.assertEqual(argv[argv.index("--effort") + 1], "E")

    def test_codex_receives_the_model_dial(self):
        argv = self.rendered("codex", {"model": "M"})
        self.assertEqual(argv[argv.index("-m") + 1], "M")

    def test_codex_receives_both_dials(self):
        tokens = ["-c", 'model_reasoning_effort="E"']
        try:
            argv = self.rendered("codex", {"model": "M", "effort": "E"})
        except run.RunError as exc:
            self.fail(
                f"codex argv must carry adjacent {tokens}; "
                f"adapter refused: {exc}"
            )
        self.assertEqual(argv[argv.index("-m") + 1], "M")
        self.assertTrue(
            any(argv[i:i + 2] == tokens for i in range(len(argv) - 1)),
            f"codex argv must carry adjacent {tokens}: {argv!r}",
        )

    def test_an_unset_dial_adds_nothing(self):
        """The control: without the dial the argv is the bare template."""
        bare = self.rendered("grok", {})
        self.assertNotIn("-m", bare)
        self.assertNotIn("--reasoning-effort", bare)


class TheRealAdaptersBindToTheirEvidence(unittest.TestCase):
    """Pins on the shipped adapter table, not the fixture's fakes.

    The fakes can be configured per case; these are the values a live
    run actually uses, and two of them were defects.
    """

    def test_grok_discovery_is_bound_to_the_snapshot(self):
        """False here made _discover_stream take the newest updates.jsonl
        anywhere under the global store, so a concurrent Grok session
        could be supervised, its spend charged here, or the wrong task
        terminated (Codex + Grok, PR #49). Grok url-encodes only `/`, so
        the snapshot's directory name survives in the path and the
        marker test in _stream_names_snapshot matches it."""
        self.assertIs(run.ADAPTERS["grok"]["stream_names_cwd"], True)

    def test_every_live_adapter_can_carry_a_model(self):
        for harness in ("codex", "grok", "claude"):
            with self.subTest(harness=harness):
                dials = run.ADAPTERS[harness].get("dials") or {}
                self.assertIn("model", dials,
                              f"{harness} cannot carry runtime.model")


class TheJobAsksForWhatTheRunnerParses(unittest.TestCase):
    """A job whose prompt and parser disagree returns `invalid`
    however well the worker behaves (Codex + Grok, PR #49)."""

    @staticmethod
    def _jobs():
        raw = json.loads(run.JOBS_PATH.read_text(encoding="utf-8"))
        return raw.get("jobs", raw)

    def test_adversarial_review_requests_the_typed_envelope(self):
        prompt = self._jobs()["adversarial-review"]["prompt"]
        self.assertNotIn("CONTRIB.md review wire format", prompt,
                         "the prose wire format is the human review "
                         "format, not this job's stdout")
        self.assertIn("JSON", prompt)
        for field in ("status", "verdict", "findings"):
            self.assertIn(field, prompt, f"the prompt never names {field}")

    def test_the_requested_keys_are_the_ones_the_parser_accepts(self):
        """Pinned against envelope.FIELDS rather than a copy of the list,
        so the prompt cannot drift from the parser."""
        prompt = self._jobs()["adversarial-review"]["prompt"]
        for field in envelope.FIELDS:
            self.assertIn(field, prompt,
                          f"envelope field {field!r} is not requested")


class TheDocAndTheRegistryAgree(unittest.TestCase):
    """CONTRACT.md's job table and jobs.json must name the same set.

    They did not: the table listed four and the file held three --
    `sweep` was documented and did not exist. That is the same shape as
    the planted-fault promise one section further down, and the reason
    it survived is that nothing compared the two. Comparing them is
    three lines, so it is done here rather than noticed again later.
    """

    @staticmethod
    def _table_names():
        text = run.CONTRACT_PATH.read_text(encoding="utf-8") \
            if hasattr(run, "CONTRACT_PATH") else \
            (Path(run.JOBS_PATH).parent / "CONTRACT.md").read_text(encoding="utf-8")
        names, seen_header = set(), False
        for line in text.splitlines():
            if line.startswith("| job ") or line.startswith("| `job`"):
                seen_header = True
                continue
            if seen_header:
                if not line.startswith("|"):
                    break
                cell = line.split("|")[1].strip()
                if cell.startswith("`") and cell.endswith("`"):
                    names.add(cell.strip("`"))
        return names

    def test_every_documented_job_exists(self):
        registry = set(json.loads(Path(run.JOBS_PATH).read_text(encoding="utf-8")))
        documented = self._table_names()
        self.assertTrue(documented, "INVALID: no job table found in CONTRACT.md")
        self.assertEqual(documented - registry, set(),
                         "documented but not built")

    def test_every_built_job_is_documented(self):
        registry = set(json.loads(Path(run.JOBS_PATH).read_text(encoding="utf-8")))
        documented = self._table_names()
        self.assertEqual(registry - documented, set(),
                         "built but not documented")


if __name__ == "__main__":
    unittest.main()
