"""Retained Jev evaluations and scoped classification over existing work evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import time
from pathlib import Path

from .artifacts import Artifacts
from .commands import Commands
from .configuration import defaults, resolve
from .control import Control, binding
from .failures import Failure, failure_info
from .jev import Jev, checked_questions
from .jev_config import clean_questions, validate_question
from .role_packages import adapter, require_capability
from .store import canonical, digest, stamp
from .telemetry import clean
from .workspaces import Workspaces, git

VERSION = 4
NOTE = "Retained evaluation of the supplied evidence; service decisions apply configured uncertainty rules."


def configured(store, role=None, job=None):
    return resolve(store, role=role, job=job)["features"]["advice"]


def capabilities(spec, settings=None):
    settings = settings or defaults()
    assessment = settings["assessment"]
    text = {"type": "string", "minLength": 1, "maxLength": 1000}
    name = {"type": "string", "minLength": 1, "maxLength": 80}
    reference = {"type": "string", "pattern": "^(artifact|operation):[1-9][0-9]*$", "maxLength": 80}
    source = {"type": "object", "additionalProperties": False, "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 300}, "reference": reference,
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 4, "maximum": assessment["max_source_bytes"]},
    }}
    entry = {"description": "Bounded TypeSafe EntryType; structured JSON is preserved.",
             "anyOf": [{"type": "string", "maxLength": 6000},
                       {"type": "object", "maxProperties": 512},
                       {"type": "array", "maxItems": 512}, {"type": "null"}]}
    question = {"type": "object", "additionalProperties": False, "required": ["name", "instructions"],
                "properties": {"name": {**name, "pattern": "[A-Za-z][A-Za-z0-9_]*"},
                               "instructions": entry,
                               "type": {"type": "string", "enum": ["noul", "choice", "score"]},
                               "options": {"type": "array", "items": name, "minItems": 2, "maxItems": 16},
                               "levels": {"type": "array", "items": entry, "minItems": 2, "maxItems": 10},
                               "criteria": {"description": "Choice option map, Score level array, or Noul true/false map; each description is an EntryType.",
                                            "anyOf": [{"type": "object", "maxProperties": 16},
                                                      {"type": "array", "minItems": 2, "maxItems": 10},
                                                      {"type": "null"}]}}}
    return [
        spec("work_assess",
             "Classify source ranges or a proposal using typed questions and the current rubric. Ambiguous results are assigned to Arbitrator; returned decisions are resolved values. Work assignment gates run independently inside orchestrate.",
             {"sources": {"type": "array", "maxItems": assessment["max_sources"], "items": source},
              "questions": {"type": "array", "minItems": 1, "maxItems": assessment["max_questions"], "items": question},
              "proposal": {**text, "maxLength": 2000}}, readonly=True),
    ]


def excerpt(data, offset, limit):
    if offset > len(data):
        raise ValueError("Source offset is beyond its size.")
    block = data[offset:offset + limit]
    try:
        text = block.decode("utf8")
    except UnicodeDecodeError as error:
        if error.reason == "unexpected end of data" and error.start:
            block = block[:error.start]
            text = block.decode("utf8")
        else:
            raise ValueError("Assess UTF-8 text at a character boundary; binary sources are not supported.") from None
    if "\0" in text:
        raise ValueError("Assess text sources, not binary files.")
    return text, offset + len(block) if offset + len(block) < len(data) else None


class Advice:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.control = Control(store, member)
        self.artifacts = Artifacts(store, member)

    def settings(self, scope=None):
        scope = scope or self.control.current()
        try:
            return resolve(self.store, role=scope["role"] if scope else None, job=scope["job"] if scope else None)
        except Failure:
            raise
        except ValueError as error:
            raise Failure(str(error), category="invalid_request", reason="invalid_configuration") from None

    def scope(self, xo=False, feature="advice"):
        scope = self.control.current()
        with self.store.connect() as db:
            current = binding(db, self.member)
        if not scope or scope != current or scope["paused"]:
            raise ValueError("Jev assessment requires configured, active current work.")
        if feature and not self.settings(scope)["features"][feature]:
            raise ValueError("This assessment feature is disabled in the active configuration.")
        if adapter(scope["role"]) == "co":
            raise ValueError("CO relays owner context and system outcomes; work assessment belongs to the execution roles.")
        if xo and adapter(scope["role"]) not in {"xo", "nco"}:
            raise ValueError("Assignment selection is available to XO and NCO.")
        if feature == "advice":
            require_capability(scope["role"], "work_assess")
        return scope

    def brief(self):
        value = self.control.work_context(project=False)
        return {key: value[key] for key in ("role", "context_revision", "responsibility", "role_assignment", "context",
                                          "work", "job", "development", "assignment", "delegation") if key in value}

    def evidence(self, reference, offset=0, limit=2000):
        with self.store.connect() as db:
            kind, row = self.control._reference(db, self.control.current(), reference)
        if kind == "artifact":
            result = self.artifacts.read(row["id"], offset=offset, limit=limit)
            if result["encoding"] != "utf8":
                raise ValueError("Assess a UTF-8 evidence artifact.")
            content, next_offset = result["content"], result["next_offset"]
            identity, units = row["sha256"], "bytes"
        elif kind == "operation":
            result = self.control.work_context(reference=reference, offset=offset)
            content = result["content"][:limit]
            next_offset = offset + len(content) if len(result["content"]) > limit else result["next_offset"]
            identity, units = digest([row["state"], row["result"], row["error"], row["updated"]]), "characters"
        else:
            raise ValueError("Choose an artifact or operation reference.")
        return {"reference": reference, "offset": offset, "offset_units": units,
                "next_offset": next_offset, "partial": bool(offset or next_offset is not None),
                "source_sha256": identity, "content": content}

    def file(self, root, revision, source):
        path = Path(source["path"])
        parts = path.parts
        if (path.is_absolute() or not parts or any(p in {"..", ".git", ".serena"}
                or p.startswith(".env") for p in parts)):
            raise ValueError("Choose a relative source path outside Git, provider state and environment files.")
        # Walk using directory descriptors: no symlink, including a changed ancestor, can escape the worktree.
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("Assess regular source files only.")
                offset, limit = source.get("offset", 0), source.get("limit", 2000)
                if offset > before.st_size:
                    raise ValueError("Source offset is beyond its size.")
                stream.seek(offset)
                data = stream.read(limit + 1)
                content, continuation = excerpt(data, 0, limit)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Source changed during assessment; retry the current range.")
                end = offset + len(content.encode())
                next_offset = end if continuation is not None or end < after.st_size else None
        except OSError:
            raise ValueError("Source is missing, unreadable or contains a symlink; inspect the workspace.") from None
        finally:
            os.close(directory)
        return {"path": path.as_posix(), "revision": revision, "offset": offset, "offset_units": "bytes",
                "next_offset": next_offset, "partial": bool(offset or next_offset is not None),
                "range_sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}

    def evaluate(self, purpose, state, questions, sources, policy=None, feature=None):
        feature = feature or {"completion": "completion", "context_relevance": "filtering", "relevance": "filtering"}.get(purpose, "advice")
        scope = self.scope(purpose == "assignment", feature="advice" if feature == "advice" else None)
        policy = policy or self.settings(scope)
        if not policy["features"][feature]:
            raise ValueError("This assessment feature is disabled in the active configuration.")
        self.check_policy(scope, policy)
        model = policy["model"]
        # Reuse the common credential filter before any third-party request or retained result.
        checked_questions(questions)
        from .credentials import credential_values

        try:
            secrets = credential_values(policy["connection"])
        except ValueError as error:
            raise Failure(str(error), category="invalid_request", reason="invalid_credential_configuration") from None
        state = clean(state, secrets=secrets)
        sources = clean(sources, secrets=secrets)
        try:
            questions = clean_questions(questions, secrets=secrets)
        except ValueError as error:
            raise Failure(str(error), category="invalid_request", reason="invalid_question_identifiers") from None
        checked_questions(questions)
        fingerprint = digest([VERSION, scope, model, purpose, state, questions, sources, policy])
        transport = policy["transport"]
        # Cover every allowed attempt/backoff, plus one timeout for retaining the result.
        wait = transport["timeout"] * (transport["max_attempts"] + 1)
        wait += transport["max_retry_delay"] * (transport["max_attempts"] - 1)
        with Workspaces(self.store, self.member).lock("jev-" + fingerprint, timeout=wait):
            return self.retained(purpose, scope, model, state, questions, sources, fingerprint, policy, secrets=secrets)

    def check_policy(self, scope, policy):
        selection = policy["selection"]
        if selection["job"] != scope["job"]:
            if scope["job"] is not None or selection["job"] is None:
                raise ValueError("Assessment policy does not match the bound job.")
            with self.store.connect() as db:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (selection["job"],)).fetchone()
                if not row or row["work_order"] != scope["work_order"] or not Commands._owns_job(db, scope, dict(row)):
                    raise ValueError("Assessment policy job is outside the bound command team.")
        if selection["role"] != scope["role"] or resolve(self.store, **selection)["revision"] != policy["revision"]:
            raise ValueError("Assessment configuration changed; retry using current policy.")

    def retained(self, purpose, scope, model, state, questions, sources, fingerprint, policy, *, secrets=()):
        if self.scope(purpose == "assignment", feature=None) != scope:
            raise ValueError("Assessment work binding changed; retry current work.")
        self.check_policy(scope, policy)
        name = f"jev-{purpose}-{fingerprint}.json"
        with self.store.connect() as db:
            row = db.execute(
                "SELECT a.id FROM artifacts a JOIN work_links l ON l.artifact=a.id "
                "WHERE a.created_by=? AND a.name=? AND l.work_order=? AND l.job IS ? LIMIT 1",
                (self.member, name, scope["work_order"], scope["job"]),
            ).fetchone()
        started = time.monotonic()
        if row:
            result = json.loads(self.artifacts.read(row["id"], limit=256 * 1024)["content"])
            reference = f"artifact:{row['id']}"
        else:
            try:
                response = Jev(policy["transport"], policy["connection"]).evaluate(state, questions, model)
            except ValueError as error:
                failure = failure_info(error)
                outcome = {"evaluation_unavailable": "unavailable", "evaluation_invalid": "invalid_response",
                           "invalid_request": "invalid_request"}.get(failure["category"], "failed")
                self.store.event(self.member, "jev_evaluation", purpose=purpose, fingerprint=fingerprint,
                                 model=model, configuration_revision=policy["revision"], outcome=outcome,
                                 failure=clean(failure, secrets=secrets), error=clean(str(error), secrets=secrets),
                                 seconds=round(time.monotonic() - started, 6))
                raise
            self.check_policy(scope, policy)
            from .credentials import credential_values

            secrets = credential_values(policy["connection"])
            result = {**response, "sources": sources, "questions": questions, "question_version": VERSION,
                      "evaluated_at": stamp(), "fingerprint": fingerprint, "purpose": purpose,
                      "binding": {key: scope[key] for key in ("member", "role", "work_order", "job", "reports_to", "revision", "paused", "updated")},
                      "policy": {**clean(policy, secrets=secrets), "scope": {**policy["scope"],
                                 "questions": clean_questions(policy["scope"]["questions"], secrets=secrets)}},
                      "request": {"model": model, "state": state, "questions": questions}}
            saved = self.artifacts.ingest(name, io.BytesIO(canonical(result).encode()), "application/json",
                                          work_order=scope["work_order"], job=scope["job"])
            reference = "artifact:" + str(saved["artifact"]["id"])
        self.store.event(self.member, "jev_evaluation", purpose=purpose, fingerprint=fingerprint, model=model,
                         outcome="completed", configuration_revision=policy["revision"], cache_hit=bool(row), evidence=reference,
                         usage=None if row else result["usage"], seconds=round(time.monotonic() - started, 6))
        # An in-flight reply never supplies stale advice as if it were for a replacement binding.
        if self.scope(purpose == "assignment", feature=None) != scope:
            raise ValueError("Assessment work binding changed; retry current work.")
        self.check_policy(scope, policy)
        for key in ("questions", "request", "binding", "policy"):
            result.pop(key, None)  # Full replay input stays in evidence, not routine agent context.
        return {**result, "evidence": reference, "cache_hit": bool(row), "note": NOTE}

    def assess(self, sources=None, questions=None, proposal=None):
        scope = self.scope()
        policy = self.settings(scope)
        settings = policy["assessment"]
        sources, questions = sources or [], questions or []
        if len(sources) > settings["max_sources"] or len(questions) > settings["max_questions"]:
            raise Failure("Assessment exceeds the configured source/question limits.", category="invalid_request",
                          reason="assessment_budget_exceeded")
        sources = [dict(source, limit=source.get("limit", settings["source_bytes"])) for source in sources]
        if any(type(source["limit"]) is not int or not 4 <= source["limit"] <= settings["max_source_bytes"] for source in sources):
            raise Failure("Assessment source range exceeds configured limits.", category="invalid_request",
                          reason="source_budget_exceeded")
        if proposal is not None and questions or proposal is None and (not sources or not questions):
            raise ValueError("Supply either a proposal for the configured scope rubric, or sources and questions.")
        if len({q["name"] for q in questions}) != len(questions):
            raise ValueError("Question names must be unique.")
        for source in sources:
            if ("path" in source) == ("reference" in source):
                raise ValueError("Supply exactly one path or reference per source.")
        templates = []
        for question in questions:
            kind = question.get("type", "noul")
            aliases = question.keys() & {"options", "levels"}
            expected = {"options"} if kind == "choice" else {"levels"} if kind == "score" else set()
            if aliases and (aliases != expected or "criteria" in question):
                raise ValueError("Choice needs options, score needs levels, and noul needs neither.")
            value = {"type": kind, "instructions": question["instructions"]}
            if "criteria" in question:
                value["criteria"] = question["criteria"]
            elif kind == "choice" and "options" in question:
                if len(set(question["options"])) != len(question["options"]):
                    raise ValueError("Choice options must be unique.")
                value["criteria"] = {option: None for option in question["options"]}
            elif kind == "score" and "levels" in question:
                value["criteria"] = question["levels"]
            validate_question(value)
            if kind == "choice" and not 2 <= len(value["criteria"]) <= 16:
                raise ValueError("Assessment choices need two to sixteen options.")
            templates.append(value)
        material = [None] * len(sources)
        paths = [i for i, source in enumerate(sources) if "path" in source]
        if paths:
            with Workspaces(self.store, self.member).use() as (_, workspace):
                revision = git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip()
                for i in paths:
                    material[i] = self.file(workspace["root"], revision, sources[i])
        for i, source in enumerate(sources):
            if "reference" in source:
                material[i] = self.evidence(**source)
        prompts = {}
        for i in range(len(material)):
            for j, template in enumerate(templates):
                rules = settings["rules"]
                instructions = template["instructions"]
                scoped = (f"Evaluate ONLY sources[{i}].content against this question: {instructions} " + rules
                          if isinstance(instructions, str) else
                          {"question": instructions, "evaluate_only": f"sources[{i}].content", "rules": rules})
                prompts[f"s{i}_q{j}"] = {**template, "instructions": scoped}
        metadata = [{k: v for k, v in item.items() if k != "content"} for item in material]
        from .decisions import Decisions

        if proposal is not None:
            return Decisions(self.store, self.member).select("scope", {"brief": self.brief(), "proposal": proposal, "sources": material},
                                 policy["scope"]["questions"], metadata, policy=policy)
        result = Decisions(self.store, self.member).select("assessment", {"brief": self.brief(), "sources": material}, prompts, metadata, policy=policy)
        answers = result.pop("answers")
        result["results"] = [{"source": meta, "answers": {
            q["name"]: answers[f"s{i}_q{j}"] for j, q in enumerate(questions) if f"s{i}_q{j}" in answers
        }, "decisions": {q["name"]: result["decisions"][f"s{i}_q{j}"] for j, q in enumerate(questions)}}
            for i, meta in enumerate(metadata)]
        result.pop("sources", None)
        return result

    def runtimes(self, roles, settings=None):
        from .launch import team_config
        from .runtime import load, resolve

        config = team_config(self.store.directory)
        settings = settings or self.settings()["assignment"]
        evidence = settings["profile_evidence"]
        result = {}
        if config.get("runtime"):
            policy, _, revision = load(self.store.directory)
            for role in roles:
                profiles = {}
                for name in policy["roles"].get(role, []):
                    try:
                        row = resolve(self.store.directory, role, profile=name)
                    except ValueError:
                        continue
                    profiles[name] = {k: row.get(k) for k in ("harness", "model", "reasoning", "provider")}
                    profiles[name]["evidence"] = evidence.get(name, settings["missing_profile_evidence"])
                if len(profiles) > settings["max_profiles"]:
                    raise Failure("Configured runtime profiles exceed the assessment candidate budget.",
                                  category="invalid_request", reason="candidate_budget_exceeded")
                result[role] = {"profiles": profiles, "policy_revision": revision,
                                "configured_default": next(iter(profiles), None)}
        else:
            with self.store.connect() as db:
                bound = {r[0] for r in db.execute("SELECT member FROM member_bindings")}
            profiles = {}
            for member in config["members"]:
                observed = self.store.member(member["name"])
                if (member["name"] in bound or observed["pid"] or observed["state"] not in {"stopped", "failed"}
                        or member["harness"] != "external" and not shutil.which(member.get("binary", member["harness"]))):
                    continue
                profiles[member["name"]] = {k: member.get(k) for k in ("harness", "model", "reasoning")}
                profiles[member["name"]]["evidence"] = evidence.get(member["name"], settings["missing_profile_evidence"])
            if len(profiles) > settings["max_profiles"]:
                raise Failure("Available collaborators exceed the configured assessment candidate budget.",
                              category="invalid_request", reason="candidate_budget_exceeded")
            result = {role: {"profiles": profiles, "configured_default": next(iter(profiles), None)} for role in roles}
        return result
