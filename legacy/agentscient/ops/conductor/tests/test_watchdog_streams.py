"""Independent differential coupling probes for watchdog's bounded scanners.

The oracle is the pre-optimization regex language, frozen here rather than
imported from production. Synthetic in-memory streams only; no stores,
processes, vendor calls or implementation-authored expectations.
"""

from __future__ import annotations

import importlib.util
import io
import re
import unittest
from pathlib import Path

from ops.testkit import World

SOURCE = Path(__file__).resolve().parents[1] / "tools" / "watchdog.py"
SIBLINGS = ("instrument.py", "laneconf.py", "dispatch_observation.py")
ERROR_ORACLE = re.compile(r"\berrored\b|\berrors\s*=\s*[1-9]\d*\b", re.IGNORECASE)
REFUSAL_ORACLE = re.compile(
    rb"(?m)^\s*(?:empty\-comparison|scope\-cap|conf\-key|toolchain):"
)


class ChunkStream(io.BytesIO):
    def __init__(self, data, chunk_size):
        super().__init__(data)
        self.chunk_size = chunk_size

    def read(self, size=-1):
        return super().read(self.chunk_size if size < 0 else min(size, self.chunk_size))


class BoundedScannerLanguage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(prefix="watchdog-streams-")
        cls.addClassCleanup(cls.world.close)
        staged = cls.world.stage(SOURCE, siblings=SIBLINGS)
        spec = importlib.util.spec_from_file_location("watchdog_language_subject", staged)
        cls.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.subject)

    def test_refusals_require_line_start_not_embedded_text(self):
        for token in (b"empty-comparison:", b"scope-cap:", b"conf-key:", b"toolchain:"):
            for prefix in (b"", b" ", b"\t", b"\n ", b"x ", b"prefix", b"x\n ", b"\x00"):
                for suffix in (b"", b" needed", b"\n"):
                    data = prefix + token + suffix
                    for chunk_size in (1, 3, 64 * 1024):
                        with self.subTest(data=data, chunk_size=chunk_size):
                            self.assertEqual(
                                self.subject._refusal_stream_matches(ChunkStream(data, chunk_size)),
                                bool(REFUSAL_ORACLE.search(data)),
                            )

    def test_error_language_keeps_unicode_boundaries_and_whitespace(self):
        words = ("errored", "ERRORED", "errors=2", "errors\u00a0=\u00a02",
                 "errors=1\u0662", "errors=\u0662", "errors=0",
                 "errors=20x", "error", "errors", "error\u017f=2")
        for word in words:
            for prefix in ("", " ", "é", "_", "9", "\n"):
                for suffix in ("", " ", "é", "_", "\n"):
                    text = prefix + word + suffix
                    data = text.encode("utf-8")
                    for chunk_size in (1, 2, 7, 64 * 1024):
                        with self.subTest(text=text, chunk_size=chunk_size):
                            self.assertEqual(
                                self.subject._error_stream_matches(ChunkStream(data, chunk_size)),
                                bool(ERROR_ORACLE.search(text)),
                            )

    def test_long_whitespace_and_token_boundaries_are_not_truncated(self):
        for data, matcher, oracle in (
            (b" " * 131073 + b"scope-cap: x", self.subject._refusal_stream_matches,
             lambda value: bool(REFUSAL_ORACLE.search(value))),
            (b"x " * 65536 + b"scope-cap: x", self.subject._refusal_stream_matches,
             lambda value: bool(REFUSAL_ORACLE.search(value))),
            (b"errors" + b" " * 131073 + b"=2", self.subject._error_stream_matches,
             lambda value: bool(ERROR_ORACLE.search(value.decode("utf-8", "replace")))),
            (b"x" * 65532 + b" errored", self.subject._error_stream_matches,
             lambda value: bool(ERROR_ORACLE.search(value.decode("utf-8", "replace")))),
        ):
            with self.subTest(size=len(data), tail=data[-30:]):
                self.assertEqual(matcher(ChunkStream(data, 65536)), oracle(data))

    def test_invalid_utf8_retains_the_previous_replacement_decode(self):
        for data in (b"\xfferrored", b"error\xffed", b"errors=2\xfe",
                     b"x\xffscope-cap:", b"\n\xffscope-cap:"):
            with self.subTest(data=data):
                self.assertEqual(
                    self.subject._error_stream_matches(ChunkStream(data, 1)),
                    bool(ERROR_ORACLE.search(data.decode("utf-8", "replace"))),
                )
                self.assertEqual(
                    self.subject._refusal_stream_matches(ChunkStream(data, 1)),
                    bool(REFUSAL_ORACLE.search(data)),
                )


if __name__ == "__main__":
    unittest.main()
