"""Resolve operator-owned dev-lane settings; credentials remain references, never values.

Defaults are generated from configuration.cue. The private team configuration
selects one JSON overlay. Precedence is defaults, project, role, trusted job ID;
objects merge recursively; lists, scope questions and credential selectors replace completely.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .adapters import names as adapter_names
from .adapters import registry as adapter_registry
from .failures import CATEGORIES
from .jev_config import validate_entry, validate_question, validate_questions
from .recovery_actions import PLACEHOLDERS, substitute, validate_templates
from .role_packages import behavior, behavior_ids, catalog
from .store import State

# Public entry point used by the separately versioned acceptance fixture and its
# frozen verifier. Keep the shared read-only implementation, not a second reader.
ConfigurationState = State

SOURCE = Path(__file__).with_name("configuration.cue")
GENERATED = SOURCE.with_suffix(".json")
FIELDS = {"version", "features", "connection", "model", "transport", "scope", "assessment",
          "assignment", "filtering", "guidance", "completion", "decisions", "readiness"}
DECISION_FIELDS = {"noul": {"reject_below", "accept_at"},
                   "choice": {"minimum_probability", "minimum_margin"},
                   "score": {"minimum_confidence", "minimum_margin"}}
REQUIREMENTS = {"objective", "done_when", "constraints"}
BOUNDARIES = {"assignment", "job", "work_order"}
UPDATE_KINDS = {"finding", "progress", "recommendation", "result", "blocker"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value, fields, where):
    if not isinstance(value, dict) or value.keys() != set(fields):
        raise ValueError(f"Invalid {where} fields; expected " + ", ".join(sorted(fields)))
    return value


def _text(value, where, limit=8000):
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value.encode("utf8")) > limit:
        raise ValueError(f"{where} must be bounded nonempty text")
    return value


def _number(value, where, low, high, integer=False):
    if (type(value) not in ({int} if integer else {int, float}) or not math.isfinite(value)
            or not low <= value <= high):
        raise ValueError(f"Invalid {where}; expected {'integer' if integer else 'number'} from {low} to {high}")


def _strings(value, where, allowed=None):
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")
    for item in value:
        _text(item, where)
    if len(set(value)) != len(value) or allowed is not None and set(value) - set(allowed):
        raise ValueError(f"{where} contains duplicate or unknown references")


def _pattern(value, where):
    _text(value, where, 2000)
    try:
        re.compile(value)
    except re.error:
        raise ValueError(f"Invalid regular expression in {where}") from None


def decision_rule(settings, purpose):
    """Return isolated thresholds for a service decision's named purpose."""
    if not isinstance(purpose, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", purpose):
        raise ValueError("Decision purpose must be a bounded identifier")
    policy = settings["decisions"]
    override = policy["rules"].get(purpose, {})
    return {kind: {**policy[kind], **override.get(kind, {})} for kind in DECISION_FIELDS}


def _decision_thresholds(rule, where):
    _object(rule, DECISION_FIELDS, where)
    for kind, fields in DECISION_FIELDS.items():
        for name, value in _object(rule[kind], fields, where + "." + kind).items():
            _number(value, where + "." + kind + "." + name, 0, 1)
    if rule["noul"]["reject_below"] >= rule["noul"]["accept_at"]:
        raise ValueError(where + ".noul.reject_below must be less than accept_at")


def _decisions(settings, roles, *, references):
    policy = _object(settings["decisions"], {*DECISION_FIELDS, "arbitrator_role", "failure_mode",
                                            "unknown_choices", "rules", "reserved_sessions"}, "decisions")
    _number(policy["reserved_sessions"], "decisions.reserved_sessions", 1, 10, True)
    _text(policy["arbitrator_role"], "decisions.arbitrator_role", 80)
    if references and (policy["arbitrator_role"] not in roles or
                       behavior(roles[policy["arbitrator_role"]]["behavior"]).id != "arbitrator"):
        raise ValueError("Decision arbitrator_role must reference a registered Arbitrator role")
    if policy["failure_mode"] not in {"arbitrate", "block"}:
        raise ValueError("Decision failure_mode must be arbitrate or block")
    _strings(policy["unknown_choices"], "decisions.unknown_choices")
    if len(policy["unknown_choices"]) > 32:
        raise ValueError("Decision unknown_choices exceeds its 32-choice budget")
    _decision_thresholds({kind: policy[kind] for kind in DECISION_FIELDS}, "decisions")
    if not isinstance(policy["rules"], dict) or len(policy["rules"]) > 32:
        raise ValueError("Decision rules must be a bounded mapping of purposes to thresholds")
    for purpose, override in policy["rules"].items():
        if not isinstance(override, dict) or set(override) - DECISION_FIELDS.keys():
            raise ValueError("Decision rules may override only Noul, Choice and Score thresholds")
        for kind, thresholds in override.items():
            if not isinstance(thresholds, dict) or set(thresholds) - DECISION_FIELDS[kind]:
                raise ValueError("Decision rules contain unknown threshold fields")
        _decision_thresholds(decision_rule(settings, purpose), "decisions.rules." + purpose)


def validate_connection(value, *, references=True):
    """Validate a provider reference without reading or displaying its credential."""
    _object(value, {"adapter", "endpoint", "credential"}, "connection")
    _text(value["adapter"], "connection.adapter", 80)
    if references and value["adapter"] not in adapter_names("evaluation"):
        raise ValueError("Unknown connection adapter; select an installed evaluation adapter")
    _text(value["endpoint"], "connection.endpoint", 2000)
    try:
        url = urlsplit(value["endpoint"])
        port = url.port
    except ValueError:
        raise ValueError("Invalid connection endpoint") from None
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.query or url.fragment or any(char.isspace() for char in value["endpoint"])
            or port is not None and not 1 <= port <= 65535):
        raise ValueError("Connection endpoint must be an HTTPS URL without credentials, query or fragment")
    credential = value["credential"]
    if not isinstance(credential, dict) or set(credential) not in ({"env"}, {"file"}):
        raise ValueError("Connection credential requires exactly one env or file reference; never supply a key value")
    if "env" in credential:
        if not isinstance(credential["env"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credential["env"]):
            raise ValueError("Connection credential env must name an environment variable")
    else:
        _text(credential["file"], "connection.credential.file", 4096)
    return value


def _validate(value, *, references=True):
    """Validate complete settings, including catalog references and dependent bounds."""
    _object(value, FIELDS, "configuration")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported configuration version")
    readiness = _object(value["readiness"], {"timeout_seconds"}, "readiness")
    _number(readiness["timeout_seconds"], "readiness.timeout_seconds", 1, 300, True)
    roles = catalog()["roles"] if references else {}
    role_adapters = behavior_ids() if references else None
    skills = {skill for package in roles.values()
              for skill in [package["skills"]["shared"], package["skills"]["role"], *package["skills"]["framework"]]}
    features = _object(value["features"], {"advice", "filtering", "guidance", "completion", "selection"}, "features")
    if any(type(enabled) is not bool for enabled in features.values()):
        raise ValueError("Feature switches must be booleans")
    _decisions(value, roles, references=references)
    validate_connection(value["connection"], references=references)
    _text(value["model"], "model", 200)
    bounds = {"timeout": (0.1, 60), "max_attempts": (1, 3), "retry_delay": (0, 5),
              "max_retry_delay": (0, 5), "max_request_bytes": (1000, 48000),
              "max_response_bytes": (1000, 131072)}
    transport = _object(value["transport"], {*bounds, "retry_statuses"}, "transport")
    for name, (low, high) in bounds.items():
        _number(transport[name], "transport." + name, low, high,
                name in {"max_attempts", "max_request_bytes", "max_response_bytes"})
    if not isinstance(transport["retry_statuses"], list):
        raise ValueError("transport.retry_statuses must be a list")
    for status in transport["retry_statuses"]:
        _number(status, "transport.retry_statuses", 400, 599, True)
    if len(set(transport["retry_statuses"])) != len(transport["retry_statuses"]):
        raise ValueError("transport.retry_statuses contains duplicates")
    scope = _object(value["scope"], {"version", "questions"}, "scope")
    _number(scope["version"], "scope.version", 1, 2**31 - 1, True)
    validate_questions(scope["questions"], max_questions=32)
    assessment = _object(value["assessment"], {"max_sources", "max_questions", "source_bytes", "max_source_bytes", "rules"}, "assessment")
    for name, low, high in (("max_sources", 1, 32), ("max_questions", 1, 32), ("source_bytes", 4, 8000), ("max_source_bytes", 4, 8000)):
        _number(assessment[name], "assessment." + name, low, high, True)
    if assessment["max_sources"] * assessment["max_questions"] > 32:
        raise ValueError("Assessment source/question product exceeds the 32-question wire budget")
    if assessment["source_bytes"] > assessment["max_source_bytes"]:
        raise ValueError("Default assessment source_bytes exceeds max_source_bytes")
    _text(assessment["rules"], "assessment.rules")
    assignment = _object(value["assignment"], {"version", "max_work", "max_jobs", "max_profiles", "profile_evidence", "roles", "questions", "unknown_profile", "missing_profile_evidence"}, "assignment")
    if type(assignment["version"]) is not int or assignment["version"] != 1:
        raise ValueError("Unsupported assignment rubric version")
    for name, low, high in (("max_work", 1, 8), ("max_jobs", 1, 1000), ("max_profiles", 1, 31)):
        _number(assignment[name], "assignment." + name, low, high, True)
    if not isinstance(assignment["profile_evidence"], dict):
        raise ValueError("assignment.profile_evidence must map configured profile names to evidence")
    for name, evidence in assignment["profile_evidence"].items():
        _text(name, "assignment profile name", 100)
        _text(evidence, "assignment profile evidence")
    for name in ("unknown_profile", "missing_profile_evidence"):
        _text(assignment[name], "assignment." + name)
    assignable = {name for name, package in roles.items() if behavior(package["behavior"]).id not in {"co", "xo"}}
    _strings(assignment["roles"], "assignment.roles", assignable if references else None)
    questions = _object(assignment["questions"], {"overlap", "parallel", "role", "runtime"}, "assignment.questions")
    for name, question in questions.items():
        if name in {"role", "runtime"}:
            _object(question, {"type", "instructions"}, "assignment.questions." + name)
            if question["type"] != "choice":
                raise ValueError("Role and runtime assignment questions require dynamic choice criteria")
            validate_entry(question["instructions"])
        else:
            validate_question(question)
            if question["type"] != "noul":
                raise ValueError("Overlap and parallel assignment questions require Noul")
    filtering = _object(value["filtering"], {"failure_mode", "failure_message", "candidate_kinds", "protected_pattern", "excluded_roles", "excluded_adapters", "max_facts", "state_bytes", "question"}, "filtering")
    if filtering["failure_mode"] not in {"preserve", "notice"}:
        raise ValueError("Filtering failure_mode must be preserve or notice")
    _text(filtering["failure_message"], "filtering.failure_message")
    _strings(filtering["candidate_kinds"], "filtering.candidate_kinds", UPDATE_KINDS)
    _strings(filtering["excluded_roles"], "filtering.excluded_roles", roles if references else None)
    _strings(filtering["excluded_adapters"], "filtering.excluded_adapters", role_adapters)
    _pattern(filtering["protected_pattern"], "filtering.protected_pattern")
    _number(filtering["max_facts"], "filtering.max_facts", 1, 32, True)
    _number(filtering["state_bytes"], "filtering.state_bytes", 1, 40000, True)
    validate_question(filtering["question"])
    if filtering["question"]["type"] != "noul":
        raise ValueError("Filtering requires a Noul relevance question")
    guidance = _object(value["guidance"], {"failure_mode", "excluded_roles", "excluded_adapters", "diagnostic_field_bytes", "diagnostic_bytes", "delivery_excerpt_bytes", "routes", "actions", "fallback", "recovery_actions", "max_recovery_actions"}, "guidance")
    _number(guidance["max_recovery_actions"], "guidance.max_recovery_actions", 1, 16, True)
    if not isinstance(guidance["recovery_actions"], list) or len(guidance["recovery_actions"]) > 32:
        raise ValueError("Recovery actions must be a bounded list")
    tools = {tool for package in roles.values() for group in package["mcp"].values() for tool in group}
    for entry in guidance["recovery_actions"]:
        _object(entry, {"categories", "reasons", "tool", "arguments", "when"}, "recovery action")
        _strings(entry["categories"], "recovery categories", CATEGORIES)
        _strings(entry["reasons"], "recovery reasons")
        _text(entry["tool"], "recovery tool", 100)
        if references and entry["tool"] not in tools:
            raise ValueError("Recovery references an unknown managed tool")
        _text(entry["when"], "recovery condition", 2000)
        if not isinstance(entry["arguments"], dict) or len(_canonical(entry["arguments"]).encode()) > 4000:
            raise ValueError("Recovery arguments must be a bounded object")
        substitute(entry["arguments"], PLACEHOLDERS)
    if guidance["failure_mode"] not in {"omit", "fallback"}:
        raise ValueError("Guidance failure_mode must be omit or fallback")
    _strings(guidance["excluded_roles"], "guidance.excluded_roles", roles if references else None)
    _strings(guidance["excluded_adapters"], "guidance.excluded_adapters", role_adapters)
    for name, high in (("diagnostic_field_bytes", 8000), ("diagnostic_bytes", 32000), ("delivery_excerpt_bytes", 64000)):
        _number(guidance[name], "guidance." + name, 1, high, True)
    for name in ("routes", "actions"):
        if not isinstance(guidance[name], list):
            raise ValueError("Guidance " + name + " must be a list")
        for row in guidance[name]:
            fields = {"action_pattern", "roles", "adapters", "next_action"}
            if name == "routes":
                fields |= {"diagnostic_pattern", "skill"}
            _object(row, fields, "guidance." + name)
            _pattern(row["action_pattern"], "guidance action pattern")
            _strings(row["roles"], "guidance roles", roles if references else None)
            _strings(row["adapters"], "guidance adapters", role_adapters)
            if name == "routes":
                _pattern(row["diagnostic_pattern"], "guidance diagnostic pattern")
                _text(row["skill"], "guidance skill")
                if references and row["skill"] not in skills:
                    raise ValueError("Guidance route references an unknown installed role skill")
            if row["next_action"] is not None or name == "actions":
                _text(row["next_action"], "guidance next action")
    fallback = _object(guidance["fallback"], {"skill", "next_action"}, "guidance.fallback")
    _text(fallback["skill"], "guidance fallback skill")
    if references and fallback["skill"] != "role" and fallback["skill"] not in skills:
        raise ValueError("Guidance fallback references an unknown installed role skill")
    _text(fallback["next_action"], "guidance fallback next action")
    completion = _object(value["completion"], {"version", "artifact_bytes", "state_bytes", "boundaries", "questions"}, "completion")
    if type(completion["version"]) is not int or completion["version"] != 1:
        raise ValueError("Unsupported completion rubric version")
    _number(completion["artifact_bytes"], "completion.artifact_bytes", 512, 8000, True)
    _number(completion["state_bytes"], "completion.state_bytes", 4000, 20000, True)
    for text in _object(completion["boundaries"], BOUNDARIES, "completion.boundaries").values():
        _text(text, "completion boundary")
    for name, question in _object(completion["questions"], REQUIREMENTS, "completion.questions").items():
        _object(question, {"type", "instructions", "criteria"}, "completion question")
        validate_question(question)
        if question["type"] != "noul":
            raise ValueError("Completion requires independent Noul judgments")
        instructions = _object(question["instructions"], {"requirement", "task", "rules"}, "completion instructions")
        if instructions["requirement"] != name:
            raise ValueError("Completion question must evaluate its named requirement")
        _text(instructions["task"], "completion task")
        _strings(instructions["rules"], "completion rules")
        if not instructions["rules"]:
            raise ValueError("Completion question needs explicit rules")
        for text in _object(question["criteria"], {"true", "false"}, "completion criteria").values():
            _text(text, "completion criterion")
    return value


def validate(value, *, references=True):
    try:
        return _validate(value, references=references)
    except (TypeError, OverflowError, UnicodeError) as error:
        raise ValueError("Invalid configuration value type") from error


def _read(path):
    """Reject duplicate keys instead of silently picking a contradictory declaration."""
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError("Duplicate configuration field: " + name)
            result[name] = value
        return result
    try:
        if path.stat().st_size > 512 * 1024:
            raise ValueError("Configuration exceeds its 512 KiB budget")
        return json.loads(path.read_text(), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite configuration number")))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("The operator must supply a readable valid configuration JSON file") from None


@lru_cache(maxsize=2)
def _defaults(source_hash, generated_hash, registry_hash):
    del generated_hash, registry_hash  # Cache invalidates when either source or generated file changes.
    value = _read(GENERATED)
    _object(value, {"source_sha256", "settings"}, "configuration export")
    if value["source_sha256"] != source_hash:
        raise ValueError("Configuration export is stale; run python -m devtools.configuration --write")
    return validate(value["settings"], references=False)


def defaults():
    """Return isolated defaults; stale CUE exports fail before serving work."""
    try:
        source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        generated_hash = hashlib.sha256(GENERATED.read_bytes()).hexdigest()
    except OSError:
        raise ValueError("Read the maintained configuration CUE/JSON pair before starting services") from None
    registry_hash = hashlib.sha256(_canonical([catalog(), adapter_registry()]).encode()).hexdigest()
    return copy.deepcopy(_defaults(source_hash, generated_hash, registry_hash))


def _merge(base, overlay, where="configuration"):
    if not isinstance(overlay, dict):
        raise ValueError(where + " overlay must be an object")
    result = copy.deepcopy(base)
    for name, value in overlay.items():
        if name not in result and not (where.endswith(".profile_evidence") or ".instructions" in where
                                       or where == "configuration.decisions.rules"
                                       or where.startswith("configuration.decisions.rules.")
                                       or where.endswith(".criteria") or where.endswith("scope.questions")):
            raise ValueError("Unknown configuration field: " + where + "." + name)
        if name == "credential" or name == "questions" and where.endswith(".scope"):
            result[name] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(name), dict):
            result[name] = _merge(result[name], value, where + "." + name)
        else:
            result[name] = copy.deepcopy(value)
    return result


def _paths(settings, base):
    credential = settings["connection"]["credential"]
    if "file" in credential:
        path = Path(credential["file"]).expanduser()
        credential["file"] = os.path.abspath(base / path if not path.is_absolute() else path)
    return settings


def credential_references(directory):
    """Read all declared credential selectors without resolving work or reading keys.

    Capture happens before request scope can be trusted, so every role/job
    override must be filtered. Reuse the configuration path rules without a
    database lookup or full policy validation on each telemetry frame.
    """
    directory = Path(directory)
    team_path = directory / "team.json"
    team = _read(team_path) if team_path.is_file() else {}
    if not isinstance(team, dict) or not isinstance(team.get("project", {}), dict):
        raise ValueError("Team configuration needs a project object")
    default_connection = defaults()["connection"]
    settings = [default_connection]
    project = team.get("project", {})
    configured = project.get("configuration_file")
    if "configuration_file" in project and configured is None:
        raise ValueError("project.configuration_file must select an operator JSON file or be omitted")
    base = directory
    if configured is not None:
        _text(configured, "project.configuration_file", 4096)
        path = Path(configured).expanduser()
        path = (directory / path if not path.is_absolute() else path).resolve()
        overlay = _read(path)
        if not isinstance(overlay, dict):
            raise ValueError("Configuration overlay must be an object")
        variants = [overlay.get("project", {})]
        for group in ("roles", "jobs"):
            rows = overlay.get(group, {})
            if not isinstance(rows, dict):
                raise ValueError("Configuration overrides must be objects")
            variants.extend(rows.values())
        for variant in variants:
            if not isinstance(variant, dict) or not isinstance(variant.get("connection", {}), dict):
                raise ValueError("Configuration connection overrides must be objects")
            if "credential" in variant.get("connection", {}):
                settings.append(variant["connection"])
        base = path.parent
    references = []
    for connection in settings:
        reference = copy.deepcopy(connection["credential"])
        validate_connection({**default_connection, "credential": reference}, references=False)
        reference = _paths({"connection": {"credential": reference}}, base)["connection"]["credential"]
        if reference not in references:
            references.append(reference)
    return references


def resolve(store, role=None, job=None):
    """Resolve trusted binding selectors; callers must establish work ownership first."""
    settings = defaults()
    roles = catalog()["roles"]
    if role is not None and role not in roles:
        raise ValueError("Unknown configuration role")
    if job is not None and (type(job) is not int or job < 1):
        raise ValueError("Configuration job selector must be a trusted positive job ID")
    path = store.directory / "team.json"
    team = _read(path) if path.is_file() else {}
    if not isinstance(team, dict) or not isinstance(team.get("project", {}), dict):
        raise ValueError("Team configuration needs a project object")
    profile_names = None

    def validate_variant(value):
        nonlocal profile_names
        validate(value)
        # Resolve is called after module initialization; defaults/generation stay
        # independent of API imports and their generated-configuration reads.
        from .advice import capabilities as advice_capabilities
        from .api import TOOLS, spec
        from .api import validate as validate_arguments
        from .commands import capabilities as command_capabilities

        effective = {row["name"]: row for row in command_capabilities(spec) + advice_capabilities(spec, value)}
        validate_templates(value["guidance"]["recovery_actions"],
                           [effective.get(row["name"], row) for row in TOOLS], validate_arguments)
        referenced = set(value["assignment"]["profile_evidence"])
        if referenced:
            if profile_names is None:
                if type(team.get("runtime", False)) is not bool:
                    raise ValueError("Team runtime mode must be a boolean")
                if team.get("runtime"):
                    from .runtime import load as load_runtime
                    try:
                        runtime, _, _ = load_runtime(store.directory)
                    except (OSError, json.JSONDecodeError) as error:
                        raise ValueError("Profile evidence requires readable runtime policy files") from error
                    profile_names = set(runtime["profiles"])
                else:
                    members = team.get("members", [])
                    if not isinstance(members, list) or any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in members):
                        raise ValueError("Profile evidence requires configured team members")
                    profile_names = {row["name"] for row in members}
            if referenced - profile_names:
                raise ValueError("Assignment profile evidence references unknown configured runtime profiles or fixed members")
        return value

    project = team.get("project", {})
    if "jev" in project:
        raise ValueError("Legacy project.jev is unsupported; migrate it to project.configuration_file and remove project.jev")
    configured = project.get("configuration_file")
    if "configuration_file" in project and configured is None:
        raise ValueError("project.configuration_file must select an operator JSON file or be omitted")
    declared_jobs = set()
    if configured is not None:
        _text(configured, "project.configuration_file", 4096)
        path = Path(configured).expanduser()
        path = (store.directory / path if not path.is_absolute() else path).resolve()
        overlay = _read(path)
        if (not isinstance(overlay, dict) or overlay.keys() - {"version", "project", "roles", "jobs"}
                or type(overlay.get("version")) is not int or overlay["version"] != 1):
            raise ValueError("Configuration overlay needs version 1 and only project, roles and jobs sections")
        role_overrides, job_overrides = overlay.get("roles", {}), overlay.get("jobs", {})
        if not isinstance(role_overrides, dict) or set(role_overrides) - set(roles):
            raise ValueError("Configuration roles reference unknown role packages")
        if not isinstance(job_overrides, dict) or any(not re.fullmatch(r"[1-9][0-9]*", name) for name in job_overrides):
            raise ValueError("Configuration jobs must reference existing positive job IDs")
        declared_jobs = {int(name) for name in job_overrides}
        common = validate_variant(_merge(settings, overlay.get("project", {})))
        by_role = {name: validate_variant(_merge(common, change)) for name, change in role_overrides.items()}
        # Validate every selectable combination, including currently inactive overrides.
        for change in job_overrides.values():
            validate_variant(_merge(common, change))
            for role_settings in by_role.values():
                validate_variant(_merge(role_settings, change))
        settings = by_role.get(role, common)
        if str(job) in job_overrides:
            settings = validate_variant(_merge(settings, job_overrides[str(job)]))
        settings = _paths(settings, path.parent)
    required_jobs = declared_jobs | ({job} if job is not None else set())
    if required_jobs:
        try:
            with store.connect() as db:
                observed = {row[0] for row in db.execute("SELECT id FROM jobs")}
        except sqlite3.OperationalError:
            observed = set()
        if required_jobs - observed:
            raise ValueError("Configuration references jobs absent from this team's retained work")
    validate_variant(settings)
    revision = hashlib.sha256(_canonical([settings, catalog(), adapter_registry()]).encode()).hexdigest()
    return {**settings, "revision": revision, "selection": {"role": role, "job": job}}


