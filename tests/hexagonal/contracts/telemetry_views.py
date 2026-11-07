"""Independent quantity, comparison and bounded-projection expectations."""

import json
from copy import deepcopy


class TelemetryViewsContract:
    def test_cumulative_snapshots_resets_and_cache_replay_are_not_summed_as_calls(self):
        events = [
            self.observation("one", 1, {"usage": {"input_tokens": 10, "output_tokens": 4}, "counter_semantics": "cumulative", "usage_stream_id": "stream"}),
            self.observation("two", 2, {"usage": {"input_tokens": 15, "output_tokens": 6}, "counter_semantics": "cumulative", "usage_stream_id": "stream"}),
            self.observation("three", 3, {"usage": {"input_tokens": 3, "output_tokens": 1}, "counter_semantics": "reset", "usage_stream_id": "stream"}),
            self.observation("four", 4, {"usage": {"input_tokens": 5, "output_tokens": 2}, "counter_semantics": "incremental"}),
            self.observation("replay", 5, {"usage": {"input_tokens": 5, "output_tokens": 2}, "cache_replay": True, "reused_from": "four"}),
        ]
        before = deepcopy(events)
        summary = self.views.usage_summary(events)
        self.assertEqual(summary["quantities"]["input_tokens"], 23)
        self.assertEqual(summary["quantities"]["output_tokens"], 9)
        self.assertEqual(summary["source_cutoff"], 5)
        self.assertTrue(summary["normalization_version"])
        self.assertIn("one", summary["source_event_ids"])
        self.assertEqual(events, before)

    def test_duplicate_observation_identity_does_not_duplicate_quantity(self):
        event = self.observation("once", 1, {"usage": {"input_tokens": 11, "output_tokens": 0}, "counter_semantics": "incremental"})
        summary = self.views.usage_summary([event, deepcopy(event)])
        self.assertEqual(summary["quantities"]["input_tokens"], 11)
        self.assertEqual(summary["quantities"]["output_tokens"], 0)

    def test_unknown_and_zero_remain_distinct_with_known_subtotals(self):
        events = [
            self.observation("known", 1, {"usage": {"input_tokens": 7, "output_tokens": 0}, "counter_semantics": "incremental"}),
            self.observation("unknown", 2, {"usage": {"input_tokens": None, "output_tokens": 0}, "counter_semantics": "incremental"}),
        ]
        summary = self.views.usage_summary(events)
        self.assertIsNone(summary["quantities"]["input_tokens"])
        self.assertEqual(summary["known_quantities"]["input_tokens"], 7)
        self.assertEqual(summary["quantities"]["output_tokens"], 0)
        self.assertIsNone(summary["quantities"]["cache_read_tokens"])
        self.assertIn("input_tokens", summary["missing_categories"])
        self.assertIn("cache_read_tokens", summary["missing_categories"])

    def test_empty_usage_cannot_be_reported_as_zero_spend(self):
        summary = self.views.usage_summary([])
        self.assertIsNone(summary["quantities"]["input_tokens"])
        self.assertIsNone(summary["quantities"]["output_tokens"])
        self.assertEqual(summary["source_event_ids"], [])

    def test_unrelated_observation_does_not_make_reported_usage_unknown(self):
        events = [
            self.observation("usage", 1, {"usage": {"input_tokens": 7, "output_tokens": 2}, "counter_semantics": "incremental"}),
            self.observation("activity", 2, {"state": "idle"}, event_type="native.activity"),
        ]
        summary = self.views.usage_summary(events)
        self.assertEqual(summary["quantities"]["input_tokens"], 7)
        self.assertEqual(summary["quantities"]["output_tokens"], 2)

    def test_claude_transcript_usage_aliases_are_versioned_and_overwrite_per_message(self):
        events = [
            self.observation("a-first", 1, {"usage": {"input_tokens": 4, "output_tokens": 2,
                "cache_read_input_tokens": 3, "cache_creation_input_tokens": 1},
                "counter_semantics": "overwrite", "usage_stream_id": "message-a",
                "source_version": "claude-transcript-jsonl-v1"}, event_type="native.usage"),
            self.observation("a-final", 2, {"usage": {"input_tokens": 8, "output_tokens": 3,
                "cache_read_input_tokens": 6, "cache_creation_input_tokens": 2},
                "counter_semantics": "overwrite", "usage_stream_id": "message-a",
                "source_version": "claude-transcript-jsonl-v1"}, event_type="native.usage"),
            self.observation("b-final", 3, {"usage": {"input_tokens": 2, "output_tokens": 1,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                "counter_semantics": "overwrite", "usage_stream_id": "message-b",
                "source_version": "claude-transcript-jsonl-v1"}, event_type="native.usage"),
        ]
        before = deepcopy(events)
        summary = self.views.usage_summary(events)
        self.assertEqual(summary["quantities"]["input_tokens"], 10)
        self.assertEqual(summary["quantities"]["output_tokens"], 4)
        self.assertEqual(summary["quantities"]["cache_read_tokens"], 6)
        self.assertEqual(summary["quantities"]["cache_creation_tokens"], 2)
        mappings = summary["source_mappings"]
        self.assertTrue(all(mapping["mapping_version"] for mapping in mappings))
        self.assertIn(("cache_read_input_tokens", "cache_read_tokens"), {(mapping["source"], mapping["quantity"]) for mapping in mappings})
        self.assertIn(("cache_creation_input_tokens", "cache_creation_tokens"), {(mapping["source"], mapping["quantity"]) for mapping in mappings})
        self.assertEqual(summary["raw_observations"], before)
        self.assertEqual(events, before)

    def test_unknown_source_counter_semantics_retains_raw_without_inventing_totals(self):
        event = self.observation("unknown-shape", 1, {"usage": {"input_tokens": 17, "output_tokens": 9},
            "counter_semantics": "unknown", "source_version": "unknown-native-format"}, event_type="native.usage")
        before = deepcopy(event)
        summary = self.views.usage_summary([event])
        self.assertIsNone(summary["quantities"]["input_tokens"])
        self.assertIsNone(summary["quantities"]["output_tokens"])
        self.assertIn("input_tokens", summary["missing_categories"])
        self.assertEqual(summary["raw_observations"], [before])
        self.assertEqual(event, before)

    def test_explicit_cumulative_stream_is_not_split_by_attempt_identity(self):
        events = [
            self.observation("turn-one", 1, {"usage": {"input_tokens": 10, "output_tokens": 2},
                "counter_semantics": "cumulative", "usage_stream_id": "session-cumulative"},
                event_type="native.usage", context={"producer_id": "native-producer", "session_id": "same-session", "attempt_id": "turn-one"}),
            self.observation("turn-two", 2, {"usage": {"input_tokens": 15, "output_tokens": 4},
                "counter_semantics": "cumulative", "usage_stream_id": "session-cumulative"},
                event_type="native.usage", context={"producer_id": "native-producer", "session_id": "same-session", "attempt_id": "turn-two"}),
        ]
        summary = self.views.usage_summary(events)
        self.assertEqual(summary["quantities"]["input_tokens"], 15)
        self.assertEqual(summary["quantities"]["output_tokens"], 4)

    def test_exact_native_message_reingestion_is_not_charged_again_for_new_application_session(self):
        payload = {"usage": {"input_tokens": 10, "output_tokens": 2,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                   "counter_semantics": "overwrite", "usage_stream_id": "native-conversation:message-one",
                   "source_version": "claude-transcript-jsonl-v1"}
        events = [
            self.observation("first-import", 1, payload, event_type="native.usage", context={
                "project_id": "project-one", "producer_id": "native-producer", "native_id": "native-conversation",
                "session_id": "application-session-one", "incarnation": "generation-one", "work_id": "work-one"}),
            self.observation("reimport", 2, deepcopy(payload), event_type="native.usage", context={
                "project_id": "project-one", "producer_id": "native-producer", "native_id": "native-conversation",
                "session_id": "application-session-two", "incarnation": "generation-two", "work_id": "work-one"}),
        ]
        before = deepcopy(events)
        summary = self.views.usage_summary(events)
        self.assertEqual(summary["quantities"]["input_tokens"], 10)
        self.assertEqual(summary["quantities"]["output_tokens"], 2)
        self.assertEqual(set(summary["source_event_ids"]), {"first-import", "reimport"})
        self.assertEqual(summary["raw_observations"], before)
        self.assertEqual(events, before)
        independent_project = deepcopy(events)
        independent_project[1]["context"]["project_id"] = "project-two"
        self.assertEqual(self.views.usage_summary(independent_project)["quantities"]["input_tokens"], 20,
                         "a stable native stream identifier cannot merge unrelated projects")

    def test_timeline_pages_keep_their_cutoff_when_new_events_arrive(self):
        events = [self.observation(f"e{number}", number, {"number": number}) for number in range(1, 6)]
        events.reverse()
        first = self.views.timeline(events, after=2, limit=2, cutoff=5)
        self.assertEqual([row["sequence"] for row in first["events"]], [3, 4])
        self.assertEqual(first["next_after"], 4)
        self.assertEqual(first["source_cutoff"], 5)
        self.assertTrue(first["projection_version"])
        later = self.views.timeline(events + [self.observation("e6", 6, {"number": 6})], after=first["next_after"], limit=2, cutoff=first["source_cutoff"])
        self.assertEqual([row["sequence"] for row in later["events"]], [5])
        self.assertEqual(later["source_cutoff"], 5)

    def test_timeline_payload_limit_returns_an_explicit_omission_without_mutation(self):
        event = self.observation("large", 1, {"text": "large-private-text" * 1000})
        before = deepcopy(event)
        page = self.views.timeline([event], max_bytes=1024)
        self.assertLessEqual(len(json.dumps(page).encode()), 1400, "bounded event payload must not escape through the projection")
        self.assertEqual(len(page["events"]), 1)
        stub = page["events"][0]
        self.assertEqual(stub["event_id"], "large")
        self.assertTrue(stub["payload_omitted"])
        self.assertGreater(stub["payload_size_bytes"], 1024)
        self.assertNotIn("large-private-text", json.dumps(stub))
        self.assertEqual(event, before)

    def test_timeline_preserves_unresolved_relation_targets(self):
        event = self.observation("one", 1, {}, links=[["caused_by", "missing-event"]])
        page = self.views.timeline([event])
        self.assertIn("missing-event", json.dumps(page["unresolved_links"]))

    def test_empty_comparison_is_inconclusive_and_retains_denominators(self):
        candidate = [self.observation("pass", 1, {"outcome": "passed", "conditions": {"fixture": "v1"}}, event_type="trial.result")]
        result = self.views.compare_runs([], candidate)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["baseline"]["attempt_count"], 0)
        self.assertIsNone(result["baseline"]["pass_rate"])
        self.assertEqual(result["candidate"]["attempt_count"], 1)
        self.assertTrue(result["limitations"])

    def test_comparison_discloses_missing_outcomes_conditions_and_corrections(self):
        baseline = [
            self.observation("old-pass", 1, {"outcome": "passed", "conditions": {"fixture": "v1"}}, event_type="trial.result"),
            self.observation("old-unknown", 2, {"outcome": "unknown", "conditions": {"fixture": "v1"}}, event_type="trial.result"),
            self.observation("correction", 3, {"outcome": "failed", "corrects_event_id": "old-pass", "conditions": {"fixture": "v1"}}, event_type="trial.result"),
        ]
        candidate = [self.observation("candidate", 4, {"outcome": "passed", "conditions": {"fixture": "v2"}}, event_type="trial.result")]
        before = deepcopy(baseline)
        result = self.views.compare_runs(baseline, candidate)
        self.assertEqual(result["baseline"]["attempt_count"], 2)
        self.assertEqual(result["baseline"]["passed"], 0)
        self.assertEqual(result["baseline"]["failed"], 1)
        self.assertEqual(result["baseline"]["missing_outcomes"], 1)
        self.assertTrue(result["differing_conditions"])
        self.assertEqual(result["source_cutoff"], {"baseline": 3, "candidate": 4})
        self.assertTrue(result["projection_version"])
        self.assertEqual(baseline, before)

    def test_comparison_does_not_treat_context_delivery_success_as_a_task_trial(self):
        baseline = [self.observation("context-sent", 1, {"outcome": "success", "run_id": "run-one"}, event_type="context.delivery")]
        candidate = [self.observation("checked", 2, {"outcome": "passed", "run_id": "run-two"}, event_type="trial.result")]
        result = self.views.compare_runs(baseline, candidate)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["baseline"]["attempt_count"], 0)
        self.assertEqual(result["baseline"]["passed"], 0)
        self.assertIsNone(result["baseline"]["pass_rate"])
        self.assertEqual(result["candidate"]["attempt_count"], 1)


