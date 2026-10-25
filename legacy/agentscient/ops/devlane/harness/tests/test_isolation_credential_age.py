"""LD-18 (2026-09-11): a grok credential older than its refresh lifetime.

The CLI refreshes the token inside each isolated job home and writes the
new refresh token there; the host file keeps the one from the login, and
that one aged out about 30 hours after `create_time` (a refresh from it
was accepted at 29.4 h, records 3ab0c7; two dispatches died `Not signed
in` at 30.1 h, records dddd49 and 70b92e). The launcher now refuses such
a credential before the mint, naming the login command, instead of
running a worker that fails in a way that looks like a model refusal.
"""

import datetime
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from ops.testkit import World

HARNESS = Path(__file__).resolve().parents[1]


def _isolation():
    spec = importlib.util.spec_from_file_location(
        "isolation_under_test", HARNESS / "isolation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stamp(hours_ago):
    at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"  # the CLI writes nanoseconds


class _CredentialFixture(unittest.TestCase):
    def setUp(self):
        self.iso = _isolation()
        self.world = World(prefix="cred-age-")
        self.addCleanup(self.world.close)
        self.tmp = self.world.root
        self.home = self.world.home
        self.env = self.world.env()

    def plant(self, harness, body):
        path = self.home / f".{harness}" / "auth.json"
        return self.world.plant_text(path, body)

    def credential(self, hours_ago, refresh="r"):
        return json.dumps({"https://auth.x.ai::client": {
            "key": "k", "refresh_token": refresh, "create_time": _stamp(hours_ago)}})

    def build(self, harness, name):
        return Path(self.iso.build_home(harness, self.tmp / name, env=self.env))


class ACredentialPastItsRefreshLifetimeIsRefused(_CredentialFixture):
    def test_a_grok_credential_older_than_the_lifetime_is_refused_naming_the_login(self):
        self.plant("grok", self.credential(31))
        with self.assertRaises(self.iso.NotIsolated) as caught:
            self.build("grok", "old")
        text = str(caught.exception)
        self.assertIn("create_time", text)
        self.assertIn("grok login --device-code", text)
        self.assertFalse((self.tmp / "old" / "auth.json").exists(),
                         "no credential link is left in a refused home")

    def test_a_fresh_grok_credential_builds_the_home(self):
        self.plant("grok", self.credential(1))
        home = self.build("grok", "fresh")
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json"])

    def test_a_credential_without_create_time_is_not_aged(self):
        # Test fixtures across the lane plant "{}" for grok; the check reads
        # only what the CLI wrote and has no opinion on a file without it.
        self.plant("grok", "{}\n")
        home = self.build("grok", "blank")
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json"])

    def test_the_rule_is_declared_per_harness_and_codex_carries_none(self):
        self.assertIn("credential_age", self.iso.HARNESSES["grok"])
        self.assertNotIn("credential_age", self.iso.HARNESSES["codex"])
        self.plant("codex", self.credential(31))
        home = self.build("codex", "codex")
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json"])


class ARenewedCredentialIsCarriedBackToTheHost(_CredentialFixture):
    """LD-41 (2026-09-15): the CLI renews by renaming a new file over the
    symlink build_home planted, so the renewal (a new refresh token and a
    new create_time) stayed in the job home and the host copy aged out.
    After an attempt the launcher carries a renewed regular file back."""

    def renew(self, home, hours_ago, refresh):
        job = home / "auth.json"
        self.assertTrue(job.is_symlink(), "plant: the home holds the link")
        job.unlink()
        self.world.plant_text(job, self.credential(hours_ago, refresh) + "\n")
        self.assertFalse(job.is_symlink(), "plant: the renewal is a regular file")
        return job

    def test_a_newer_regular_file_in_the_job_home_replaces_the_host_copy(self):
        host = self.plant("grok", self.credential(20, "r0"))
        host.chmod(0o600)
        home = self.build("grok", "job")
        job = self.renew(home, 1, "r1")
        readings = self.iso.carry_back("grok", home, env=self.env)
        self.assertEqual(readings, [{"file": "auth.json", "outcome": "carried"}])
        self.assertIn('"r1"', host.read_text(encoding="utf-8"))
        self.assertEqual(host.stat().st_mode & 0o777, 0o600)
        self.assertTrue(job.is_symlink(), "the job home holds the link again, no copy outlives the run")
        self.assertEqual(job.resolve(), host.resolve())
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json"],
                         "no temporary file is left beside the credential")

    def test_an_untouched_link_carries_nothing(self):
        host = self.plant("grok", self.credential(20, "r0"))
        home = self.build("grok", "job")
        readings = self.iso.carry_back("grok", home, env=self.env)
        self.assertEqual(readings, [{"file": "auth.json", "outcome": "linked"}])
        self.assertIn('"r0"', host.read_text(encoding="utf-8"))

    def test_an_older_regular_file_does_not_replace_the_host_copy(self):
        host = self.plant("grok", self.credential(20, "r0"))
        home = self.build("grok", "job")
        self.renew(home, 30, "stale")
        readings = self.iso.carry_back("grok", home, env=self.env)
        self.assertEqual(readings, [{"file": "auth.json", "outcome": "older"}])
        self.assertIn('"r0"', host.read_text(encoding="utf-8"))

    def test_a_carried_credential_passes_the_age_rule_again(self):
        self.plant("grok", self.credential(23, "r0"))
        home = self.build("grok", "first")
        self.renew(home, 1, "r1")
        self.iso.carry_back("grok", home, env=self.env)
        second = self.build("grok", "second")
        self.assertEqual(sorted(p.name for p in second.iterdir()), ["auth.json"])

    def test_a_harness_without_a_home_carries_nothing(self):
        self.assertEqual(self.iso.carry_back("claude", self.tmp / "none", env=self.env), [])


if __name__ == "__main__":
    unittest.main()
