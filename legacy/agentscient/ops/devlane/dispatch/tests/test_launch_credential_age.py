"""LD-18 (2026-09-11): an aged grok credential is refused at the launcher,
on the wire, before any child starts. The isolation module raises
NotIsolated; the launcher must turn that into a refusal, not a traceback
(NotIsolated subclasses Exception, and the launcher's catch listed only
the standard classes)."""

import datetime
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import launch_support as ls

from ops.devlane.hooks import profiles
from ops.testkit import World


def _stamp(hours_ago):
    at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


class AnAgedGrokCredentialIsRefusedBeforeTheMint(ls._TempLaunch):
    def plant_credential(self, hours_ago):
        auth = self.home / ".grok" / "auth.json"
        self.assertTrue(auth.is_file(), "plant: the fixture's credential is present")
        self.world.plant_text(auth, json.dumps({"https://auth.x.ai::client": {
            "key": "k", "refresh_token": "r", "create_time": _stamp(hours_ago)}}) + "\n")
        self.assertIn("create_time", auth.read_text(encoding="utf-8"), "plant: stamp written")

    def test_a_credential_past_its_lifetime_refuses_naming_the_login(self):
        self.plant_credential(31)
        code, out, err = self.dispatch(self.argv_for(job="plan", harness="grok", stage="plan"))
        self.assertEqual(code, ls.REFUSAL_EXIT, self.combined(out, err))
        text = self.combined(out, err)
        self.assertIn("isolation", text)
        self.assertIn("grok login --device-code", text)
        self.assertNotIn("Traceback", text)
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])

    def test_a_fresh_credential_launches(self):
        self.plant_credential(1)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["harness"]["name"], "grok")

    def test_a_renewal_by_the_worker_reaches_the_host_copy(self):
        # LD-41 (2026-09-15): the CLI renews by renaming a new file over the
        # link in the job home; the host copy kept the login's token and
        # aged out while every renewal sat in a job home.
        self.plant_credential(20)
        renewed = json.dumps({"https://auth.x.ai::client": {
            "key": "k2", "refresh_token": "renewed", "create_time": _stamp(0)}}) + "\n"
        os.environ["TASK_LAUNCH_RENEW_AUTH"] = renewed
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        host = self.home / ".grok" / "auth.json"
        self.assertIn('"renewed"', host.read_text(encoding="utf-8"),
                      "the host copy carries the worker's renewal")
        self.assertEqual(rec["attempts"][-1].get("credential"),
                         [{"file": "auth.json", "outcome": "carried"}])
        job_auth = self.jobs_root / rec["id"] / "home" / "grok" / "auth.json"
        self.assertTrue(job_auth.is_symlink(), "the job home holds the link again")

    def check_post_child_binding_fault(self, *, missing):
        self.plant_credential(20)
        host = self.home / ".grok/auth.json"
        original = host.read_bytes()
        os.environ["TASK_LAUNCH_RENEW_AUTH"] = json.dumps({"https://auth.x.ai::client": {
            "key": "k2", "refresh_token": "renewed", "create_time": _stamp(0)}})
        carry = self.load_launch()._carry_back_credentials

        def after_child(harness, home, **kwargs):
            binding_path = Path(home).parents[1] / "enforcement/binding.json"
            self.assertTrue(binding_path.is_file())
            if missing:
                binding_path.unlink()
            else:
                binding_path.write_text("[]")
            return carry(harness, home, **kwargs)

        with patch.object(self.launch, "_carry_back_credentials", side_effect=after_child):
            rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["attempts"][-1]["exit"], 0)
        self.assertIn("unresolved", rec["attempts"][-1]["credential"][0])
        self.assertEqual(host.read_bytes(), original)
        job_auth = self.jobs_root / rec["id"] / "home/grok/auth.json"
        self.assertFalse(job_auth.is_symlink())
        self.assertIn("renewed", job_auth.read_text())

    def test_missing_binding_after_worker_exit_keeps_renewal_unresolved(self):
        self.check_post_child_binding_fault(missing=True)

    def test_changed_binding_after_worker_exit_keeps_renewal_unresolved(self):
        self.check_post_child_binding_fault(missing=False)

    def test_replaced_worker_home_retains_both_artifacts_without_host_effect(self):
        self.plant_credential(20)
        host = self.home / ".grok/auth.json"
        original = host.read_bytes()
        os.environ["TASK_LAUNCH_RENEW_AUTH"] = json.dumps({"https://auth.x.ai::client": {
            "key": "k2", "refresh_token": "renewed", "create_time": _stamp(0)}})
        carry = self.load_launch()._carry_back_credentials

        def after_child(harness, home, **kwargs):
            home = Path(home)
            saved = home.with_name(home.name + ".before")
            home.rename(saved)
            shutil.copytree(saved, home, symlinks=True)
            (home / "auth.json").write_text(json.dumps({"https://auth.x.ai::client": {
                "key": "wrong", "refresh_token": "substituted", "create_time": _stamp(0)}}))
            return carry(harness, home, **kwargs)

        with patch.object(self.launch, "_carry_back_credentials", side_effect=after_child):
            rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["attempts"][-1]["exit"], 0)
        self.assertIn("unresolved", rec["attempts"][-1]["credential"][0])
        self.assertEqual(host.read_bytes(), original)
        job_home = self.jobs_root / rec["id"] / "home/grok"
        self.assertIn("substituted", (job_home / "auth.json").read_text())
        self.assertIn("renewed", (job_home.with_name("grok.before") / "auth.json").read_text())


