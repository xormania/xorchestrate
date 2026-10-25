"""A reviewer's practical label on the path a harness's envelope takes.

Written from ops/devlane/task/CONTRACT.md §The practical label, by a
producer other than the author of envelope.py's label (58ad7d6c):
"`practical` is `yes` or `no` ... and `failing` is that invocation as a
command when `practical` is `yes` and `null` when it is `no`", "both or
neither", and "a finding without it has the four fields alone, as every
record before it does".

A claude dispatch runs under `--json-schema ENVELOPE_SCHEMA` and its
object reaches the record through launch._envelope; these rows put a
claude result through that function, the invocation every claude review
makes, and read the envelope the record would carry.
"""

from __future__ import annotations

import json
import unittest

import launch_support as ls

from ops.testkit import World

ORIGIN = "second producer on #273, 58ad7d6c"
JOB = "adversarial-review"
REF = "0123456789abcdef0123456789abcdef01234567"

# An envelope as the code before the label wrote it: four-field findings,
# written out here, not built by today's envelope.py.
OLD_ENVELOPE = """{"job": "adversarial-review", "status": "ok", "verdict": "changes",
 "counts": {"p1": 0, "p2": 1, "p3": 1, "opinions": 1},
 "findings": [
  {"severity": "p2", "where": "ops/x.py", "claim": "an old finding", "reproduce": "python3 x"},
  {"severity": "p3", "where": "ops/y.py", "claim": "an old opinion", "reproduce": null}],
 "artifacts": {}, "spend": {"harness": "claude", "total": 0, "out": 0, "runs": 1},
 "stamp": {"ref": "0123456", "started": null, "ended": null}, "note": "before the label"}"""


def labelled(**label):
    finding = {"severity": "p2", "where": "ops/x.py", "claim": "a labelled finding",
               "reproduce": "python3 x"}
    finding.update(label)
    return finding


class ThePracticalLabelOnTheClaudePath(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="practical-label-")
        self.addCleanup(self.world.close)
        self.launch = ls.require_module(self, "launch")

    def claude(self, envelope):
        """launch._envelope over one claude result carrying `envelope`."""
        wrapper = {"type": "result", "subtype": "success", "is_error": False,
                   "structured_output": envelope}
        raw = (json.dumps(wrapper) + "\n").encode("utf-8")
        return self.launch._envelope(raw, JOB, REF, harness="claude")

    def with_finding(self, finding):
        envelope = json.loads(OLD_ENVELOPE)
        envelope["findings"] = [finding]
        envelope["counts"] = {"p1": 0, "p2": 1, "p3": 0, "opinions": 0}
        return envelope

    def test_an_old_envelope_without_the_label_still_validates(self):
        with self.subTest(id="old-envelope-validates", origin=ORIGIN):
            self.launch.ENVELOPE.validate(json.loads(OLD_ENVELOPE))
        with self.subTest(id="old-envelope-claude-ok", origin=ORIGIN):
            data = self.claude(json.loads(OLD_ENVELOPE))
            self.assertEqual(data["status"], "ok", data.get("note"))
            self.assertEqual([sorted(f) for f in data["findings"]],
                             [["claim", "reproduce", "severity", "where"]] * 2)

    def test_a_whole_label_reaches_the_record(self):
        cases = (
            ("label-yes", labelled(practical="yes", failing="ops/bin/dispatch.sh review --for A-1")),
            ("label-no", labelled(practical="no", failing=None)),
        )
        for case_id, finding in cases:
            with self.subTest(id=case_id, origin=ORIGIN):
                data = self.claude(self.with_finding(finding))
                self.assertEqual(data["status"], "ok", data.get("note"))
                self.assertEqual(data["findings"], [finding])

    def test_half_a_label_never_reaches_the_record_as_ok(self):
        """Both or neither: yes names its failing invocation, and failing
        never comes without practical. The schema claude is bound to types
        each field alone, so these are shapes a claude review can emit."""
        cases = (
            ("yes-without-failing", labelled(practical="yes", failing=None)),
            ("failing-without-practical", labelled(failing="ops/bin/dispatch.sh review --for A-1")),
        )
        for case_id, finding in cases:
            with self.subTest(id=case_id, origin=ORIGIN):
                with self.assertRaises(self.launch.ENVELOPE.EnvelopeError):
                    self.launch.ENVELOPE.finding(**finding)
                data = self.claude(self.with_finding(finding))
                self.assertNotEqual(
                    data["status"], "ok",
                    f"{case_id}: the record would carry {data['findings']} as an ok finding")


if __name__ == "__main__":
    unittest.main()
