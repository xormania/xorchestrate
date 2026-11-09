"""Pure, versioned interpretations of retained telemetry observations.

These functions consume already authorized rows. They do not collect events,
read files, infer permission, or replace source observations with derived values.
"""

import json
from copy import deepcopy
from math import isfinite

PROJECTION_VERSION = "xorchestrate-telemetry-views-v1"
NORMALIZATION_VERSION = "xorchestrate-usage-v1"
QUANTITIES = ("input_tokens", "output_tokens", "cache_read_tokens",
              "cache_creation_tokens", "reasoning_tokens")


def _payload(event):
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _ordered(events):
    identities = {}
    result = []
    for event in events:
        identity = event.get("event_id")
        if identity is not None and identity in identities:
            if event != identities[identity]:
                raise ValueError("conflicting telemetry observation identity")
            continue
        if identity is not None:
            identities[identity] = event
        result.append(deepcopy(event))
    return sorted(result, key=lambda event: event.get("sequence", 0))


def _cutoff(events):
    return max((event.get("sequence", 0) for event in events), default=0)


def _corrected(events):
    """Append-only corrections change a projection, never the source corpus."""
    superseded = set()
    corrections = []
    known = {event.get("event_id") for event in events}
    for event in events:
        targets = []
        direct = _payload(event).get("corrects_event_id")
        if direct:
            targets.append(direct)
        for link in event.get("links", []):
            if isinstance(link, dict):
                relation, target = link.get("relation"), link.get("target_id")
            else:
                relation, target = link if len(link) == 2 else (None, None)
            if relation in ("corrects", "supersedes"):
                targets.append(target)
        for target in targets:
            if target in known:
                superseded.add(target)
            corrections.append({"event_id": event.get("event_id"), "target_id": target,
                                "resolved": target in known})
    return [event for event in events if event.get("event_id") not in superseded], corrections


def usage_summary(events):
    """Normalize explicit counter semantics without treating unknowns as zero.

    Cumulative snapshots contribute their latest delta within a stream. An
    explicit reset starts a new segment; an unexplained decrease makes the
    affected quantity unknown. Replayed cached evaluations contribute no new
    provider quantities. Cache token categories stay separate from input/output.
    No rate card means cost remains unknown.
    """
    raw = _ordered(events)
    selected, corrections = _corrected([event for event in raw if isinstance(_payload(event).get("usage"), dict)])
    totals = {name: 0 for name in QUANTITIES}
    observed = {name: False for name in QUANTITIES}
    uncertain = {name: False for name in QUANTITIES}
    streams = {}
    issues = []
    source_ids = []
    replay_ids = []
    source_mappings = []
    for event in selected:
        payload = _payload(event)
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        usage = dict(usage)
        if payload.get("source_version") == "claude-transcript-jsonl-v1":
            aliases = {"cache_read_input_tokens": "cache_read_tokens",
                       "cache_creation_input_tokens": "cache_creation_tokens"}
            for source_name, target_name in aliases.items():
                if source_name in usage and target_name not in usage:
                    usage[target_name] = usage[source_name]
                    source_mappings.append({"event_id": event.get("event_id"), "source": source_name,
                                            "quantity": target_name, "mapping_version": "claude-usage-v1"})
        source_ids.append(event.get("event_id"))
        if payload.get("cache_replay") is True or payload.get("reused_from") is not None:
            replay_ids.append(event.get("event_id"))
            continue
        semantics = payload.get("counter_semantics", usage.get("counter_semantics"))
        context = event.get("context", {})
        stream = payload.get("usage_stream_id")
        explicit_stream = stream is not None
        if not explicit_stream:
            stream = (context.get("producer_id"), context.get("session_id"),
                      context.get("operation_id"), context.get("attempt_id"),
                      payload.get("model"), payload.get("source"))
            if not any(stream):
                stream = event.get("event_id")
        # An explicit source stream may span many attempts (e.g. a session
        # total). Attempts scope inferred streams only; changing an attempt must
        # not turn repeated cumulative totals into independent new quantities.
        identity = (str(stream), context.get("project_id"), context.get("dataset_id"),
                    context.get("collector_id"), context.get("producer_id"), None if explicit_stream else context.get("session_id"),
                    None if explicit_stream else context.get("attempt_id"))
        previous = streams.setdefault(identity, {})
        for name in QUANTITIES:
            value = usage.get(name)
            valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and isfinite(value) and value >= 0)
            if not valid:
                uncertain[name] = True
                continue
            if semantics == "incremental":
                observed[name] = True
                totals[name] += value
            elif semantics in ("cumulative", "overwrite", "reset"):
                observed[name] = True
                prior = previous.get(name, 0)
                if semantics == "reset":
                    totals[name] += value
                elif value < prior and semantics != "overwrite":
                    uncertain[name] = True
                    issues.append({"event_id": event.get("event_id"), "quantity": name,
                                   "reason": "unexplained_counter_decrease"})
                else:
                    totals[name] += value - prior
                previous[name] = value
            else:
                uncertain[name] = True
                issues.append({"event_id": event.get("event_id"), "quantity": name,
                               "reason": "unknown_counter_semantics"})
    missing = [name for name in QUANTITIES if not observed[name] or uncertain[name]]
    quantities = {name: totals[name] if name not in missing else None for name in QUANTITIES}
    return {"quantities": quantities,
            "known_quantities": {name: totals[name] if observed[name] else None for name in QUANTITIES},
            "missing_categories": missing, "normalization_version": NORMALIZATION_VERSION,
            "projection_version": PROJECTION_VERSION, "source_cutoff": _cutoff(raw),
            "source_event_ids": source_ids, "raw_observations": raw,
            "cache_replays": replay_ids, "corrections": corrections, "issues": issues,
            "source_mappings": source_mappings,
            "unit": "token", "cost": {"status": "unknown", "reason": "no_rate_card"}}


