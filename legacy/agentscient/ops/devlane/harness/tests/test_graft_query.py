"""Prepare, query, freshness, worktrees, and provider faults.

Public CLI and Python seams from graft/CONTRACT.md. The fake Node is
the provider; the adapter under test is graft.py.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from ops.testkit import World, plant_text  # noqa: F401 - GraftCase owns World

import support  # noqa: F401
import graft_support as gs

ADMITTED = (
    "graft_find_code",
    "graft_file_api",
    "graft_trace_calls",
    "graft_find_all",
    "graft_repo_map",
)
DESCRIPTOR_KEYS = {
    "schema", "root", "cache", "install",
    "install_receipt_sha256", "scope", "role",
}
BINDING_KEYS = {
    "schema", "prepared_at", "head", "source_sha256",
    "graph_sha256", "cache_sha256", "counts",
}


class _Prepared(gs.GraftCase, unittest.TestCase):
    def prepare(self, *, role=None, files=None):
        self.install = self.plant_fake_install()
        self.root = self.plant_root(files)
        self.cache = self.home / "cache"
        argv = ["prepare", "--root", str(self.root), "--cache", str(self.cache)]
        code, out, err = self.cli(argv)
        self.assertEqual(code, 0, err)
        path = Path(out.strip())
        self.assertTrue(path.is_file(), f"prepare prints a descriptor path: {out!r}")
        self.descriptor_path = path
        self.descriptor = json.loads(path.read_text(encoding="utf-8"))
        return self.descriptor

    def query(self, tool="graft_repo_map", arguments=None, *, identity=True):
        args = "{}" if arguments is None else json.dumps(arguments)
        return self.cli(
            ["query", "--context", str(self.descriptor_path), tool, args],
            identity=identity,
        )


class PrepareWritesDescriptorAndBinding(_Prepared):
    def test_prepare_descriptor_has_exactly_the_contracted_keys(self):
        desc = self.prepare()
        self.assertEqual(set(desc), DESCRIPTOR_KEYS)
        self.assertEqual(desc["schema"], "graft-context/1")
        self.assertEqual(desc["scope"], ["ops"])
        self.assertEqual(desc["role"], "conductor")
        self.assertEqual(Path(desc["root"]).resolve(), self.root.resolve())
        self.assertEqual(Path(desc["cache"]).resolve(), self.cache.resolve())
        self.assertEqual(Path(desc["install"]).resolve(), self.install.resolve())
        receipt = self.install / "receipt.json"
        self.assertEqual(desc["install_receipt_sha256"], gs.sha256_file(receipt))
        binding_path = self.cache / "binding.json"
        self.assertTrue(binding_path.is_file())
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        self.assertEqual(set(binding), BINDING_KEYS)
        self.assertEqual(binding["schema"], "graft-binding/1")
        self.assertEqual(set(binding["counts"]), {"files", "nodes", "edges"})
        head = gs.git(self.root, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(binding["head"], head)
        self.assertEqual(len(binding["head"]), 40)

    def test_prepare_does_not_mutate_source_bytes(self):
        self.install = self.plant_fake_install()
        self.root = self.plant_root()
        before = {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        }
        ignore = self.root / ".gitignore"
        if ignore.is_file():
            before[ignore.relative_to(self.root).as_posix()] = ignore.read_bytes()
        self.cache = self.home / "cache"
        code, _out, err = self.cli(
            ["prepare", "--root", str(self.root), "--cache", str(self.cache)],
        )
        self.assertEqual(code, 0, err)
        after = {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        }
        self.assertEqual(after, before)
        self.assertFalse(
            (self.root / ".graft").exists(),
            "adapter cache must not land under the source root",
        )

    def test_default_cache_is_under_state_home_not_the_checkout(self):
        self.plant_fake_install()
        root = self.plant_root()
        code, out, err = self.cli(["prepare", "--root", str(root)])
        self.assertEqual(code, 0, err)
        desc = json.loads(Path(out.strip()).read_text(encoding="utf-8"))
        cache = Path(desc["cache"]).resolve()
        state = self.world.xdg_state.resolve()
        self.assertTrue(str(cache).startswith(str(state) + os.sep), cache)
        self.assertIn("/graft/", str(cache))
        try:
            cache.relative_to(root.resolve())
        except ValueError:
            pass
        else:
            self.fail(f"default cache escaped into the source root: {cache}")

    def test_cache_inside_root_is_refused(self):
        self.plant_fake_install()
        root = self.plant_root()
        cache = root / "inside-cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(root), "--cache", str(cache)],
        )
        gs.assert_refusal(self, code, out, err, "graft-config")
        self.assertFalse((cache / "graph.json").exists())

    def test_ambient_graft_config_is_refused(self):
        self.plant_fake_install()
        root = self.plant_root()
        cfg = root / ".graft" / "config.json"
        plant_text(cfg, '{"onlyDirs":["src"]}\n')
        cache = self.home / "cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(root), "--cache", str(cache)],
        )
        gs.assert_refusal(self, code, out, err, "graft-config", phrases=["ambient"])

    def test_zero_ops_files_is_unavailable(self):
        self.plant_fake_install()
        root = self.plant_root({"README.md": "no ops\n"})
        cache = self.home / "cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(root), "--cache", str(cache)],
        )
        gs.assert_refusal(
            self, code, out, err, "graft-provider",
            phrases=["unavailable"],
        )

    def test_two_roots_do_not_share_a_cache(self):
        self.plant_fake_install()
        a = self.home / "wt-a"
        b = self.home / "wt-b"
        gs.plant_ops_root(a, {"ops/a.md": "a\n"})
        gs.plant_ops_root(b, {"ops/b.md": "b\n"})
        ca, cb = self.home / "cache-a", self.home / "cache-b"
        out_a = self.cli(["prepare", "--root", str(a), "--cache", str(ca)])
        out_b = self.cli(["prepare", "--root", str(b), "--cache", str(cb)])
        self.assertEqual(out_a[0], 0, out_a[2])
        self.assertEqual(out_b[0], 0, out_b[2])
        da = json.loads(Path(out_a[1].strip()).read_text(encoding="utf-8"))
        db = json.loads(Path(out_b[1].strip()).read_text(encoding="utf-8"))
        self.assertNotEqual(da["root"], db["root"])
        self.assertNotEqual(da["cache"], db["cache"])
        bind_a = json.loads((ca / "binding.json").read_text(encoding="utf-8"))
        bind_b = json.loads((cb / "binding.json").read_text(encoding="utf-8"))
        self.assertNotEqual(bind_a["source_sha256"], bind_b["source_sha256"])
        self.assertNotEqual(bind_a["head"], bind_b["head"])


class QueryServesAndRefuses(_Prepared):
    def test_query_prints_navigation_text(self):
        self.prepare()
        code, out, err = self.query()
        self.assertEqual(code, 0, err)
        self.assertTrue((out or "").strip())
        self.assertNotIn("instruction", (out or "").lower())
        transcript = self.fake.transcript_path(self.install)
        self.assertTrue(transcript.is_file())
        ops = {
            json.loads(line)["request"]["op"]
            for line in transcript.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("request")
        }
        self.assertIn("build", ops)
        self.assertIn("query", ops)

    def test_unknown_tool_is_the_nearest_invalid_of_an_admitted_name(self):
        self.prepare()
        code, out, err = self.query("graft_repo_map_all")
        gs.assert_refusal(self, code, out, err, "arguments", named=("graft_repo_map_all",))
        for name in ADMITTED:
            self.assertNotEqual(name, "graft_repo_map_all")

    def test_unknown_argument_key_is_refused(self):
        self.prepare()
        code, out, err = self.query("graft_repo_map", {"not_a_field": 1})
        gs.assert_refusal(self, code, out, err, "arguments", named=("not_a_field",))

    def test_path_escaping_ops_is_refused(self):
        self.prepare()
        code, out, err = self.query("graft_file_api", {"path": "../secret.py"})
        gs.assert_refusal(self, code, out, err, "arguments", phrases=["ops"])

    def test_iserror_result_exits_one_with_the_text(self):
        self.install = self.plant_fake_install(mode="query-iserror")
        self.root = self.plant_root()
        self.cache = self.home / "cache"
        prep = self.cli(
            ["prepare", "--root", str(self.root), "--cache", str(self.cache)],
        )
        self.assertEqual(prep[0], 0, prep[2])
        self.descriptor_path = Path(prep[1].strip())
        code, out, err = self.query()
        self.assertEqual(code, 1, err)
        self.assertTrue((out or "").strip())
        self.assertIn("synthetic isError", out)


class FreshnessAndCorruption(_Prepared):
    def test_edit_under_ops_makes_query_stale(self):
        self.prepare()
        target = self.root / "ops" / "lane.md"
        before = target.read_bytes()
        plant_text(target, target.read_text(encoding="utf-8") + "edited\n")
        self.assertNotEqual(target.read_bytes(), before)
        code, out, err = self.query()
        gs.assert_refusal(self, code, out, err, "graft-stale")
        self.assertEqual((out or "").strip(), "")

    def test_delete_under_ops_makes_query_stale(self):
        self.prepare()
        target = self.root / "ops" / "lane.md"
        self.assertTrue(target.is_file())
        target.unlink()
        code, out, err = self.query()
        gs.assert_refusal(self, code, out, err, "graft-stale")

    def test_query_does_not_repair_stale_cache(self):
        self.prepare()
        plant_text(self.root / "ops" / "lane.md", "stale-me\n")
        self.query()
        binding = json.loads((self.cache / "binding.json").read_text(encoding="utf-8"))
        code, out, err = self.query()
        gs.assert_refusal(self, code, out, err, "graft-stale")
        after = json.loads((self.cache / "binding.json").read_text(encoding="utf-8"))
        self.assertEqual(after, binding)

    def test_refresh_updates_binding_and_preserves_descriptor(self):
        desc = self.prepare()
        original = dict(desc)
        plant_text(self.root / "ops" / "lane.md", "refreshed\n")
        code, _out, err = self.cli(["refresh", "--context", str(self.descriptor_path)])
        self.assertEqual(code, 0, err)
        now = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        self.assertEqual(now, original)
        qcode, qout, qerr = self.query()
        self.assertEqual(qcode, 0, qerr)
        self.assertTrue((qout or "").strip())

    def test_read_role_refresh_is_graft_role(self):
        self.prepare()
        desc = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        desc["role"] = "read"
        plant_text(self.descriptor_path, json.dumps(desc) + "\n")
        code, out, err = self.cli(["refresh", "--context", str(self.descriptor_path)])
        gs.assert_refusal(self, code, out, err, "graft-role")

    def test_missing_descriptor_refuses_graft_context(self):
        self.prepare()
        missing = self.home / "no-such-context.json"
        self.assertFalse(missing.exists())
        code, out, err = self.cli(
            ["query", "--context", str(missing), "graft_repo_map", "{}"],
        )
        gs.assert_refusal(self, code, out, err, "graft-context")

    def test_tampered_descriptor_role_refuses_graft_context(self):
        self.prepare()
        desc = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        desc["role"] = "admin"
        plant_text(self.descriptor_path, json.dumps(desc) + "\n")
        code, out, err = self.query()
        gs.assert_refusal(self, code, out, err, "graft-context")

    def test_truncated_provider_response_is_graft_provider(self):
        self.install = self.plant_fake_install(mode="truncated")
        self.root = self.plant_root()
        self.cache = self.home / "cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(self.root), "--cache", str(self.cache)],
        )
        gs.assert_refusal(self, code, out, err, "graft-provider")
        self.assertEqual((out or "").strip(), "")
        self.assertTrue(
            self.fake.transcript_path(self.install).is_file(),
            "truncated JSON is a provider result, so the fake must have run",
        )

    def test_wrong_type_provider_result_is_graft_provider(self):
        self.install = self.plant_fake_install(mode="wrong-type")
        self.root = self.plant_root()
        self.cache = self.home / "cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(self.root), "--cache", str(self.cache)],
        )
        gs.assert_refusal(self, code, out, err, "graft-provider")
        self.assertTrue(
            self.fake.transcript_path(self.install).is_file(),
            "a typed-wrong result is a provider result, so the fake must have run",
        )

    def test_provider_error_object_is_graft_provider_not_empty_success(self):
        self.install = self.plant_fake_install(mode="provider-error")
        self.root = self.plant_root()
        self.cache = self.home / "cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(self.root), "--cache", str(self.cache)],
        )
        gs.assert_refusal(self, code, out, err, "graft-provider")
        self.assertEqual((out or "").strip(), "")
        self.assertTrue(
            self.fake.transcript_path(self.install).is_file(),
            "ok:false is a provider result, so the fake must have run",
        )

    def test_symlink_escaping_the_source_root_is_refused(self):
        self.plant_fake_install()
        root = self.plant_root()
        outside = self.home / "secret.md"
        plant_text(outside, "secret\n")
        link = root / "ops" / "leak.md"
        link.symlink_to(outside)
        self.assertTrue(link.is_symlink())
        cache = self.home / "cache"
        code, out, err = self.cli(
            ["prepare", "--root", str(root), "--cache", str(cache)],
        )
        gs.assert_refusal(self, code, out, err, "graft-context")


class WorkerCustodyAtThePythonSeam(gs.GraftCase, unittest.TestCase):
    def test_prepare_worker_returns_none_when_disabled(self):
        self.set_install("")
        job = self.home / "job"
        snap = job / "snapshot"
        snap.mkdir(parents=True)
        result = gs.call_public(self, "prepare_worker", job, snap, "plan")
        self.assertIsNone(result)
        self.assertFalse((job / "context" / "graft.json").exists())

    def test_author_tests_are_withheld_without_indexing(self):
        dest = self.plant_fake_install()
        job = self.home / "job"
        snap = gs.plant_ops_root(job / "snapshot")
        result = gs.call_public(self, "prepare_worker", job, snap, "author-tests")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("state"), "withheld")
        self.assertEqual(
            result["reason"],
            "implementation context withheld for test independence",
        )
        self.assertEqual(set(result), {"state", "reason"})
        self.assertFalse((job / "context" / "graft-cache").exists())
        self.assertFalse((job / "out" / "graft-cache").exists())
        self.assertFalse((job / "context" / "graft.json").exists())
        note = gs.call_public(self, "worker_note", result)
        self.assertEqual(note, result["reason"])
        self.assertNotIn(str(dest), note)
        self.assertNotIn(str(snap), note)
        transcript = self.fake.transcript_path(dest)
        self.assertFalse(transcript.exists(), "withheld jobs must not invoke the provider")

    def test_check_tests_are_withheld_the_same_way(self):
        self.plant_fake_install()
        job = self.home / "job"
        snap = gs.plant_ops_root(job / "snapshot")
        result = gs.call_public(self, "prepare_worker", job, snap, "check-tests")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("state"), "withheld")
        self.assertFalse((job / "context" / "graft.json").exists())

    def test_read_job_ready_metadata_uses_context_cache(self):
        self.plant_fake_install()
        job = self.home / "job"
        snap = gs.plant_ops_root(job / "snapshot")
        result = gs.call_public(self, "prepare_worker", job, snap, "plan")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("state"), "ready")
        self.assertEqual(result["descriptor"], "context/graft.json")
        desc_path = job / "context" / "graft.json"
        self.assertTrue(desc_path.is_file())
        self.assertEqual(result["sha256"], gs.sha256_file(desc_path))
        desc = json.loads(desc_path.read_text(encoding="utf-8"))
        self.assertEqual(desc["role"], "read")
        self.assertEqual(
            Path(desc["cache"]).resolve(),
            (job / "context" / "graft-cache").resolve(),
        )
        note = gs.call_public(self, "worker_note", result)
        self.assertIn("../context/graft.json", note)
        self.assertIn("python3 ops/devlane/harness/graft.py query", note)
        self.assertIn("ops", note)
        self.assertIn("navigation", note.lower())

    def test_write_job_ready_metadata_uses_out_cache(self):
        self.plant_fake_install()
        job = self.home / "job"
        snap = gs.plant_ops_root(job / "snapshot")
        result = gs.call_public(self, "prepare_worker", job, snap, "implement")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("state"), "ready")
        desc = json.loads((job / "context" / "graft.json").read_text(encoding="utf-8"))
        self.assertEqual(desc["role"], "write")
        self.assertEqual(
            Path(desc["cache"]).resolve(),
            (job / "out" / "graft-cache").resolve(),
        )

    def test_verify_worker_refuses_a_foreign_descriptor(self):
        self.plant_fake_install()
        job = self.home / "job"
        snap = gs.plant_ops_root(job / "snapshot")
        meta = gs.call_public(self, "prepare_worker", job, snap, "plan")
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("state"), "ready")
        other = gs.plant_ops_root(self.home / "other")
        desc_path = job / "context" / "graft.json"
        self.assertTrue(desc_path.is_file(), "ready metadata names a written descriptor")
        desc = json.loads(desc_path.read_text(encoding="utf-8"))
        desc["root"] = str(other)
        plant_text(desc_path, json.dumps(desc) + "\n")
        graft = gs.load_graft()
        self.assertTrue(callable(getattr(graft, "verify_worker", None)))
        try:
            graft.verify_worker(job, snap, meta)
        except Exception as exc:
            self.assertIn("graft-context", str(exc))
            return
        self.fail("verify_worker accepted a foreign descriptor root")

    def test_worker_note_for_disabled_is_empty(self):
        note = gs.call_public(self, "worker_note", None)
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
