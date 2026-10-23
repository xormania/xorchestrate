"""Receipt lever tests, from evidence.cue, the lever docstring, and the
extraction checklist. The lever is invoked as a subprocess; these tests
do not import it.

RECEIPT_TEST_LEVER is the red-proof seam: point it at a stub that exits
99 and every case fails its first contracted assertion. The frozen
command does not set it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import unittest
from pathlib import Path

import support  # noqa: F401
from ops.testkit import World, run

HARNESS = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
RECEIPTS = Path(".dev") / "records" / "receipts"
AGENT = "Receipt Test <receipt-test@example.invalid>"
FIELDS = (
    "kind",
    "claim",
    "argv",
    "cwd",
    "head_sha",
    "tree_dirty",
    "exit_code",
    "duration_ms",
    "stdout_sha256",
    "stderr_sha256",
    "actor",
    "at",
)
DIGEST = re.compile(r"^[0-9a-f]{64}$")
AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
EMPTY = hashlib.sha256(b"").hexdigest()
REFUSAL = 2


def canonical_text(obj) -> str:
    """Canonical JSON as receipt/canonical.py defines it (PLAN §2)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_digest(obj) -> str:
    return hashlib.sha256(canonical_text(obj).encode("utf-8")).hexdigest()


def lever_path() -> Path:
    override = os.environ.get("RECEIPT_TEST_LEVER", "").strip()
    if override:
        return Path(override)
    return HARNESS / "receipt.py"