def compare_runs(baseline, candidate, *, event_types=("trial.result", "run.result", "candidate.result"),
                 unit="reported_trial_result", passed_outcomes=("passed",), failed_outcomes=("failed",)):
    """Describe an explicitly declared result population, never incidental logs.

    Defaults select reported trial results. A caller comparing model assessments
    must declare that event family, outcome mapping and assessment unit; provider
    success or valid parsing does not by itself mean a completed task passed.
    """
    if (not event_types or not isinstance(unit, str) or not unit or
            set(passed_outcomes) & set(failed_outcomes)):
        raise ValueError("invalid telemetry comparison population")
    for values in (event_types, passed_outcomes, failed_outcomes):
        if isinstance(values, str) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError("comparison population requires explicit text identifiers")
    event_types = tuple(event_types)
    passed_outcomes, failed_outcomes = tuple(passed_outcomes), tuple(failed_outcomes)
    before, after = _ordered(baseline), _ordered(candidate)

    def population(events):
        effective, corrections = _corrected([event for event in events if event.get("event_type") in event_types])
        outcomes = []
        conditions = []
        provenance = []
        for event in effective:
            payload = _payload(event)
            outcome = payload.get("outcome")
            if outcome is None and isinstance(payload.get("result"), dict):
                outcome = payload["result"].get("outcome")
            outcomes.append(outcome)
            conditions.append(payload.get("conditions", {}))
            provenance.append({"event_id": event.get("event_id"), "sequence": event.get("sequence"),
                               "event_type": event.get("event_type"), "observation_basis": event.get("basis", "unknown"),
                               "outcome_basis": "reported",
                               "producer_claimed_outcome_basis": payload.get("outcome_basis"),
                               "attempt_id": event.get("context", {}).get("attempt_id")})
        passed = sum(outcome in passed_outcomes for outcome in outcomes)
        failed = sum(outcome in failed_outcomes for outcome in outcomes)
        known = passed + failed
        return {"attempt_count": len(outcomes), "observed_outcomes": known,
                "passed": passed, "failed": failed, "missing_outcomes": len(outcomes) - known,
                "pass_rate": passed / known if known else None, "conditions": conditions,
                "corrections": corrections, "provenance": provenance,
                "source_event_ids": [item["event_id"] for item in provenance]}

    left, right = population(before), population(after)
    keys = {key for population_conditions in (left["conditions"], right["conditions"])
            for conditions in population_conditions for key in conditions}
    differences = {}
    for key in sorted(keys):
        left_values = [item.get(key) for item in left["conditions"]]
        right_values = [item.get(key) for item in right["conditions"]]
        if {json.dumps(item, sort_keys=True) for item in left_values} != {json.dumps(item, sort_keys=True) for item in right_values}:
            differences[key] = {"baseline": left_values, "candidate": right_values}
    inconclusive = not left["observed_outcomes"] or not right["observed_outcomes"]
    limitations = ["Observational comparison does not establish causation or authorize promotion."]
    if inconclusive:
        limitations.append("At least one population has no known outcomes.")
    if left["missing_outcomes"] or right["missing_outcomes"]:
        limitations.append("Missing outcomes are excluded from pass-rate denominators.")
    if differences:
        limitations.append("Observed conditions differ between selected populations.")
    return {"status": "inconclusive" if inconclusive else "compared", "baseline": left, "candidate": right,
            "differing_conditions": differences,
            "population_basis": {"event_types": list(event_types), "unit": unit,
                                 "passed_outcomes": list(passed_outcomes), "failed_outcomes": list(failed_outcomes),
                                 "count_basis": "selected terminal observations after linked corrections",
                                 "outcome_basis": "reported"},
            "source_cutoff": {"baseline": _cutoff(before), "candidate": _cutoff(after)},
            "projection_version": PROJECTION_VERSION, "limitations": limitations}
