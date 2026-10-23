"""Configuration, status, and identity — graft/CONTRACT.md installation
and Python/CLI surfaces. Reached through the bootstrap, not by importing
an absent module.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import support  # noqa: F401
import graft_support as gs
from ops.testkit import World  # noqa: F401 - GraftCase owns one


class DisabledAndMalformedConfig(gs.GraftCase, unittest.TestCase):
    def test_empty_install_makes_configuration_none(self):
        self.set_install("")
        result = gs.call_public(self, "configuration")
        self.assertIsNone(result)

    def test_absent_key_is_disabled_not_a_refusal(self):
        gs.write_conf(self.conf, omit=("GRAFT_INSTALL",))
        os.environ.pop("GRAFT_INSTALL", None)
        result = gs.call_public(self, "configuration")
        self.assertIsNone(result)

    def test_status_prints_disabled_json_and_creates_nothing(self):
        self.set_install("")
        before = {p.relative_to(self.home) for p in self.home.rglob("*") if p.is_file()}
        code, out, err = self.cli(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual((err or "").strip(), "")
        body = json.loads(out)
        self.assertEqual(body["state"], "disabled")
        after = {p.relative_to(self.home) for p in self.home.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_relative_install_refuses_graft_config(self):
        self.set_install("opt/graft")
        code, out, err = self.cli(["status"])
        gs.assert_refusal(
            self, code, out, err, "graft-config",
            phrases=["relative"], named=("opt/graft",),
        )

    def test_tilde_home_install_is_not_an_operator_home_fallback(self):
        self.set_install("~/graft")
        code, out, err = self.cli(["status"])
        gs.assert_refusal(
            self, code, out, err, "graft-config",
            named=("~/graft",),
        )

    def test_bare_name_is_not_a_path_or_npx_fallback(self):
        self.set_install("graft")
        code, out, err = self.cli(["status"])
        gs.assert_refusal(
            self, code, out, err, "graft-config",
            named=("graft",),
        )

    def test_truthy_one_is_malformed_not_disabled(self):
        self.set_install("1")
        code, out, err = self.cli(["status"])
        gs.assert_refusal(
            self, code, out, err, "graft-config",
            named=("1",),
        )

    def test_missing_absolute_dir_refuses_graft_install(self):
        missing = self.home / "no-such-install"
        self.assertFalse(missing.exists())
        self.set_install(str(missing))
        code, out, err = self.cli(["status"])
        gs.assert_refusal(
            self, code, out, err, "graft-install",
            named=(str(missing),),
        )

    def test_absolute_valid_install_makes_configuration_that_path(self):
        dest = self.plant_fake_install()
        result = gs.call_public(self, "configuration")
        self.assertIsNotNone(
            result,
            "an absolute enabled install returns that path, not None",
        )
        self.assertEqual(Path(result).resolve(), dest.resolve())

    def test_status_ready_when_receipt_matches_the_tracked_pin(self):
        self.plant_fake_install()
        code, out, err = self.cli(["status"])
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body.get("pin"), self.pin)


class WriteVerbsNeedIdentityReadsDoNot(gs.GraftCase, unittest.TestCase):
    def test_install_without_identity_refuses_and_does_not_create_the_dest(self):
        dest = self.home / "new-install"
        code, out, err = self.cli(
            ["install", "--destination", str(dest)], identity=False,
        )
        gs.assert_refusal(self, code, out, err, "identity")
        self.assertFalse(dest.exists())

    def test_prepare_without_identity_refuses(self):
        root = self.plant_root()
        cache = self.home / "cache"
        self.plant_fake_install()
        code, out, err = self.cli(
            ["prepare", "--root", str(root), "--cache", str(cache)],
            identity=False,
        )
        gs.assert_refusal(self, code, out, err, "identity")
        self.assertFalse(cache.exists())

    def test_status_does_not_need_identity(self):
        os.environ.pop("WF_AGENT", None)
        code, out, err = self.cli(["status"], identity=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["state"], "disabled")

    def test_query_against_an_unchanged_context_does_not_need_identity(self):
        # Identity is asserted absent; the red is that query is not an
        # identity refusal once a context exists. Bootstrap still fails
        # this on the missing prepare/query behaviour.
        root = self.plant_root()
        cache = self.home / "cache"
        self.plant_fake_install()
        prep = self.cli(["prepare", "--root", str(root), "--cache", str(cache)])
        self.assertEqual(prep[0], 0, prep[2])
        descriptor = Path(prep[1].strip())
        self.assertTrue(descriptor.is_file())
        code, out, err = self.cli(
            ["query", "--context", str(descriptor), "graft_repo_map", "{}"],
            identity=False,
        )
        self.assertNotIn("identity", (err or "").lower())
        self.assertEqual(code, 0, err)
        self.assertTrue((out or "").strip())


if __name__ == "__main__":
    unittest.main()
