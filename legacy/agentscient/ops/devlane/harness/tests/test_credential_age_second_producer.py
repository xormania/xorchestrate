"""LD-18 regression from a second producer: grok credential_age.

The grok isolation entry carries credential_age (file auth.json, field
create_time, max_hours 24, remedy `grok login --device-code`).
check_credential_age(harness, spec, name, src) is loaded with
importlib. The launcher preflight catches NotIsolated before the mint.

Named command:
python3 -m unittest discover -s ops/devlane/harness/tests -p test_credential_age_second_producer.py
"""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import json
import unittest
from pathlib import Path

from ops.testkit import World

HARNESS = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "isolation_ld18_second_producer", HARNESS / "isolation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp(hours_ago, *, fraction=None):
    """UTC create_time older (or younger) than now by hours_ago."""
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=hours_ago)
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if fraction is not None:
        text += f".{fraction}"
    return text + "Z"


def _entries(*stamps):
    """A grok auth.json object: one keyed entry per stamp (None omits the field)."""
    body = {}
    for index, stamp in enumerate(stamps):
        entry = {"key": f"k{index}", "refresh_token": f"r{index}"}
        if stamp is not None:
            entry["create_time"] = stamp
        body[f"https://auth.x.ai::entry-{index}"] = entry
    return body