def selections(store):
    """Enumerate every configured override combination for operator inspection."""
    resolve(store)  # Validate the entire declaration before exposing selectors.
    team_path = store.directory / "team.json"
    team = _read(team_path) if team_path.is_file() else {}
    configured = team.get("project", {}).get("configuration_file")
    if configured is None:
        return [{"role": None, "job": None}]
    path = Path(configured).expanduser()
    path = (store.directory / path if not path.is_absolute() else path).resolve()
    overlay = _read(path)
    roles = [None, *sorted(overlay.get("roles", {}))]
    jobs = [None, *sorted(int(job) for job in overlay.get("jobs", {}))]
    return [{"role": role, "job": job} for role in roles for job in jobs]


def decision_reservations(settings):
    """Capacity required by every enabled operator role/job decision policy."""
    reserved = {}
    for item in settings:
        if any(item["features"][name] for name in ("selection", "advice", "filtering", "completion")):
            policy = item["decisions"]
            role = policy["arbitrator_role"]
            reserved[role] = max(reserved.get(role, 0), policy["reserved_sessions"])
    return reserved


def arbitration_reservations(store):
    return decision_reservations(resolve(store, **selection) for selection in selections(store))


def generate(cue):
    subprocess.run([cue, "vet", "-c", str(SOURCE)], check=True, capture_output=True, text=True, timeout=30)
    result = subprocess.run([cue, "export", str(SOURCE), "-e", "settings"], check=True, capture_output=True, text=True, timeout=30)
    settings = validate(json.loads(result.stdout))
    return {"source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "settings": settings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--effective", metavar="TEAM_DIRECTORY", help="Show resolved non-secret settings")
    parser.add_argument("--role")
    parser.add_argument("--job", type=int)
    parser.add_argument("--cue", default="cue", help="Maintainer CUE executable")
    args = parser.parse_args()
    try:
        if args.effective:
            from .readiness import config
            value = config(args.effective, role=args.role, job=args.job)
            print(json.dumps(value, indent=2, ensure_ascii=False))
        else:
            value = generate(args.cue)
            if args.write:
                GENERATED.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
                print("Regenerated devtools/configuration.json")
            elif _read(GENERATED) != value:
                raise ValueError("Configuration export differs from CUE; regenerate devtools/configuration.json")
            else:
                print("Configuration CUE source and generated export agree")
    except subprocess.CalledProcessError as error:
        parser.exit(1, error.stderr or str(error))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