class _Repo:
    """One temporary git repository, one commit, a scratch HOME."""

    def setUp(self):
        self.world = World(prefix="receipt-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.repo = self.world.cwd
        self.outside = self.world.root / "outside"
        self.outside.mkdir()
        self.world.require("git")
        self.world.tools.plant("true")
        self.world.tools.plant("false", exit=1)
        self.git("init", "-b", "work")
        self.git("config", "user.name", "receipt-test")
        self.git("config", "user.email", "receipt-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.world.plant_text(self.repo / "README", "receipt fixture\n")
        self.git("add", "README")
        self.git("commit", "-m", "one")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(head, "fixture commit must exist")
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")

    def git(self, *args: str):
        return self.world.git(*args, cwd=self.repo, timeout=60)

    def invoke(self, args, *, agent=AGENT):
        extra = {"TZ": "UTC"}
        if agent is not None:
            extra["WF_AGENT"] = agent
        proc = run(
            [sys.executable, str(lever_path()), *args],
            cwd=self.repo,
            env=self.world.env(extra),
            timeout=30,
        )
        return proc.exit, proc.stdout, proc.stderr

    def record(self, claim, argv, *, cwd=None, allow_dirty=False, agent=AGENT):
        args = ["record", "--claim", claim]
        if cwd is not None:
            args.extend(["--cwd", cwd])
        if allow_dirty:
            args.append("--allow-dirty")
        args.append("--")
        args.extend(list(argv))
        return self.invoke(args, agent=agent)

    def verify(self, receipt, *, agent=AGENT):
        return self.invoke(["verify", receipt], agent=agent)

    def show(self, receipt, *, agent=AGENT):
        return self.invoke(["show", receipt], agent=agent)

    def receipt_files(self):
        directory = self.repo / RECEIPTS
        if not directory.is_dir():
            return []
        return sorted(p for p in directory.glob("*.json") if p.is_file())

    def one_receipt(self):
        files = self.receipt_files()
        self.assertEqual(len(files), 1, files)
        path = files[0]
        body = json.loads(path.read_text(encoding="utf-8"))
        return body, path

    def assert_no_receipt(self):
        self.assertEqual(self.receipt_files(), [])

    def assert_refusal(self, code, out, err, cls, *, found=None, named=()):
        self.assertEqual(
            code, REFUSAL,
            f"{cls}: exit {REFUSAL}, got {code}: out={out!r} err={err!r}",
        )
        self.assertEqual(
            (out or "").strip(), "",
            f"{cls} refusal stdout must be empty: {out!r}",
        )
        lines = [ln for ln in (err or "").splitlines() if ln.strip()]
        self.assertEqual(
            len(lines), 1,
            f"{cls} refusal is one stderr line, got {err!r}",
        )
        line = lines[0]
        self.assertRegex(
            line, rf"^{re.escape(cls)}: expected ",
            f"{cls} class token leads the wire: {line!r}",
        )
        self.assertIn("; found ", line, line)
        self.assertIn("; needed ", line, line)
        self.assertLess(line.index("expected"), line.index("found"), line)
        self.assertLess(line.index("found"), line.index("needed"), line)
        if found is not None:
            self.assertIn(found, line, line)
        for item in named:
            self.assertIn(str(item), line, line)
        return line

    def assert_named_by_digest(self, path: Path):
        self.assertRegex(path.stem, DIGEST, path.name)
        body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            path.stem,
            canonical_digest(body),
            "receipt file name is the canonical digest of the parsed body",
        )

    def planted_body(self, **overrides):
        body = {
            "kind": "receipt",
            "claim": "planted",
            "argv": ["true"],
            "cwd": ".",
            "head_sha": self.head_digest(),
            "tree_dirty": False,
            "exit_code": 0,
            "duration_ms": 0,
            "stdout_sha256": EMPTY,
            "stderr_sha256": EMPTY,
            "actor": AGENT,
            "at": "2026-09-11T00:00:00.000000Z",
        }
        body.update(overrides)
        return body

    def write_json(self, path: Path, obj) -> Path:
        return self.world.plant_text(path, json.dumps(obj) + "\n")

    def marker_argv(self, marker: Path):
        return [
            sys.executable,
            "-c",
            "from pathlib import Path; Path(%r).write_text('ran', encoding='utf-8')"
            % str(marker),
        ]

    def _clear_receipts_tree(self):
        dev = self.repo / ".dev"
        if dev.exists():
            shutil.rmtree(dev)

    def assert_twelve(self, body: dict):
        self.assertEqual(set(body), set(FIELDS), body)
        self.assertEqual(len(body), 12, body)
        self.assertEqual(body["kind"], "receipt")

    def head_digest(self) -> str:
        commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(commit, "HEAD must name a commit")
        return hashlib.sha256(commit.encode("ascii")).hexdigest()

    def cue_vet(self, receipt: Path, definition: str):
        env = self.world.env()
        cue = shutil.which("cue", path=env["PATH"])
        if cue is None:
            self.skipTest("cue is not on PATH")
        return run(
            [cue, "vet", "./ops/devlane/harness/", str(receipt), "-d", definition],
            cwd=REPO,
            env=env,
            timeout=60,
        )


class Record(_Repo, unittest.TestCase):
    def test_absent_identity_refuses_identity(self):
        code, out, err = self.record("x", ["true"], agent=None)
        self.assert_refusal(
            code, out, err, "identity", found="absent identity",
        )
        self.assert_no_receipt()

    def test_identity_without_address_refuses_identity(self):
        agent = "Receipt Test"
        self.assertNotIn("<", agent)
        code, out, err = self.record("x", ["true"], agent=agent)
        self.assert_refusal(code, out, err, "identity", named=(agent,))
        self.assert_no_receipt()

    def test_passing_command_exits_0_and_writes_twelve_field_receipt_named_by_digest(self):
        claim = "a passing command"
        code, out, err = self.record(claim, ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        body, path = self.one_receipt()
        self.assert_twelve(body)
        self.assert_named_by_digest(path)
        self.assertEqual(body["claim"], claim)
        self.assertEqual(body["argv"], ["true"])
        self.assertEqual(body["cwd"], ".")
        self.assertFalse(Path(body["cwd"]).is_absolute(), body["cwd"])
        self.assertIs(body["tree_dirty"], False)
        self.assertEqual(body["exit_code"], 0)
        self.assertIsInstance(body["duration_ms"], int)
        self.assertGreaterEqual(body["duration_ms"], 0)
        self.assertEqual(body["stdout_sha256"], EMPTY)
        self.assertEqual(body["stderr_sha256"], EMPTY)
        self.assertRegex(body["stdout_sha256"], DIGEST)
        self.assertRegex(body["stderr_sha256"], DIGEST)
        self.assertEqual(body["actor"], AGENT)
        self.assertRegex(body["at"], AT)
        self.assertRegex(body["head_sha"], DIGEST)

    def test_passing_receipt_vets_as_admissible_when_cue_is_on_path(self):
        code, out, err = self.record("admissible", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        _body, path = self.one_receipt()
        proc = self.cue_vet(path, "#AdmissibleReceipt")
        self.assertEqual(proc.exit, 0, proc.stderr)

    def test_failing_command_exits_1_and_writes_receipt_with_that_exit_code(self):
        code, out, err = self.record("a failing command", ["false"])
        self.assertEqual(code, 1, f"out={out!r} err={err!r}")
        body, path = self.one_receipt()
        self.assert_twelve(body)
        self.assert_named_by_digest(path)
        self.assertEqual(body["exit_code"], 1)
        self.assertEqual(body["argv"], ["false"])

    def test_head_sha_is_sha256_of_the_commit_id_hex(self):
        want = self.head_digest()
        code, out, err = self.record("head", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        body, _path = self.one_receipt()
        self.assertEqual(body["head_sha"], want)

    def test_dirty_tree_refuses_dirty(self):
        planted = self.repo / "unstaged.txt"
        self.world.plant_text(planted, "dirty\n")
        self.assertEqual(planted.read_text(encoding="utf-8"), "dirty\n")
        self.assertTrue(self.git("status", "--porcelain").stdout.strip())
        code, out, err = self.record("dirty", ["true"])
        self.assert_refusal(code, out, err, "dirty")
        self.assert_no_receipt()

    def test_allow_dirty_records_tree_dirty_true(self):
        planted = self.repo / "unstaged.txt"
        self.world.plant_text(planted, "dirty\n")
        self.assertTrue(self.git("status", "--porcelain").stdout.strip())
        code, out, err = self.record("allow dirty", ["true"], allow_dirty=True)
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        body, path = self.one_receipt()
        self.assert_twelve(body)
        self.assert_named_by_digest(path)
        self.assertIs(body["tree_dirty"], True)

    def test_allow_dirty_receipt_vets_as_receipt_not_admissible_when_cue_is_on_path(self):
        self.world.plant_text(self.repo / "unstaged.txt", "dirty\n")
        code, out, err = self.record("allow dirty cue", ["true"], allow_dirty=True)
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        _body, path = self.one_receipt()
        as_receipt = self.cue_vet(path, "#Receipt")
        self.assertEqual(as_receipt.exit, 0, as_receipt.stderr)
        as_admissible = self.cue_vet(path, "#AdmissibleReceipt")
        self.assertNotEqual(as_admissible.exit, 0, as_admissible.stderr)
        self.assertIn("tree_dirty", as_admissible.stderr)

    def test_absolute_cwd_refuses_cwd(self):
        absolute = str(self.repo.resolve())
        self.assertTrue(Path(absolute).is_absolute())
        self.assertTrue(Path(absolute).is_dir())
        code, out, err = self.record("abs cwd", ["true"], cwd=absolute)
        self.assert_refusal(code, out, err, "cwd", named=(absolute,))
        self.assert_no_receipt()

    def test_cwd_outside_the_root_refuses_cwd(self):
        rel = "../outside"
        target = (self.repo / rel).resolve()
        self.assertEqual(target, self.outside.resolve())
        self.assertTrue(target.is_dir())
        self.assertFalse(target.is_relative_to(self.repo.resolve()))
        code, out, err = self.record("outside cwd", ["true"], cwd=rel)
        self.assert_refusal(code, out, err, "cwd", named=(rel,))
        self.assert_no_receipt()

    def test_malformed_identity_forms_refuse_identity(self):
        for agent in ("<>", "Name <>", " <a@b>"):
            with self.subTest(agent=agent):
                self._clear_receipts_tree()
                code, out, err = self.record("x", ["true"], agent=agent)
                self.assert_refusal(code, out, err, "identity")
                self.assert_no_receipt()

    def test_empty_claim_or_empty_argv_element_refuses_arguments_before_running(self):
        marker = self.home / "empty-ran"
        cases = (
            ("empty-claim", "", self.marker_argv(marker)),
            ("empty-argv-element", "x", self.marker_argv(marker) + [""]),
        )
        for label, claim, command in cases:
            with self.subTest(label=label):
                self._clear_receipts_tree()
                if marker.exists():
                    marker.unlink()
                code, out, err = self.record(claim, command)
                self.assert_refusal(code, out, err, "arguments")
                self.assertFalse(marker.exists(), f"argv ran for {label}")
                self.assert_no_receipt()

    def test_receipts_dir_symlink_outside_root_refuses_and_writes_nothing_outside(self):
        kept = self.repo / ".dev" / "records" / "kept"
        self.world.plant_text(kept, "tracked\n")
        self.git("add", ".dev")
        self.git("commit", "-m", "track .dev")
        leaked = self.outside / "leaked"
        leaked.mkdir()
        receipts = self.repo / RECEIPTS
        receipts.symlink_to(leaked)
        self.assertTrue(receipts.is_symlink())
        self.assertEqual(receipts.resolve(), leaked.resolve())
        before = sorted(p.relative_to(self.outside) for p in self.outside.rglob("*"))
        code, out, err = self.record("symlink receipts", ["true"])
        self.assertEqual(code, REFUSAL, f"out={out!r} err={err!r}")
        self.assertEqual((out or "").strip(), "", out)
        lines = [ln for ln in (err or "").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, err)
        line = lines[0]
        self.assertRegex(line, r"^(receipt|cwd): expected ", line)
        self.assertIn("; found ", line, line)
        self.assertIn("; needed ", line, line)
        after = sorted(p.relative_to(self.outside) for p in self.outside.rglob("*"))
        self.assertEqual(after, before)
        self.assertEqual(list(leaked.glob("*.json")), [])

    def test_non_utf8_outputs_hash_as_sha256_of_raw_bytes(self):
        payloads = (bytes([255]), bytes([254]))
        wants = [hashlib.sha256(p).hexdigest() for p in payloads]
        self.assertNotEqual(wants[0], wants[1])
        got_out, got_err = [], []
        seen: set[Path] = set()
        for i, payload in enumerate(payloads):
            n = payload[0]
            argv = [
                sys.executable, "-c",
                "import os; os.write(1, bytes([%d])); os.write(2, bytes([%d]))" % (n, n),
            ]
            code, out, err = self.record("bytes %d" % n, argv, allow_dirty=True)
            self.assertEqual(code, 0, f"out={out!r} err={err!r}")
            files = self.receipt_files()
            self.assertEqual(len(files), i + 1, files)
            # The listing is sorted by digest, not by creation: pick the
            # receipt this iteration wrote, never the last name in order.
            fresh = [f for f in files if f not in seen]
            self.assertEqual(len(fresh), 1, files)
            seen.add(fresh[0])
            body = json.loads(fresh[0].read_text(encoding="utf-8"))
            got_out.append(body["stdout_sha256"])
            got_err.append(body["stderr_sha256"])
        self.assertEqual(got_out[0], wants[0])
        self.assertEqual(got_out[1], wants[1])
        self.assertEqual(got_err[0], wants[0])
        self.assertEqual(got_err[1], wants[1])
        self.assertNotEqual(got_out[0], got_out[1])

    def test_git_status_failure_refuses_repository(self):
        index = self.repo / ".git" / "index"
        self.assertTrue(index.is_file())
        self.world.plant_bytes(index, b"corrupt")
        probe = run(
            ["git", "status"],
            cwd=self.repo,
            env=self.world.env(),
            timeout=60,
        )
        self.assertNotEqual(probe.exit, 0, probe.stderr)
        code, out, err = self.record("corrupt index", ["true"])
        self.assert_refusal(code, out, err, "repository")
        self.assert_no_receipt()

    def test_argv_element_containing_a_newline_keeps_the_wire_one_line(self):
        token = "line1\nline2"
        self.assertIn("\n", token)
        code, out, err = self.record(
            "newline in argv", ["definitely-no-such-command", token],
        )
        self.assertNotIn(token, err or "")
        lines = [ln for ln in (err or "").splitlines() if ln.strip()]
        if code == REFUSAL:
            self.assertEqual((out or "").strip(), "", out)
            self.assertEqual(len(lines), 1, err)
            line = lines[0]
            self.assertRegex(line, r"^[a-z][a-z0-9-]*: expected ", line)
            self.assertIn("; found ", line, line)
            self.assertIn("; needed ", line, line)
            self.assert_no_receipt()
        else:
            self.assertLessEqual(len(lines), 1, err)
            files = self.receipt_files()
            self.assertEqual(len(files), 1, files)

    def test_second_record_passes_when_dev_is_untracked(self):
        code, out, err = self.record("first", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        self.assertTrue((self.repo / RECEIPTS).is_dir())
        status = self.git("status", "--porcelain").stdout
        self.assertIn(".dev", status)
        tracked = self.git("ls-files", ".dev").stdout.strip()
        self.assertEqual(tracked, "")
        code2, out2, err2 = self.record("second", ["true"])
        self.assertEqual(code2, 0, f"out={out2!r} err={err2!r}")
        files = self.receipt_files()
        self.assertEqual(len(files), 2, files)
        for path in files:
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(body["tree_dirty"], False, body)


class Verify(_Repo, unittest.TestCase):
    def test_deterministic_command_agrees_exit_0(self):
        code, out, err = self.record("reproducible", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        _body, path = self.one_receipt()
        vcode, vout, verr = self.verify(path.stem)
        self.assertEqual(vcode, 0, f"out={vout!r} err={verr!r}")

    def test_new_commit_refuses_state(self):
        code, out, err = self.record("then move HEAD", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        _body, path = self.one_receipt()
        recorded_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.world.plant_text(self.repo / "later.txt", "moved\n")
        self.git("add", "later.txt")
        self.git("commit", "-m", "two")
        new_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(new_head, recorded_head)
        vcode, vout, verr = self.verify(path.stem)
        self.assert_refusal(vcode, vout, verr, "state")

    def test_changed_output_names_the_differing_field_and_exits_1(self):
        counter = self.home / "counter"
        script = (
            "import pathlib, sys\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n + 1))\n"
            "print(n)\n"
        )
        argv = [sys.executable, "-c", script, str(counter)]
        code, out, err = self.record("non-deterministic stdout", argv)
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")
        _body, path = self.one_receipt()
        vcode, vout, verr = self.verify(str(path))
        self.assertEqual(counter.read_text(encoding="utf-8"), "2")
        self.assertEqual(vcode, 1, f"out={vout!r} err={verr!r}")
        reported = (vout or "") + (verr or "")
        self.assertIn("stdout_sha256", reported)

    def test_absolute_or_escaping_cwd_refuses_cwd_and_does_not_run_argv(self):
        marker = self.home / "cwd-ran"
        argv = self.marker_argv(marker)
        cases = ("/", "../outside")
        for cwd in cases:
            with self.subTest(cwd=cwd):
                if marker.exists():
                    marker.unlink()
                body = self.planted_body(claim="cwd " + cwd, argv=argv, cwd=cwd)
                path = self.write_json(self.home / "cwd.json", body)
                code, out, err = self.verify(str(path))
                self.assert_refusal(code, out, err, "cwd")
                self.assertFalse(marker.exists(), f"argv ran for cwd={cwd!r}")

    def test_edited_receipt_refuses_receipt_on_verify_and_show(self):
        code, out, err = self.record("edit me", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        body, path = self.one_receipt()
        digest = path.stem
        body["claim"] = "tampered"
        self.world.plant_text(path, json.dumps(body) + "\n")
        self.assertEqual(path.stem, digest)
        self.assertNotEqual(canonical_digest(body), digest)
        with self.subTest("verify"):
            vcode, vout, verr = self.verify(digest)
            self.assert_refusal(vcode, vout, verr, "receipt")
        with self.subTest("show"):
            scode, sout, serr = self.show(digest)
            self.assert_refusal(scode, sout, serr, "receipt")


class Show(_Repo, unittest.TestCase):
    def test_unknown_digest_refuses_receipt(self):
        digest = "a" * 64
        self.assertEqual(self.receipt_files(), [])
        self.assertFalse((self.repo / RECEIPTS / f"{digest}.json").is_file())
        code, out, err = self.show(digest)
        self.assert_refusal(code, out, err, "receipt", named=(digest,))

    def test_json_file_with_an_extra_field_refuses_receipt(self):
        zeros = "0" * 64
        body = {
            "kind": "receipt",
            "claim": "extra field",
            "argv": ["true"],
            "cwd": ".",
            "head_sha": zeros,
            "tree_dirty": False,
            "exit_code": 0,
            "duration_ms": 0,
            "stdout_sha256": zeros,
            "stderr_sha256": zeros,
            "actor": AGENT,
            "at": "2026-09-11T00:00:00Z",
            "stage": "not-a-receipt-field",
        }
        path = self.home / "extra.json"
        self.world.plant_text(path, json.dumps(body) + "\n")
        planted = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("stage", planted)
        self.assertEqual(len(planted), 13)
        code, out, err = self.show(str(path))
        self.assert_refusal(code, out, err, "receipt")

    def test_bare_invocation_refuses_arguments(self):
        code, out, err = self.invoke([])
        self.assert_refusal(code, out, err, "arguments")

    def test_prints_the_canonical_json(self):
        code, out, err = self.record("show me", ["true"])
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        body, path = self.one_receipt()
        scode, sout, serr = self.show(path.stem)
        self.assertEqual(scode, 0, f"out={sout!r} err={serr!r}")
        want = canonical_text(body)
        self.assertIn(
            sout,
            (want, want + "\n"),
            "show must print exactly the canonical serialization, got %r" % (sout,),
        )

    def test_ill_typed_twelve_key_file_refuses_receipt(self):
        zeros = "0" * 64
        base = self.planted_body(
            claim="wrong types",
            head_sha=zeros,
            stdout_sha256=zeros,
            stderr_sha256=zeros,
        )
        variants = (
            {"tree_dirty": "no"},
            {"exit_code": "0"},
            {"duration_ms": -1},
            {"head_sha": "a" * 40},
        )
        for override in variants:
            with self.subTest(override=override):
                body = dict(base)
                body.update(override)
                self.assertEqual(len(body), 12, body)
                path = self.write_json(
                    self.home / ("typed-%s.json" % next(iter(override))),
                    body,
                )
                code, out, err = self.show(str(path))
                self.assert_refusal(code, out, err, "receipt")

    def test_json_array_refuses_receipt(self):
        path = self.write_json(self.home / "array.json", [])
        code, out, err = self.show(str(path))
        self.assert_refusal(code, out, err, "receipt")
        self.assertNotIn("Traceback", err or "")
        self.assertNotIn("AttributeError", err or "")

    def test_missing_required_operands_refuse_arguments(self):
        cases = (
            ["record", "--", "true"],
            ["verify"],
            ["show"],
        )
        for args in cases:
            with self.subTest(args=args):
                code, out, err = self.invoke(args)
                self.assert_refusal(code, out, err, "arguments")
                self.assertNotIn("usage:", (err or "").lower())
                if args[0] == "record":
                    self.assert_no_receipt()


if __name__ == "__main__":
    unittest.main()