class GrokCredentialAgeSecondProducer(unittest.TestCase):
    def setUp(self):
        self.iso = load()
        self.rule = self.iso.HARNESSES["grok"]["credential_age"]
        self.world = World(prefix="ld18-")
        self.addCleanup(self.world.close)
        self.base = self.world.root

    def _write_auth(self, path, body):
        if isinstance(body, bytes):
            self.world.plant_bytes(path, body)
            planted = path.read_bytes()
            if planted != body:
                raise AssertionError("auth plant did not land")
            return path
        text = body if isinstance(body, str) else json.dumps(body)
        self.world.plant_text(path, text)
        planted = path.read_text(encoding="utf-8")
        if planted != text:
            raise AssertionError("auth plant did not land")
        if isinstance(body, dict):
            for entry in body.values():
                if isinstance(entry, dict) and "create_time" in entry:
                    self.assertIn(entry["create_time"], planted)
        return path

    def _auth_path(self, harness="grok"):
        return self.base / "operator" / f".{harness}" / "auth.json"

    def _check(self, src, harness="grok"):
        spec = self.iso.HARNESSES[harness]
        rule = spec.get("credential_age") or {}
        leaf = rule.get("file", "auth.json")
        return self.iso.check_credential_age(harness, spec, leaf, src)

    def _aged_hours(self):
        return self.rule["max_hours"] + 24

    def _young_hours(self):
        return 1

    def test_an_aged_grok_auth_json_raises_naming_the_remedy_and_field(self):
        src = self._write_auth(
            self._auth_path(), _entries(_stamp(self._aged_hours())))
        with self.assertRaises(self.iso.NotIsolated) as caught:
            self._check(src)
        text = str(caught.exception)
        self.assertIn(self.rule["remedy"], text)
        self.assertIn(self.rule["field"], text)

    def test_a_young_grok_auth_json_passes_silently(self):
        src = self._write_auth(
            self._auth_path(), _entries(_stamp(self._young_hours())))
        try:
            self._check(src)
        except self.iso.NotIsolated as exc:
            self.fail(f"a credential younger than max_hours was aged: {exc}")

    def test_several_entries_pass_when_the_newest_stamp_is_young(self):
        # Newest-by-time is young even when an older stamp sits last in
        # the file, and even when it sits first. Either order would
        # refuse if any-old or last-in-file were the rule.
        old = _stamp(self._aged_hours())
        young = _stamp(self._young_hours())
        for body in (_entries(old, young), _entries(young, old)):
            with self.subTest(order=list(body)):
                src = self._write_auth(self._auth_path(), body)
                try:
                    self._check(src)
                except self.iso.NotIsolated as exc:
                    self.fail(
                        "newest stamp is young; several entries were aged: "
                        f"{exc}")

    def test_several_entries_raise_when_the_newest_stamp_is_old(self):
        older = _stamp(self._aged_hours() + 24)
        newest = _stamp(self._aged_hours())
        src = self._write_auth(self._auth_path(), _entries(older, newest))
        with self.assertRaises(self.iso.NotIsolated):
            self._check(src)

    def test_an_entry_without_create_time_is_not_aged(self):
        src = self._write_auth(self._auth_path(), _entries(None))
        self.assertNotIn("create_time", src.read_text(encoding="utf-8"))
        try:
            self._check(src)
        except self.iso.NotIsolated as exc:
            self.fail(f"a file with no create_time was aged: {exc}")

    def test_an_empty_object_is_not_aged(self):
        src = self._write_auth(self._auth_path(), {})
        self.assertEqual("{}", src.read_text(encoding="utf-8"))
        try:
            self._check(src)
        except self.iso.NotIsolated as exc:
            self.fail(f"an empty object was aged: {exc}")

    def test_unparseable_bytes_are_not_aged(self):
        src = self._write_auth(self._auth_path(), b"\xff\xfe not json {")
        try:
            self._check(src)
        except self.iso.NotIsolated as exc:
            self.fail(f"unparseable bytes were aged: {exc}")

    def test_a_nanosecond_stamp_with_trailing_z_parses(self):
        # Same shape as the measured host file: nine fractional digits
        # and a trailing Z. Aged so a failed parse (ignored stamp) would
        # not raise, and a successful parse must.
        stamp = _stamp(self._aged_hours(), fraction="673826295")
        self.assertTrue(stamp.endswith("Z"), stamp)
        self.assertIn(".673826295Z", stamp)
        src = self._write_auth(self._auth_path(), _entries(stamp))
        with self.assertRaises(self.iso.NotIsolated):
            self._check(src)

    def test_a_malformed_stamp_is_ignored(self):
        src = self._write_auth(
            self._auth_path(), _entries("not-a-timestamp"))
        self.assertIn("not-a-timestamp", src.read_text(encoding="utf-8"))
        try:
            self._check(src)
        except self.iso.NotIsolated as exc:
            self.fail(f"a malformed stamp was aged: {exc}")

    def test_build_home_raises_for_an_aged_grok_file(self):
        operator = self.base / "operator"
        src = self._write_auth(
            operator / ".grok" / "auth.json",
            _entries(_stamp(self._aged_hours())))
        self.assertTrue(src.is_file())
        root = self.base / "minimal-home"
        root.mkdir()
        with self.assertRaises(self.iso.NotIsolated) as caught:
            self.iso.build_home("grok", root, env={"HOME": str(operator)})
        text = str(caught.exception)
        self.assertIn(self.rule["remedy"], text)
        self.assertIn(self.rule["field"], text)

    def test_build_home_leaves_no_auth_json_link_for_an_aged_grok_file(self):
        operator = self.base / "operator"
        src = self._write_auth(
            operator / ".grok" / "auth.json",
            _entries(_stamp(self._aged_hours())))
        self.assertTrue(src.is_file())
        root = self.base / "minimal-home"
        root.mkdir()
        with contextlib.suppress(self.iso.NotIsolated):
            self.iso.build_home("grok", root, env={"HOME": str(operator)})
        auth = root / "auth.json"
        self.assertFalse(auth.exists(), f"aged build left {auth}")
        self.assertFalse(auth.is_symlink(), f"aged build left a link at {auth}")

    def test_codex_carries_no_credential_age_and_an_aged_file_builds_a_home(self):
        self.assertNotIn("credential_age", self.iso.HARNESSES["codex"])
        operator = self.base / "operator"
        src = self._write_auth(
            operator / ".codex" / "auth.json",
            _entries(_stamp(self._aged_hours())))
        root = self.base / "minimal-home"
        root.mkdir()
        home = Path(self.iso.build_home(
            "codex", root, env={"HOME": str(operator)}))
        link = home / "auth.json"
        self.assertTrue(link.exists(), "codex home was not built")
        self.assertTrue(link.is_symlink())
        self.assertEqual(src.resolve(), link.resolve())


if __name__ == "__main__":
    unittest.main()
