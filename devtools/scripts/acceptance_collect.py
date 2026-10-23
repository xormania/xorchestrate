"""Collect bounded, filtered observations from an existing frozen acceptance run."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_CELL = 131072
MAX_ARTIFACT = 2 * 1024 * 1024
MAX_REPORT = 4 * 1024 * 1024


def regular_file(path, limit):
    """Read a bounded regular file without following a substituted leaf symlink."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Diagnostic source is not a bounded regular file.")
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Diagnostic source exceeded its size limit.")
        return content


def inside(root, path):
    return path.resolve() == path and path.is_relative_to(root)


def load_run(root):
    root = Path(root).expanduser().resolve(strict=True)
    if root.name == "run.json":
        root = root.parent
    runner_path = root / "verifier/acceptance/run.py"
    if not inside(root, runner_path):
        raise ValueError("The frozen acceptance runner must stay inside its run.")
    regular_file(runner_path, 1024 * 1024)
    regular_file(root / "run.json", MAX_CELL)
    spec = importlib.util.spec_from_file_location("acceptance_frozen_collector", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    manifest = runner.load_run(root)
    # The frozen runner validates manifest paths and the recorded runtime's
    # commit, tree and clean checkout before importing its read-only services.
    api = runner.run_runtime(manifest)
    return manifest, api


def bounded(value, warnings, limit=12000):
    if isinstance(value, str) and len(value) > limit:
        warnings.add("Long diagnostic fields were truncated after credential filtering.")
        return value[:limit] + " [truncated]"
    if isinstance(value, dict):
        return {key: bounded(item, warnings, limit) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > 100:
            warnings.add("Long diagnostic lists were limited to 100 entries.")
        return [bounded(item, warnings, limit) for item in value[:100]]
    return value


def rows(db, table, fields, limit, warnings, *, where="", arguments=()):
    columns = {row["name"] for row in db.execute("PRAGMA table_info(" + table + ")")}
    if not columns:
        warnings.add("Unavailable table: " + table)
        return []
    fields = [field for field in fields.split() if field in columns]
    select = ",".join(f"CASE WHEN length(CAST({field} AS BLOB))>{MAX_CELL} "
                      f"THEN '[omitted: size limit]' ELSE {field} END AS {field}" for field in fields)
    order = "id" if "id" in columns else fields[0]
    result = [dict(row) for row in db.execute(
        f"SELECT {select} FROM {table} {where} ORDER BY {order} DESC LIMIT ?", (*arguments, limit + 1)
    )]
    if len(result) > limit:
        warnings.add("Only the most recent " + str(limit) + " records from " + table + " were included.")
    for row in result[:limit]:
        for key, value in row.items():
            if value == "[omitted: size limit]":
                warnings.add("Oversized database fields were omitted.")
            if key in {"data", "result", "arguments", "detail"} and isinstance(value, str):
                try:
                    row[key] = json.loads(value)
                except ValueError:
                    pass
    return result[:limit]


def failed_artifacts(value):
    if isinstance(value, dict):
        reference = value.get("evidence")
        if value.get("exit_code") not in (None, 0) and isinstance(reference, str):
            match = re.fullmatch(r"artifact:([1-9][0-9]*)", reference)
            if match:
                yield int(match[1])
        for item in value.values():
            yield from failed_artifacts(item)
    elif isinstance(value, list):
        for item in value:
            yield from failed_artifacts(item)


def artifacts(db, state, operations, warnings):
    result, selected = [], set()
    for operation in operations:
        for ident in failed_artifacts(operation.get("result")):
            selected.add((ident, operation.get("work_order")))
    if len(selected) > 8:
        warnings.add("Only eight failed-check artifacts were included.")
    for ident, order in sorted(selected, key=lambda pair: pair[0], reverse=True)[:8]:
        row = db.execute(
            "SELECT a.id,a.name,a.media_type,a.size,a.sha256,a.location FROM artifacts a "
            "WHERE a.id=? AND EXISTS (SELECT 1 FROM work_links l WHERE l.artifact=a.id AND l.work_order=?)",
            (ident, order),
        ).fetchone()
        if row is None:
            warnings.add("A failed-check artifact was missing or outside its operation's work order.")
            continue
        path = state / row["location"]
        try:
            if not inside(state / "artifacts", path) or row["size"] > MAX_ARTIFACT:
                raise ValueError("Unsafe or oversized artifact")
            content = regular_file(path, MAX_ARTIFACT)
            if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError("Artifact integrity mismatch")
            text = content.decode("utf-8")
            try:
                content = json.loads(text)
            except ValueError:
                content = text
            result.append({"reference": "artifact:" + str(ident), "name": row["name"], "content": content})
        except (OSError, ValueError, UnicodeError):
            warnings.add("A failed-check artifact could not be safely read; no raw bytes were exported.")
    return result


def telemetry(state, members, warnings):
    result = []
    for member in members[:8]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", member):
            continue
        path = state / "telemetry" / (member + ".jsonl")
        if not path.exists():
            continue
        try:
            if not inside(state / "telemetry", path):
                raise ValueError("Unsafe telemetry path")
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Not a regular telemetry file")
                head = stream.read(512 * 1024)
                blocks = [head]
                if info.st_size > len(head):
                    stream.seek(max(len(head), info.st_size - 512 * 1024))
                    blocks.append(stream.read(512 * 1024))
                    warnings.add("Telemetry was sampled from bounded file beginnings and endings.")
            observations = []
            for block in blocks:
                for line in block.splitlines():
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    data = row.get("data", {})
                    if not isinstance(data, dict):
                        continue
                    observation = None
                    if row.get("kind") == "console_closed":
                        observation = {key: data.get(key) for key in ("exit_code", "error")}
                    elif row.get("kind") == "process_started":
                        observation = {key: data.get(key) for key in ("pid", "transport", "component")}
                    elif ((row.get("kind") == "mcp_in" and data.get("method") == "tools/list")
                          or (row.get("kind") == "mcp_out" and data.get("method") == "notifications/tools/list_changed")):
                        observation = {"method": data["method"]}
                    elif row.get("kind") == "mcp_out" and isinstance(data.get("result"), dict):
                        catalogue = data["result"].get("tools")
                        if isinstance(catalogue, list):
                            observation = {"discovered_tools": [item.get("name") for item in catalogue
                                                                if isinstance(item, dict)][:100]}
                    if observation is not None:
                        observations.append({"member": member, "kind": row.get("kind"), "time": row.get("time"),
                                             "connection": row.get("connection"), **observation})
            result.extend(observations[-20:])
        except (OSError, ValueError):
            warnings.add("A telemetry source could not be safely read.")
    return result


def collect(manifest, api):
    state = Path(manifest["state"])
    warnings = set()
    report = {"version": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
              "run": {key: manifest.get(key) for key in
                      ("run_id", "status", "run_root", "agentscient_sha", "baseline_sha", "selected_harnesses", "gates")},
              "interpretation": "Diagnostic observations only; this does not certify acceptance."}
    if (state / "live.sqlite3").exists():
        store = api.store.State(state)
        with store.connect() as db:
            db.execute("BEGIN")
            report["schema_version"] = db.execute("PRAGMA user_version").fetchone()[0]
            orders = rows(db, "work_orders", "id title status blocker updated", 8, warnings)
            report["work_orders"] = orders
            report["members"] = rows(db, "members", "name harness state desired native_id generation updated detail", 40, warnings)
            bindings = rows(db, "member_bindings", "member role work_order job reports_to", 40, warnings)
            report["bindings"] = bindings
            report["messages"] = rows(db, "messages", "id sender recipient body work_order job created delivery error", 50, warnings)
            report["operations"] = rows(db, "operations", "id member work_order job action state result error updated", 40, warnings)
            report["failed_checks"] = rows(
                db, "operations", "id member work_order job action state result error updated", 10, warnings,
                where="WHERE action='work_check' AND (state='failed' OR "
                      "CASE WHEN json_valid(result) THEN json_extract(result,'$.passed')=0 ELSE 0 END)",
            )
            report["events"] = rows(db, "events", "id at actor kind data work_order job", 100, warnings)
            candidates = {operation["id"]: operation for operation in report["operations"] + report["failed_checks"]}
            report["failed_check_artifacts"] = artifacts(db, state, candidates.values(), warnings)
            event_count, event_bytes = db.execute(
                "SELECT count(*),coalesce(sum(length(CAST(data AS BLOB))),0) FROM events"
            ).fetchone()
        if event_count <= 10000 and event_bytes <= MAX_ARTIFACT:
            # Detailed runtime records are already sampled above; retain the
            # existing exporter's compact aggregates without repeating traces.
            report["summaries"] = [
                {key: value for key, value in api.readiness.evidence(state, order["id"]).items()
                 if key not in {"runtime_records", "telemetry_files"}}
                for order in orders
            ]
        else:
            warnings.add("Full runtime summaries were omitted because the event census exceeds its count or byte limit.")
        leaders = []
        for binding in bindings:
            if api.role_packages.adapter(binding["role"]) in {"co", "xo"}:
                leaders.append(binding["member"])
        report["console_observations"] = telemetry(state, leaders, warnings)
    else:
        warnings.add("No runtime database exists; only preparation metadata was collected.")
    filtered = api.telemetry.clean_for_state(state, report)
    if isinstance(filtered, dict) and filtered.get("capture") == "suppressed":
        raise ValueError("Credential filtering is unavailable; no diagnostic report was written.")
    filtered = bounded(filtered, warnings)
    filtered["warnings"] = sorted(warnings)
    return filtered


def write_report(manifest, report, output=None):
    destination = Path(output).expanduser() if output else (
        Path.home() / ("agentscient-diagnostics-" + manifest["run_id"] + "-" + uuid.uuid4().hex[:12] + ".json")
    )
    destination = destination.parent.resolve(strict=True) / destination.name
    for key in ("run_root", "agentscient_root", "state"):
        if destination.is_relative_to(Path(manifest[key]).resolve()):
            raise ValueError("Diagnostic output must be outside the frozen run and runtime.")
    content = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    if len(content) > MAX_REPORT:
        raise ValueError("Diagnostic report exceeds its output limit; no report was written.")
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(content)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        manifest, api = load_run(args.run_dir)
        destination = write_report(manifest, collect(manifest, api), args.output)
        print("Acceptance diagnostics: " + str(destination))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, ImportError, sqlite3.Error):
        print("Acceptance: diagnostics could not be safely collected; no unfiltered report was written.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
