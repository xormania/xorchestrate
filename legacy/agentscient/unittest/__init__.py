"""Standard-library unittest with positional RegexFlag compatibility."""

import re
import sysconfig
from pathlib import Path

_stdlib_package = Path(sysconfig.get_path("stdlib")) / "unittest"
__path__ = [str(_stdlib_package)]
exec(  # noqa: S102 - this wrapper executes the installed stdlib implementation
    compile(
        (_stdlib_package / "__init__.py").read_bytes(),
        str(_stdlib_package / "__init__.py"),
        "exec",
    )
)

from unittest.case import TestCase  # noqa: E402

_assert_regex = TestCase.assertRegex


def _assert_regex_with_flags(self, text, expected_regex, msg=None):
    if isinstance(msg, re.RegexFlag):
        if isinstance(expected_regex, str):
            expected_regex = re.compile(expected_regex, msg)
        msg = None

    return _assert_regex(self, text, expected_regex, msg)


TestCase.assertRegex = _assert_regex_with_flags
