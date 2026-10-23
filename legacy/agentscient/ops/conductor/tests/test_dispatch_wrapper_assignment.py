"""The ordinary wrapper carries an explicit assignment to the launcher.

Grant 309; one dev-lane producer, independent test author absent.
"""

import test_dispatch_wrapper as wrap

from ops.testkit import World  # noqa: F401 - _WrapperFixture owns the World


class AssignmentForwarding(wrap._WrapperFixture):
    def test_explicit_assignment_reaches_the_launcher(self):
        result = self.invoke(self.full_flags("--for", "A-42", "--dry-run"))
        self.assertEqual(result.returncode, 0, result.stderr)
        argv, _env = self.parse_dry_run(result)
        self.assertIn("--for", argv)
        self.assertEqual(argv[argv.index("--for") + 1], "A-42")
