#!/usr/bin/env python3
"""Construct and validate dev-lane dispatch records."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path

POLICY_SCHEMA = "agentscient.lane-policy/v1"

FIELDS = (
    "id", "lane", "stage", "unit", "lineage", "follows", "answers", "job",
    "role", "dispatched_by", "at", "snapshot", "harness", "model",
    "session", "brief", "caps", "overrides", "attempts", "result", "status",
)


class RecordError(ValueError):
    pass


def build(payload=None, **values):
    source = dict(payload or {})
    source.update(values)
    source["lane"] = "dev"
    source.setdefault("follows", [])
    source.setdefault("answers", [])
    lineage = source.get("lineage") or {}
    if not str(source.get("unit") or "").strip():
        source["unit"] = lineage.get("branch", "")
    harness = copy.deepcopy(source.get("harness"))
    if isinstance(harness, dict):
        isolation = harness.get("isolation")
        if isinstance(isolation, dict):
            isolation.setdefault(
                "observed", {"unresolved": "behavioural probe has not run"})
        source["harness"] = harness
    if source.get("status") == "launched":
        source["result"] = None
    result = {field: copy.deepcopy(source.get(field)) for field in FIELDS}
    return validate(result)


def _need(condition, field, detail="is invalid"):
    if not condition:
        raise RecordError(f"record field {field} {detail}")


def _policy_match(value, pattern, field, detail):
    _need(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
          field, detail)


def _policy_receipt(value, field):
    _need(isinstance(value, dict), field, "must be an object")
    _policy_match(value.get("record"), r"D-[A-Z0-9]+-[0-9]+",
                  f"{field}.record", "must be a decision id")
    _policy_match(value.get("sha256"), r"[0-9a-f]{64}",
                  f"{field}.sha256", "must be a lowercase SHA-256 digest")


def _validate_policy(brief):
    """Check binding structure only, without admitting policy or looking it up."""
    if not isinstance(brief, dict) or "policy" not in brief:
        return
    policy = brief["policy"]
    _need(isinstance(policy, dict), "brief.policy", "must be an object")
    for key in ("schema", "path", "ref", "sha256", "admission", "profile",
                "template_sha256", "grants_sha256", "maintenance"):
        _need(key in policy, f"brief.policy.{key}", "is missing")
    _need(policy["schema"] == POLICY_SCHEMA,
          "brief.policy.schema", f"must be {POLICY_SCHEMA}")
    _need(policy["path"] == "ops/lane-policy.json",
          "brief.policy.path", "must be ops/lane-policy.json")
    _policy_match(policy["ref"], r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
                  "brief.policy.ref", "must be a full immutable Git object id")
    for key in ("sha256", "template_sha256", "grants_sha256"):
        _policy_match(policy[key], r"[0-9a-f]{64}", f"brief.policy.{key}",
                      "must be a lowercase SHA-256 digest")
    _policy_receipt(policy["admission"], "brief.policy.admission")
    _policy_match(policy["profile"], r"[a-z][a-z0-9-]*",
                  "brief.policy.profile", "must be a syntactically valid profile id")
    template = brief.get("template")
    _need(isinstance(template, dict)
          and policy["template_sha256"] == template.get("sha256"),
          "brief.policy.template_sha256", "must equal brief.template.sha256")
    maintenance = policy["maintenance"]
    if maintenance is None:
        return
    _need(isinstance(maintenance, dict)
          and set(maintenance) == {"standing", "per_job"},
          "brief.policy.maintenance", "must contain exactly standing and per_job")
    for key in ("standing", "per_job"):
        field = f"brief.policy.maintenance.{key}"
        rows = maintenance[key]
        _need(isinstance(rows, list), field, "must be a list")
        for index, row in enumerate(rows):
            _policy_receipt(row, f"{field}[{index}]")
        ids = [row["record"] for row in rows]
        _need(ids == sorted(set(ids)), field, "must be sorted by unique record id")


def validate_context(rec, *, expected_id=None, expected_snapshot=None):
    _need(isinstance(rec, dict), "record", "must be an object")
    ident = rec.get("id")
    _need(isinstance(ident, str) and bool(ident)
          and ident not in {".", ".."}
          and "/" not in ident and "\\" not in ident and "\0" not in ident,
          "id", "must be one path component")
    if expected_id is not None:
        _need(ident == expected_id, "id", f"must equal {expected_id}")
    snapshot = rec.get("snapshot")
    _need(isinstance(snapshot, dict), "snapshot")
    ref_sha = snapshot.get("ref_sha")
    _need(isinstance(ref_sha, str) and re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", ref_sha) is not None,
        "snapshot.ref_sha", "must be a full immutable Git object id")
    snapshot_root = snapshot.get("root")
    _need(isinstance(snapshot_root, str) and Path(snapshot_root).is_absolute(),
          "snapshot.root", "must be an absolute path")
    if expected_snapshot is not None:
        expected = Path(os.path.abspath(expected_snapshot))
        _need(expected.resolve(strict=False) == expected,
              "expected_snapshot", "must be a canonical path")
        _need(Path(snapshot_root).resolve(strict=False) == expected,
              "snapshot.root", f"must resolve to {expected}")
    return rec


def validate(rec, *, expected_id=None, expected_snapshot=None):
    _need(isinstance(rec, dict), "record", "must be an object")
    for field in FIELDS:
        if field != "answers":
            _need(field in rec, field, "is missing")
    _need(rec["lane"] == "dev", "lane", "must be dev")
    _need(rec["status"] in {"launched", "closed", "died"}, "status")
    _need(rec["role"] in {"read", "write"}, "role")
    _need(isinstance(rec["follows"], list), "follows", "must be a list")
    if "answers" in rec:
        _need(isinstance(rec["answers"], list), "answers", "must be a list")
        for index, answer in enumerate(rec["answers"]):
            field = f"answers[{index}]"
            _need(isinstance(answer, dict), field, "must be an object")
            for key in ("key", "disposition", "disposed_at", "citation"):
                _need(key in answer, field, f"must carry {key}")
            _need(isinstance(answer["key"], str) and re.fullmatch(
                r"\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}#[1-9][0-9]*",
                answer["key"],
            ) is not None, field, "must carry a valid key")
            _need(isinstance(answer["disposition"], str)
                  and bool(answer["disposition"].strip()),
                  field, "must carry a disposition")
            _need(isinstance(answer["disposed_at"], str) and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                answer["disposed_at"],
            ) is not None, field, "must carry disposed_at")
            for name in ("citation", "finding_citation"):
                if name not in answer:
                    continue
                citation = answer[name]
                valid_item = (
                    type(citation.get("item")) is int
                    and citation["item"] > 0
                ) if isinstance(citation, dict) else False
                valid_item = valid_item or (
                    isinstance(citation, dict)
                    and isinstance(citation.get("item"), str)
                    and re.fullmatch(r"[1-9][0-9]*", citation["item"])
                    is not None
                )
                _need(valid_item and isinstance(citation.get("path"), str)
                      and bool(citation["path"].strip()),
                      field, f"must carry {name} item and path")
    _need(isinstance(rec["unit"], str) and bool(rec["unit"].strip()), "unit")
    lineage = rec["lineage"]
    _need(isinstance(lineage, dict) and bool(lineage.get("branch"))
          and bool(lineage.get("base_sha")), "lineage")
    snapshot = rec["snapshot"]
    _need(isinstance(snapshot, dict), "snapshot")
    _need(snapshot.get("mode") in {"whole", "fileset"}, "snapshot.mode")
    validate_context(
        rec, expected_id=expected_id, expected_snapshot=expected_snapshot,
    )
    harness = rec["harness"]
    _need(isinstance(harness, dict), "harness")
    _need(harness.get("containment") in {"os", "policy"}, "containment")
    isolation = harness.get("isolation")
    _need(isinstance(isolation, dict), "isolation")
    observed = isolation.get("observed")
    _need(isinstance(observed, dict) and (
        bool(str(observed.get("unresolved") or "").strip()) or
        all(key in observed for key in (
            "operator_config_present", "evidence", "checked_at",
            "harness_version"))), "observed")
    model = rec.get("model")
    _need(isinstance(model, dict), "model")
    effort_argv = model.get("effort_argv")
    _need(isinstance(effort_argv, list), "model.effort_argv")
    effort = model.get("effort_requested")
    if effort:
        carriers = {effort, f'model_reasoning_effort="{effort}"'}
        _need(bool(effort_argv) and any(token in carriers for token in effort_argv),
              "model.effort_argv", "must carry model.effort_requested")
        argv = harness.get("argv")
        _need(isinstance(argv, list), "harness.argv", "must be a list")
        width = len(effort_argv)
        _need(any(argv[index:index + width] == effort_argv
                  for index in range(len(argv) - width + 1)),
              "harness.argv", "must carry model.effort_argv in order")
    else:
        _need(effort_argv == [], "model.effort_argv",
              "must be empty without model.effort_requested")
    if model.get("ran") is None:
        if not (rec.get("note") or model.get("note")):
            model["note"] = "no stream was found; model.ran is null"
    elif not model.get("read_from"):
        _need(model.get("ran") != model.get("requested"), "model.ran",
              "cannot copy requested without a stream")
    if rec["status"] == "launched":
        _need(rec["result"] is None, "result", "must be null while launched")
    result = rec.get("result")
    if isinstance(result, dict) and "collected" in result:
        collected = result["collected"]
        valid = (
            isinstance(collected, dict)
            and all(isinstance(collected.get(key), str)
                    and bool(collected[key].strip())
                    for key in ("at", "by", "head"))
            and isinstance(collected.get("verify"), dict)
            and type(collected["verify"].get("exit")) is int
            and collected["verify"]["exit"] == 0
        )
        _need(valid, "result.collected",
              "must carry at, by, head, and verify.exit equal to 0")
    _validate_policy(rec["brief"])
    return rec
