"""keepalive: holding a harness credential alive between logins.

Authored from ops/conductor/keepalive-lever.md alone; keepalive.py was
not read. Origin: the owner, 2026-09-16, on a third consecutive morning
of logging grok in. The fixture set is a planted grok that renews
(the fault the lever must catch moving) and one that does not (the
clean neighbour it must report as unchanged).
"""

from __future__ import annotations

import datetime
import json
import sys
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, Tools, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
LEVER = REPO / "ops" / "conductor" / "tools" / "keepalive.py"
TIMEOUT = 60
AGENT = "Keepalive Referee <noreply@anthropic.com>"

RENEWING = r"""#!__PYTHON__
import json, os, pathlib, sys, datetime
log = pathlib.Path(os.environ["FAKE_LOG"])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
home = os.environ.get("GROK_HOME") or os.environ["HOME"]
stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
    timespec="microseconds").replace("+00:00", "Z")
target = pathlib.Path(home) / "auth.json"
tmp = target.with_suffix(".tmp")
tmp.write_text(json.dumps({"default": {"create_time": stamp,
                                       "refresh_token": "renewed"}}))
os.replace(tmp, target)
print("ok")
"""

QUIET = r"""#!__PYTHON__
import json, os, pathlib, sys
log = pathlib.Path(os.environ["FAKE_LOG"])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
print("ok")
"""


def _stamp(hours_ago):
    when = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours_ago))
    return when.isoformat(timespec="microseconds").replace("+00:00", "Z")


class _Keepalive(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="keepalive-")
        self.addCleanup(self.world.close)
        self.tools = Tools(self.world)
        self.log = self.world.root / "fakes" / "grok.calls.jsonl"

    def plant_credential(self, hours_ago):
        """The host copy the lever reads, aged as the case declares."""
        path = self.world.home / ".grok" / "auth.json"
        return self.world.plant_text(
            path,
            json.dumps({"default": {"create_time": _stamp(hours_ago),
                                    "refresh_token": "login"}}, indent=1) + "\n",
            recognisable="create_time",
        )

    def plant_grok(self, script):
        """The shebang names this interpreter by path. `/usr/bin/env
        python3` would need python3 on the world's PATH, which is the
        world's bin alone — measured 2026-09-16: the first run of these
        rows exited 127, the loader failing to resolve the shebang, and
        the lever read `unchanged` from a command that never ran."""
        assert "__PYTHON__" in script, "the fake must name its interpreter"
        return self.tools.plant_executable(
            self.world.bin / "grok",
            script.replace("__PYTHON__", sys.executable, 1))

    def created(self):
        path = self.world.home / ".grok" / "auth.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["default"]["create_time"]

    def calls(self):
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def wire(self, row_id, argv, cls, named=()):
        """One refusal row, addressed by id: the kit asserts the wire."""
        case = RefusalCase(id=row_id, argv=tuple(argv), cls=cls,
                           named=tuple(named),
                           origin="ops/conductor/keepalive-lever.md §Refusals")
        return refusal_wire(case, self.run_lever(*argv))

    def run_lever(self, *args):
        return run(
            [sys.executable, str(LEVER), *args],
            cwd=REPO,
            # PATH is the world's bin alone. The hermetic environment
            # still carries the host's PATH, so without this the real
            # grok on this host answers and the planted fake never runs
            # — measured 2026-09-16, the first run of these rows
            # invoked /home/.../.local/bin/grok.
            env=self.world.env({"WF_AGENT": AGENT, "FAKE_LOG": str(self.log),
                                "PATH": str(self.world.bin)}),
            timeout=TIMEOUT,
        )


class KeepaliveRefuses(_Keepalive):
    """A keepalive cannot log in, so every case it cannot hold alive
    refuses on the wire rather than reporting a success."""

    def test_an_unknown_harness_refuses_class_harness(self):
        self.plant_grok(QUIET)
        self.wire("keepalive-unknown-harness", ("nosuch",), "harness")

    def test_a_harness_with_no_credential_rule_refuses_class_harness(self):
        """codex is isolated and carries auth.json, but declares no
        credential_age rule: there is no lifetime to hold alive."""
        self.plant_grok(QUIET)
        self.wire("keepalive-no-credential-rule", ("codex",), "harness")

    def test_an_absent_credential_refuses_class_credential(self):
        self.plant_grok(QUIET)
        self.wire("keepalive-credential-absent", ("grok",), "credential")

    def test_a_credential_past_max_hours_refuses_and_names_the_remedy(self):
        self.plant_credential(30)
        self.plant_grok(RENEWING)
        self.wire("keepalive-credential-aged", ("grok",), "credential",
                  named=("grok login",))
        self.assertEqual(self.calls(), [], "a refused keepalive invokes nothing")

    def test_no_executable_refuses_class_keepalive_error(self):
        self.plant_credential(2)
        self.wire("keepalive-no-executable", ("grok",), "keepalive-error")


class KeepaliveReports(_Keepalive):
    """The reading comes from the credential, never from the exit code."""

    def test_a_renewal_reads_renewed_with_both_ages(self):
        self.plant_credential(20)
        before = self.created()
        self.plant_grok(RENEWING)
        result = self.run_lever("grok")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("keepalive grok:", result.stdout)
        self.assertIn("renewed", result.stdout, result.stderr)
        self.assertIn("before", result.stdout)
        self.assertIn("after", result.stdout)
        self.assertNotEqual(self.created(), before,
                            "the host copy did not take the renewal")
        self.assertEqual(len(self.calls()), 1, self.calls())

    def test_a_run_that_does_not_renew_reads_unchanged(self):
        """The clean neighbour: the command ran and the credential did
        not move. That is an answer, not a failure."""
        self.plant_credential(20)
        before = self.created()
        self.plant_grok(QUIET)
        result = self.run_lever("grok")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("unchanged", result.stdout)
        self.assertNotIn("renewed", result.stdout)
        self.assertEqual(self.created(), before)

    def test_dry_run_reads_the_age_and_invokes_nothing(self):
        self.plant_credential(20)
        before = self.created()
        self.plant_grok(RENEWING)
        result = self.run_lever("grok", "--dry-run")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("before", result.stdout)
        self.assertEqual(self.calls(), [], "--dry-run invoked the harness")
        self.assertEqual(self.created(), before)

    def test_the_operator_home_is_never_the_source(self):
        """The lever reads the credential the environment names, so a
        run under a planted home never touches the operator's own."""
        self.plant_credential(20)
        self.plant_grok(QUIET)
        result = self.run_lever("grok")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn(str(self.world.home), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
