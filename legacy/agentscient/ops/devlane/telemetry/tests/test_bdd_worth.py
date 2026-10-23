"""BDD traceability cases joining worth behaviors covered separately elsewhere."""

import json
import unittest

import test_worth as worth_contract
import test_worth_dispatches as dispatch_meter

from ops.testkit import World  # noqa: F401 - the import rule (docs/tests.md rule 1)


class WorthScenarios(unittest.TestCase):
    def setUp(self):
        self.fixture = worth_contract.WorthContractTests(
            "test_default_window_boundaries_and_independent_edge_overrides"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_report_joins_window_cost_results_and_stamp(self):
        """Scenario: the report joins spend and results for a window"""
        w = self.fixture
        base = worth_contract.iso_epoch("2026-08-22T10:00:00.000Z")
        w._plant_standard_claude(
            "joined-claude",
            "2026-08-22T10:00:00.000Z",
        )
        w._plant_standard_codex(
            "joined-codex",
            "2026-08-22T10:00:00.000Z",
        )
        w._plant_grok(
            "joined-grok",
            "2026-08-22T10:00:00.000Z",
            [[{
                "inputTokens": 70,
                "cachedReadTokens": 10,
                "cacheCreationTokens": 5,
                "outputTokens": 15,
                "totalTokens": 100,
                "reasoningTokens": 2,
                "costUsdTicks": 9,
            }]],
        )
        self.assertGreater(base, 0)

        w._git("switch", "-q", "-c", "pr-7")
        w._commit_files(
            {"src/joined.txt": "joined report fixture\n"},
            "implement joined report fixture",
            "2026-08-22T10:30:00.000Z",
        )
        w._git("switch", "-q", "dev")
        merge = w._merge(
            "pr-7",
            "Merge pull request #7 from fixtures/pr-7",
            "2026-08-22T10:40:00.000Z",
        )
        parents = w._git("show", "-s", "--format=%P", merge).stdout.split()
        self.assertEqual(len(parents), 2, "the numbered-merge plant did not land")

        since = "2026-08-22T09:00:00.000Z"
        until = "2026-08-22T12:00:00.000Z"
        now = "2026-08-22T13:00:00.000Z"
        args = ("--since", since, "--until", until)

        plain = w._run_worth("report", *args, now=now)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        data = w._json_worth("report", *args, now=now)

        expected_totals = {
            "claude": 12_530,
            "codex": 490,
            "grok": 100,
        }
        for harness, total in expected_totals.items():
            record = w._harness_record(data, harness)
            w._assert_key_number(record, ("total",), total)
            line = w._line_for_harness(plain.stdout, harness)
            w._assert_plain_number(line, ("total",), total)

        self.assertEqual(w._pr_numbers(data["results"]), {7})
        w._assert_stamp(plain.stdout, data, since, until, now)

    def test_waste_combines_ranking_ties_and_heavy_turn_identity(self):
        """Scenario: waste ranks the window's sessions and names the heavy turn"""
        w = self.fixture
        w._plant_claude(
            "leader",
            [
                {
                    "id": "leader-small",
                    "timestamp": "2026-08-22T10:00:00.000Z",
                    "output": 10,
                },
                {
                    "id": "leader-heavy",
                    "timestamp": "2026-08-22T10:01:00.000Z",
                    "output": 300,
                },
            ],
        )
        w._plant_claude(
            "tie-a",
            [{
                "id": "tie-a-message",
                "timestamp": "2026-08-22T10:02:00.000Z",
                "output": 200,
            }],
        )
        w._plant_grok(
            "tie-b",
            "2026-08-22T10:00:00.000Z",
            [[{
                "inputTokens": 140,
                "cachedReadTokens": 10,
                "cacheCreationTokens": 0,
                "outputTokens": 50,
                "totalTokens": 200,
                "reasoningTokens": 0,
                "costUsdTicks": 1,
            }]],
        )

        data = w._json_worth(
            "waste",
            "--since",
            "2026-08-22T09:00:00.000Z",
            "--until",
            "2026-08-22T12:00:00.000Z",
            "--top",
            "3",
            now="2026-08-22T13:00:00.000Z",
        )

        self.assertEqual(
            w._session_ids(data),
            ["leader", "tie-a", "tie-b"],
            "sessions did not rank by window total and then id",
        )
        heavy = [
            signal
            for signal in w._signals_of_kind(data, "heavy-turn")
            if (
                w._signal_field(signal, ("harness",)) == "claude"
                and w._signal_field(
                    signal,
                    ("session", "session_id", "id"),
                ) == "leader"
            )
        ]
        self.assertEqual(len(heavy), 1, data["signals"])
        rendered = json.dumps(heavy[0], sort_keys=True)
        self.assertIn("leader-heavy", rendered)
        self.assertIn("300", rendered)


class DispatchScenarios(unittest.TestCase):
    """Unit A scenarios from what-did-today-cost.feature."""

    def setUp(self):
        self.fixture = dispatch_meter.DispatchMeterTests(
            "test_report_counts_a_claude_dispatch_with_usd"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_dispatched_claude_usd_appears_in_the_report(self):
        """Scenario: a dispatched Claude run's USD appears in the report"""
        self.fixture.test_report_counts_a_claude_dispatch_with_usd()

    def test_codex_dispatch_prints_tokens_and_unrecorded_cost(self):
        """Scenario: a Codex dispatch prints tokens and cost=unrecorded"""
        self.fixture.test_report_prints_codex_tokens_and_unrecorded_cost()

    def test_grok_dispatch_prints_tokens_and_ticks(self):
        """Scenario: a Grok dispatch prints tokens and ticks"""
        self.fixture.test_report_prints_grok_tokens_and_ticks()

    def test_job_directory_without_a_record_is_counted_unrecorded(self):
        """Scenario: a job directory without a record is counted and unrecorded"""
        self.fixture.test_a_job_directory_without_a_record_is_counted_unrecorded()

    def test_dispatch_launched_before_the_window_is_not_charged(self):
        """Scenario: a dispatch launched before the window is not charged to it"""
        self.fixture.test_a_dispatch_launched_before_the_window_is_not_charged()

    def test_dispatched_stream_is_not_also_counted_as_a_session(self):
        """Scenario: a dispatched stream is not also counted as a session"""
        self.fixture.test_a_dispatched_stream_is_not_also_counted_as_a_session()


if __name__ == "__main__":
    unittest.main()
