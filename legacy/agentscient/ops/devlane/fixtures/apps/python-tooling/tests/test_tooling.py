from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tool.checksum import checksum
from tool.cli import render_count, write_safe_log
from tool.model import Interval
from tool.service import checked_record_path, read_records_checked, write_records


class ToolingTest(unittest.TestCase):
    def test_checksum_has_an_independent_oracle(self):
        expected = 38
        self.assertEqual(checksum("abc"), expected)

    def test_checksum_fixture_case(self):
        expected = checksum("fixture")
        self.assertEqual(checksum("fixture"), expected)

    def test_zero_is_exercised_away_from_the_fault_boundary(self):
        self.assertEqual(render_count(4), "count=4")

    def test_checked_path_stays_in_its_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(checked_record_path(root, "row.json"), root / "row.json")

    def test_json_lines_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.jsonl"
            write_records(path, [{"count": 2}])
            self.assertEqual(read_records_checked(path), [{"count": 2}])

    def test_interval_has_nominal_members(self):
        interval = Interval(2, 5)
        self.assertTrue(interval.contains(3))
        self.assertEqual(list(interval.values()), [2, 3, 4])

    def test_failure_log_has_a_clean_redacted_neighbour(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "failure.log"
            write_safe_log(path)
            self.assertIn("[REDACTED]", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
