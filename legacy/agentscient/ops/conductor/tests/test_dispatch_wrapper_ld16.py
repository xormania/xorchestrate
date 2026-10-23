# testkit: exempt exercises live_store_guard's kit compare without I/O
"""LD-16 regression: a job directory of the declared shape is not an
ordinary added name, so the shared live-store guard tolerates it while
still failing on anything else.

The half of this file that rode on the dispatch wrapper's own tear-down
was deleted by S5 on 2026-09-16 with the subject it watched: the wrapper
fixture no longer snapshots a host store, because the wrapper runs under
the world's HOME and XDG roots and cannot reach one. The fault that row
carried, a directory minted mid-run by someone else, is carried by
test_dispatch_wrapper.RowHermeticStore's concurrent-entry-not-ours,
which plants a job directory and a launcher log in the world's own jobs
root and asserts neither is read as this run's write. The two rows below
are the guard's own behaviour and still have a subject: live_store_guard
is used by the watchdog, ladder, land and onboard suites.

Written by hand by the conductor (owner, 2026-09-11: "FIX IT"); the
second producer the defect list asks for was not dispatched.
"""

from __future__ import annotations

import unittest

from live_store_guard import assert_unchanged

PLANTED = "20260101T000000Z-tests-grok-a1d016"


class TheGuardStillFailsOnAnOrdinaryName(unittest.TestCase):
    def test_an_ordinary_added_name_fails(self):
        before = {"root": ("dir", ("keep",))}
        after = {"root": ("dir", ("keep", "stray.txt"))}
        with self.assertRaises(AssertionError):
            assert_unchanged(self, before, after)

    def test_a_new_job_dir_passes(self):
        before = {"root": ("dir", ("keep",))}
        after = {"root": ("dir", ("keep", PLANTED))}
        assert_unchanged(self, before, after)


if __name__ == "__main__":
    unittest.main()