class WorkerCustodySource(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="worker-custody-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.launch = ls.require_module(self, "launch")
        self.home = self.world.root / "native-home"
        self.home.mkdir()
        self.host = self.world.home / ".codex/auth.json"
        self.host.parent.mkdir()
        self.host.write_text('{"synthetic":"original"}')
        os.utime(self.host, ns=(1, 1))
        (self.home / "auth.json").write_text('{"synthetic":"renewed"}')

    def test_missing_custody_context_has_no_host_effect(self):
        result = self.launch._carry_back_credentials("codex", self.home)
        self.assertIn("unresolved", result[0])
        self.assertEqual(json.loads(self.host.read_text()), {"synthetic": "original"})

    def test_bound_source_faults_refuse_before_import_and_valid_renewal_works(self):
        source = self.world.root / "source"
        for original in profiles.source_pins(ls.REPO):
            target = source / Path(original).relative_to(ls.REPO)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        owner = source / "ops/devlane/harness/isolation.py"
        pristine = owner.read_bytes()
        canary = self.world.root / "unvalidated-import"
        profiles.isolation_owner.cache_clear()
        self.addCleanup(profiles.isolation_owner.cache_clear)
        with patch.object(profiles, "__file__", str(source / "ops/devlane/hooks/profiles.py")):
            binding = profiles.generate("codex", self.world.root / "profile", source=source,
                                        cwd=self.world.cwd, home=self.home, mode="worker",
                                        session="issued", executable="/usr/bin/true")
            custody = profiles.capture_custody(binding)
            loaded = profiles.isolation_owner(binding["pins"][str(owner)])
            for fault in ("changed", "missing", "binding", "home", "harness", "capture", "error"):
                with self.subTest(fault=fault):
                    if fault == "changed":
                        owner.write_text(owner.read_text() + f"\nPath({str(canary)!r}).touch()\n")
                    elif fault == "missing":
                        owner.unlink()
                    context = {} if fault == "binding" else binding
                    with (patch.object(profiles, "isolation_owner", side_effect=AssertionError("late custody import")) as loader,
                          patch.object(loaded, "carry_back", side_effect=RuntimeError("custody unavailable")) as effect):
                        result = self.launch._carry_back_credentials(
                            "grok" if fault == "harness" else "codex",
                            self.world.home if fault == "home" else self.home,
                            profile=profiles, binding=context, custody=None if fault == "capture" else custody)
                    self.assertIn("unresolved", result[0])
                    loader.assert_not_called()
                    if fault != "error":
                        effect.assert_not_called()
                    self.assertEqual(json.loads(self.host.read_text()), {"synthetic": "original"})
                    self.assertFalse(canary.exists())
                    owner.write_bytes(pristine)
            result = self.launch._carry_back_credentials("codex", self.home, profile=profiles, binding=binding, custody=custody)
            self.assertEqual(result, [{"file": "auth.json", "outcome": "carried"}])
            self.assertEqual(json.loads(self.host.read_text()), {"synthetic": "renewed"})
            self.assertTrue((self.home / "auth.json").is_symlink())