class OperationalExportContract:
    def test_local_sink_receives_operational_data_and_input_stays_unchanged(self):
        event = self.observation("call", 1, {"status": "completed", "duration_seconds": 0.25, "usage": {"input_tokens": 3}})
        before = deepcopy(event)
        sink = self.sink()
        result = self.exports.OperationalExporter(sink).record(event)
        self.assertEqual(result["status"], "exported")
        self.assertTrue(sink.events)
        self.assertEqual(event, before)

    def test_sampling_and_failed_sink_cannot_remove_durable_observations(self):
        self.dataset.append("durable", {"status": "completed"})
        original = deepcopy(self.dataset.rows())
        event = self.observation("durable", 1, {"status": "completed"})
        sink = self.sink()
        sampled = self.exports.OperationalExporter(sink, sample=lambda _: False).record(event)
        failed = self.exports.OperationalExporter(self.sink(fails=True)).record(event)
        self.assertEqual(sampled["status"], "sampled_out")
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(sink.events, [])
        self.assertEqual(self.dataset.rows(), original)

    def test_sampling_callback_failure_is_noninterfering(self):
        def sample(_):
            raise RuntimeError("controlled sampling failure")
        event = self.observation("safe", 1, {"status": "completed"})
        before = deepcopy(event)
        result = self.exports.OperationalExporter(self.sink(), sample=sample).record(event)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(event, before)

    def test_private_payload_is_not_copied_into_operational_signals(self):
        event = self.observation("safe", 1, {"status": "completed", "prompt": "private-source-content", "authorization": "Bearer credential-value"})
        sink = self.sink()
        self.exports.OperationalExporter(sink).record(event)
        encoded = json.dumps(sink.events)
        self.assertNotIn("private-source-content", encoded)
        self.assertNotIn("credential-value", encoded)

    def test_metric_dimensions_exclude_event_session_and_run_identifiers(self):
        event = {"kind": "metric", "name": "capture.duration", "value": 0.25, "unit": "s",
                 "boundary": "evaluator", "outcome": "completed", "event_id": "unique-event",
                 "session_id": "unique-session", "run_id": "unique-run", "attributes": {
                     "event_id": "unique-event", "session_id": "unique-session", "run_id": "unique-run",
                     "boundary": "evaluator", "outcome": "completed", "authorization": "private-key"}}
        sink = self.sink()
        self.assertEqual(self.exports.OperationalExporter(sink).record(event)["status"], "exported")
        encoded = json.dumps(sink.events)
        self.assertNotIn("unique-event", encoded)
        self.assertNotIn("unique-session", encoded)
        self.assertNotIn("unique-run", encoded)
        self.assertNotIn("private-key", encoded)
        self.assertIn("evaluator", encoded)

    def test_otlp_transport_is_optional_and_log_payload_uses_standard_envelope(self):
        calls = []

        def transport(url, payload, timeout):
            calls.append((url, json.loads(payload), timeout))
            return b"{}"

        sink = self.exports.OtlpOperationalSink("http://127.0.0.1:4318", transport=transport, timeout=0.5)
        self.assertEqual(calls, [], "constructing an optional exporter cannot contact a backend")
        result = self.exports.OperationalExporter(sink).record({"kind": "log", "name": "capture.failure", "boundary": "capture", "outcome": "unavailable"})
        self.assertEqual(result["status"], "exported")
        self.assertEqual(len(calls), 1)
        url, payload, timeout = calls[0]
        self.assertEqual(url, "http://127.0.0.1:4318/v1/logs")
        self.assertEqual(timeout, 0.5)
        records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(len(records), 1)
        self.assertIn("capture.failure", json.dumps(records[0]))

    def test_otlp_metric_and_trace_signals_keep_distinct_standard_envelopes(self):
        calls = []

        def transport(url, payload, timeout):
            calls.append((url, json.loads(payload)))
            return b"{}"

        sink = self.exports.OtlpOperationalSink("http://127.0.0.1:4318", transport=transport)
        exporter = self.exports.OperationalExporter(sink)
        self.assertEqual(exporter.record({"kind": "metric", "name": "capture.count", "value": 1, "unit": "1", "boundary": "capture"})["status"], "exported")
        self.assertEqual(exporter.record({"kind": "span", "name": "evaluator.request", "boundary": "evaluator", "duration_ms": 12, "trace_id": "1" * 32, "span_id": "2" * 16})["status"], "exported")
        self.assertEqual([call[0] for call in calls], ["http://127.0.0.1:4318/v1/metrics", "http://127.0.0.1:4318/v1/traces"])
        self.assertIn("metrics", calls[0][1]["resourceMetrics"][0]["scopeMetrics"][0])
        span = calls[1][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["traceId"], "1" * 32)
        self.assertEqual(span["spanId"], "2" * 16)

    def test_missing_or_invalid_duration_never_becomes_a_fabricated_metric_sample(self):
        calls = []

        def transport(url, payload, timeout):
            calls.append((url, json.loads(payload)))
            return b"{}"

        sink = self.exports.OtlpOperationalSink("http://127.0.0.1:4318", transport=transport)
        exporter = self.exports.OperationalExporter(sink)
        for value in (None, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                result = exporter.record({"kind": "metric", "name": "xorchestrate.capture.duration",
                    "measurement": "duration", "boundary": "capture", "unit": "ms", "value": value})
                self.assertEqual(result["status"], "unavailable")
        result = exporter.record({"kind": "metric", "name": "xorchestrate.capture.duration",
            "measurement": "duration", "boundary": "capture", "unit": "ms"})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(calls, [], "invalid duration cannot reach an OTLP histogram as a fabricated1ms value")


class ObservationSignalsContract:
    def test_operation_span_measures_request_to_terminal_gap_and_capture_cost_is_separate(self):
        clock = self.clock(5.0)
        sink = self.sink()
        signals = self.exports.ObservationSignals(self.exports.OperationalExporter(sink), clock=clock)
        context = {"attempt_id": "attempt-one", "action": "invoke", "operation_id": "operation-one"}
        signals.observe("application.requested", context, capture_status="recorded", capture_duration_ms=3)
        clock.now = 5.25
        signals.observe("application.completed", context, capture_status="recorded", capture_duration_ms=7)
        spans = [event for event in sink.events if event.get("kind") == "span"]
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["duration_ms"], 250)
        self.assertTrue(spans[0].get("trace_id"))
        self.assertTrue(spans[0].get("span_id"))
        metrics = [event for event in sink.events if event.get("kind") == "metric" and event.get("name") == "xorchestrate.capture.duration"]
        self.assertEqual([event["value"] for event in metrics], [3, 7])
        self.assertEqual(signals.status()["correlation"]["pending"], 0)

    def test_terminal_without_observed_start_never_invents_an_operation_span(self):
        sink = self.sink()
        signals = self.exports.ObservationSignals(self.exports.OperationalExporter(sink), clock=self.clock(2))
        signals.observe("application.failed", {"attempt_id": "missing-start", "action": "invoke"}, capture_status="recorded")
        self.assertFalse([event for event in sink.events if event.get("kind") == "span"])
        self.assertEqual(signals.status()["correlation"]["missing_starts"], 1)

    def test_trace_sampling_can_suppress_spans_while_preserving_capture_metrics(self):
        sink = self.sink()
        exporter = self.exports.OperationalExporter(sink, sample=lambda event: event.get("kind") != "span")
        clock = self.clock(1)
        signals = self.exports.ObservationSignals(exporter, clock=clock)
        context = {"attempt_id": "sampled-span", "action": "invoke"}
        signals.observe("application.requested", context, capture_status="recorded", capture_duration_ms=1)
        clock.now = 2
        signals.observe("application.completed", context, capture_status="recorded", capture_duration_ms=1)
        self.assertFalse([event for event in sink.events if event.get("kind") == "span"])
        self.assertTrue([event for event in sink.events if event.get("kind") == "metric"])

    def test_correlation_capacity_is_configurable_and_evicts_with_visible_gap(self):
        sink = self.sink()
        signals = self.exports.ObservationSignals(self.exports.OperationalExporter(sink), clock=self.clock(1), capacity=1)
        signals.observe("application.requested", {"attempt_id": "first", "action": "invoke"})
        signals.observe("application.requested", {"attempt_id": "second", "action": "invoke"})
        state = signals.status()["correlation"]
        self.assertEqual(state["pending"], 1)
        self.assertEqual(state["evicted"], 1)
        signals.observe("application.completed", {"attempt_id": "first", "action": "invoke"})
        self.assertEqual(signals.status()["correlation"]["missing_starts"], 1)
        self.assertFalse([event for event in sink.events if event.get("kind") == "span"])
