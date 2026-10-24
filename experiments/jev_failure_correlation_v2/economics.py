"""Scenario accounting, deliberately separate from observed correctness.

Dollar values below are analyst assumptions, not business losses or provider fees.
Unknown charges stay unknown. A sensitivity scenario is never an invoice.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


SCENARIOS = {
    "low_stakes": {
        "currency": "USD", "basis": "analyst assumption; not owner valuation",
        "false_acceptance": 2.0, "duplicate_effect": 5.0,
        "wasted_investigation": 1.0, "low_impact": 0.10,
        "unnecessary_rejection": 0.50, "investigation_action": 0.25,
        "defer_action": 0.10, "unknown_model_call_usd": [0.001, 0.01, 0.10, 1.0],
        "probe_second_usd": [0.0, 0.001, 0.01],
        "human_minute_usd": [0.0, 1.0, 3.0],
    },
    "consequential": {
        "currency": "USD", "basis": "analyst assumption; not owner valuation",
        "false_acceptance": 100.0, "duplicate_effect": 500.0,
        "wasted_investigation": 10.0, "low_impact": 0.10,
        "unnecessary_rejection": 20.0, "investigation_action": 2.0,
        "defer_action": 5.0, "unknown_model_call_usd": [0.001, 0.01, 0.10, 1.0],
        "probe_second_usd": [0.0, 0.001, 0.01],
        "human_minute_usd": [0.0, 1.0, 3.0],
    },
}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        import math
        return float(value) if math.isfinite(value) and value >= 0 else None
    return None


def correct_actions(case: dict) -> list[str]:
    value = case.get("outcome_key", {}).get("action")
    return value if isinstance(value, list) else [value] if value else []


def loss(case: dict, action: str | None, scenario: dict) -> float | None:
    """Outcome loss only. Resource cost is charged elsewhere, exactly once.

    Investigation/defer are real operational choices, not missing results. Their
    delay/handling assumptions apply only when they are not the outcome action.
    Missing/invalid/provider-failed actions cannot receive an assumed loss of zero.
    """
    truth = correct_actions(case)
    if not truth or action is None or action == "unresolved":
        return None
    if action in truth:
        return 0.0
    consequences = case.get("packet", {}).get("consequences", {})
    category = consequences.get("class", "false_acceptance")
    if action == "accept":
        return float(scenario.get(category, scenario["false_acceptance"]))
    if action == "reject":
        return float(scenario["unnecessary_rejection"])
    if action == "investigate":
        return float(scenario["investigation_action"])
    if action == "defer":
        return float(scenario["defer_action"])
    return None


def accounting(attempts: list[dict]) -> dict:
    """Sum actual attempt records, including failures and retries.

    Cached response references are not additional incurred calls. A known zero
    must be explicit; lack of provider billing data is never converted to zero.
    """
    known = 0.0
    unknown = 0
    list_price = 0.0
    list_price_unknown = 0
    grounded_cost = 0.0
    grounded_unknown = 0
    latency = 0.0
    latency_unknown = 0
    tokens: dict[str, float] = defaultdict(float)
    status: dict[str, int] = defaultdict(int)
    routes: dict[str, int] = defaultdict(int)
    purposes: dict[str, int] = defaultdict(int)
    cache = 0
    counted = 0
    seen: set[str] = set()
    billed_units: set[str] = set()
    invocations: set[str] = set()
    for i, row in enumerate(attempts):
        identity = row.get("attempt_id")
        if identity is not None:
            if identity in seen:
                raise ValueError(f"Duplicate attempt identity: {identity}")
            seen.add(identity)
        status[str(row.get("status", "unknown"))] += 1
        routes[str(row.get("backend", row.get("route", "unknown")))] += 1
        if row.get("cache_hit") or row.get("status") == "cache_reuse":
            cache += 1
            continue
        counted += 1
        purposes[str(row.get("purpose", row.get("budget_category", "unspecified")))] += 1
        invocation = row.get("invocation_id")
        if invocation:
            invocations.add(str(invocation))
        billing_unit = str(invocation) if invocation and row.get("billing_scope") == "invocation" else f"attempt-record:{i}"
        charge_already_counted = billing_unit in billed_units
        billed_units.add(billing_unit)
        value = _number(row.get("cost_usd", row.get("actual_cost_usd")))
        if value is None and not charge_already_counted:
            unknown += 1
        elif value is not None and not charge_already_counted:
            known += value
        estimate = _number(row.get("list_price_estimate_usd", row.get("estimated_cost_usd")))
        if estimate is None and not charge_already_counted:
            list_price_unknown += 1
        elif estimate is not None and not charge_already_counted:
            list_price += estimate
        if charge_already_counted:
            pass
        elif value is not None:
            grounded_cost += value
        elif estimate is not None:
            grounded_cost += estimate
        else:
            grounded_unknown += 1
        duration = _number(row.get("latency_s", row.get("elapsed_seconds", row.get("latency_seconds"))))
        if duration is None:
            latency_unknown += 1
        else:
            latency += duration
        usage = row.get("usage") or {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = _number(usage.get(key))
            if value is not None:
                tokens[key] += value
    return {
        "attempts_incurred": counted, "cache_reuses": cache,
        "known_charges_usd": known, "unknown_charge_attempts": unknown,
        "actual_total_usd": known if not unknown else None,
        "known_list_price_estimate_usd": list_price,
        "unknown_list_price_attempts": list_price_unknown,
        "list_price_basis": "Provider usage × documented list price where supplied; not invoiced charges.",
        "actual_or_list_price_basis_usd": grounded_cost,
        "unpriced_calls_after_list_price": grounded_unknown,
        "known_serial_latency_seconds": latency,
        "unknown_latency_attempts": latency_unknown,
        "reported_usage": dict(tokens),
        "usage_note": "Reported provider usage only; not inferred billed tokens. Missing records are unknown.",
        "statuses": dict(status), "execution_routes": dict(routes),
        "budget_purposes": dict(purposes),
        "recorded_distinct_invocations": len(invocations),
        "charge_units": len(billed_units),
        "charge_unit_note": "Shared native invocation is one cost unit when invocation_id and billing_scope=invocation are recorded; assessment records remain separate.",
    }


def _attempts_for(rows: list[dict], attempts: list[dict]) -> tuple[list[dict], list[str]]:
    wanted = {rid for row in rows for rid in row.get("request_ids", [])}
    selected = [a for a in attempts if a.get("request_id", a.get("assessment_id")) in wanted]
    observed = {a.get("request_id", a.get("assessment_id")) for a in selected}
    return selected, sorted(wanted - observed)


def economic_report(cases: list[dict], decisions: list[dict], attempts: list[dict] | None = None,
                    scenarios: dict | None = None, probes: list[dict] | None = None) -> dict:
    """Paired outcome-loss comparisons and explicit price sensitivity.

    Only comparable observed case actions enter the paired benefit numerator.
    Policy deployment cost uses its request dependency set, while collection cost
    counts every incurred attempt once across all arms. This is static decision
    replay unless the caller supplies separately verified end-to-end outcomes.
    """
    attempts = attempts or []
    probes = probes or []
    scenarios = scenarios or SCENARIOS
    by_case = {c["id"]: c for c in cases}
    by_policy: dict[str, list[dict]] = defaultdict(list)
    for row in decisions:
        by_policy[row["policy"]].append(row)
    baseline = {r["case_id"]: r for r in by_policy.get("P0", [])}
    report: dict = {
        "scenarios": scenarios, "collection": accounting(attempts),
        "scope": "Within-case action loss under analyst scenarios, not measured business benefit.",
        "policies": {},
        "learning": {
            "monetized_value_usd": None,
            "status": "unverified_without_transfer_evidence",
            "required_evidence": "Frozen learning change tested on distinct later incident groups with costs and a reuse horizon.",
            "note": "A discovered error or correlated model agreement is not measured reusable learning.",
        },
        "unpriced_components": ["development and candidate construction", "human review effort when unmeasured",
                                "source retrieval", "queue time when unreported", "future implementation effort"],
    }
    for name, rows in sorted(by_policy.items()):
        selected, missing = _attempts_for(rows, attempts)
        cost = accounting(selected)
        # Missing linked records are unknown calls, not free calls.
        unknown_calls = cost["unknown_charge_attempts"] + len(missing)
        residual_unpriced = cost["unpriced_calls_after_list_price"] + len(missing)
        probe_ids = {pid for row in rows for pid in row.get("probe_ids", [])}
        selected_probes = [p for p in probes if p.get("record_id", p.get("id", p.get("probe_id"))) in probe_ids]
        observed_probes = {p.get("record_id", p.get("id", p.get("probe_id"))) for p in selected_probes}
        missing_probes = sorted(probe_ids - observed_probes)
        durations = [_number(p.get("elapsed_seconds")) for p in selected_probes]
        seconds = sum(d for d in durations if d is not None)
        unknown_durations = sum(d is None for d in durations) + len(missing_probes)
        value = {"deployment_model_cost": cost, "missing_request_cost_records": missing,
                 "missing_probe_records": missing_probes,
                 "probe_seconds_measured": seconds, "probe_durations_unknown": unknown_durations,
                 "scenario_results": {}}
        for scenario_name, scenario in scenarios.items():
            before_sum = after_sum = 0.0
            paired = 0
            missing_actions = 0
            changes = 0
            corrected = damaged = 0
            for row in rows:
                case = by_case[row["case_id"]]
                old = baseline.get(row["case_id"])
                if old is None or row.get("status") != "ok" or old.get("status") != "ok":
                    missing_actions += 1
                    continue
                before = loss(case, old.get("action"), scenario)
                after = loss(case, row.get("action"), scenario)
                if before is None or after is None:
                    missing_actions += 1
                    continue
                paired += 1
                before_sum += before
                after_sum += after
                changes += row.get("action") != old.get("action")
                corrected += before > after
                damaged += after > before
            gross = before_sum - after_sum
            known_net = gross - cost["known_charges_usd"]
            sensitivity = [
                {"assumed_unknown_call_usd": p, "assumed_probe_second_usd": s,
                 "net_value_usd": known_net - unknown_calls * p - seconds * s}
                for p in scenario["unknown_model_call_usd"] for s in scenario["probe_second_usd"]
            ]
            value["scenario_results"][scenario_name] = {
                "status": "observed_action_replay" if paired else "unverified_no_paired_actions",
                "paired_cases": paired, "excluded_missing_actions": missing_actions,
                "action_changes": changes, "lower_loss_actions": corrected, "higher_loss_actions": damaged,
                "baseline_outcome_loss_usd": before_sum, "policy_outcome_loss_usd": after_sum,
                "gross_loss_reduction_usd": gross,
                "net_value_at_known_charges_only_usd": known_net,
                "net_value_usd": None,
                "maximum_total_incremental_cost_for_break_even_usd": gross,
                "break_even_per_unknown_call_usd_excluding_other_unpriced_costs":
                    known_net / unknown_calls if unknown_calls else None,
                "sensitivity": sensitivity,
                "list_price_grounded_sensitivity": [
                    {"actual_or_jev_usage_list_price_basis_usd": cost["actual_or_list_price_basis_usd"],
                     "remaining_unpriced_calls": residual_unpriced,
                     "assumed_remaining_call_usd": p, "assumed_probe_second_usd": s,
                     "net_before_unmeasured_review_and_other_costs_usd":
                         gross - cost["actual_or_list_price_basis_usd"] - residual_unpriced * p - seconds * s}
                    for p in scenario["unknown_model_call_usd"] for s in scenario["probe_second_usd"]
                ],
                "human_review_sensitivity": [
                    {"assumed_total_minutes": minutes, "assumed_usd_per_minute": price,
                     "value_after_known_charges_and_assumed_review_usd": known_net - minutes * price}
                    for minutes in (0, 1, 5, 15, 60) for price in scenario["human_minute_usd"]
                ],
                "break_even_review_minutes_before_other_unpriced_costs": {
                    str(price): known_net / price for price in scenario["human_minute_usd"] if price > 0
                },
                "interpretation": "Unpriced human effort, delay, and probes can lower net value. Negative break-even means no nonnegative investigation price rescues this scenario.",
            }
            if not paired:
                for field in ("baseline_outcome_loss_usd", "policy_outcome_loss_usd", "gross_loss_reduction_usd",
                              "net_value_at_known_charges_only_usd", "maximum_total_incremental_cost_for_break_even_usd",
                              "break_even_per_unknown_call_usd_excluding_other_unpriced_costs"):
                    value["scenario_results"][scenario_name][field] = None
                value["scenario_results"][scenario_name]["sensitivity"] = []
                value["scenario_results"][scenario_name]["list_price_grounded_sensitivity"] = []
                value["scenario_results"][scenario_name]["human_review_sensitivity"] = []
                value["scenario_results"][scenario_name]["break_even_review_minutes_before_other_unpriced_costs"] = {}
        report["policies"][name] = value
    report["incremental_comparisons"] = {}
    for before_name, after_name in (("P0", "P1"), ("P0", "P0_probe"), ("P0_probe", "P2"), ("P1", "P2"), ("P2", "P3"), ("P3", "P4"),
                                    ("never", "always"), ("random", "P4"), ("risk", "P4"),
                                    ("reasoning_only", "always")):
        before_rows = {r["case_id"]: r for r in by_policy.get(before_name, [])}
        after_rows = {r["case_id"]: r for r in by_policy.get(after_name, [])}
        pairs = sorted(before_rows.keys() & after_rows.keys())
        group = {}
        for split in ["all"] + sorted({c.get("split", "unknown") for c in cases}):
            members = [cid for cid in pairs if split == "all" or by_case[cid].get("split", "unknown") == split]
            left = [before_rows[cid] for cid in members]
            right = [after_rows[cid] for cid in members]
            old_attempts, old_missing = _attempts_for(left, attempts)
            new_attempts, new_missing = _attempts_for(right, attempts)
            old_cost = accounting(old_attempts)
            new_cost = accounting(new_attempts)
            delta_known = new_cost["known_charges_usd"] - old_cost["known_charges_usd"]
            delta_unknown = (new_cost["unknown_charge_attempts"] + len(new_missing)
                             - old_cost["unknown_charge_attempts"] - len(old_missing))
            delta_grounded = new_cost["actual_or_list_price_basis_usd"] - old_cost["actual_or_list_price_basis_usd"]
            delta_unpriced = (new_cost["unpriced_calls_after_list_price"] + len(new_missing)
                              - old_cost["unpriced_calls_after_list_price"] - len(old_missing))
            results = {}
            for scenario_name, scenario in scenarios.items():
                gross = 0.0
                measured = changes = 0
                for cid in members:
                    a, b = before_rows[cid], after_rows[cid]
                    if a.get("status") != "ok" or b.get("status") != "ok":
                        continue
                    old_loss = loss(by_case[cid], a.get("action"), scenario)
                    new_loss = loss(by_case[cid], b.get("action"), scenario)
                    if old_loss is None or new_loss is None:
                        continue
                    gross += old_loss - new_loss
                    measured += 1
                    changes += a.get("action") != b.get("action")
                results[scenario_name] = {
                    "status": "observed_action_replay" if measured else "unverified_no_paired_actions",
                    "paired_observed_actions": measured, "action_changes": changes,
                    "excluded_cases": len(members) - measured,
                    "gross_loss_reduction_usd": gross,
                    "incremental_known_charges_usd": delta_known,
                    "incremental_unknown_charge_calls": delta_unknown,
                    "incremental_known_list_price_estimate_usd": new_cost["known_list_price_estimate_usd"] - old_cost["known_list_price_estimate_usd"],
                    "actual_net_value_usd": None,
                    "maximum_incremental_investigation_cost_for_break_even_usd": gross,
                    "call_price_sensitivity": [
                        {"assumed_unknown_call_usd": p,
                         "net_before_other_unpriced_costs_usd": gross - delta_known - delta_unknown * p}
                        for p in scenario["unknown_model_call_usd"]
                    ],
                    "list_price_grounded_sensitivity": [
                        {"incremental_actual_or_jev_list_price_usd": delta_grounded,
                         "incremental_remaining_unpriced_calls": delta_unpriced,
                         "assumed_remaining_call_usd": p,
                         "net_before_probe_review_and_other_costs_usd": gross - delta_grounded - delta_unpriced * p}
                        for p in scenario["unknown_model_call_usd"]
                    ],
                }
                if not measured:
                    results[scenario_name]["gross_loss_reduction_usd"] = None
                    results[scenario_name]["maximum_incremental_investigation_cost_for_break_even_usd"] = None
                    results[scenario_name]["call_price_sensitivity"] = []
                    results[scenario_name]["list_price_grounded_sensitivity"] = []
            group[split] = {"expected_paired_cases": len(members), "scenario_results": results}
        report["incremental_comparisons"][f"{before_name}->{after_name}"] = group
    return report
