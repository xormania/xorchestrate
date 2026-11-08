"""Finite local calibration; no acceptance thresholds and no product changes."""

import base64
import hashlib
import json
import os
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent_telemetry

import xorchestrate
from xorchestrate.composition import build

ROOT = Path(os.environ.get("TELEMETRY_CALIBRATION_ROOT", Path(__file__).resolve().parents[2])).resolve()
COLLECTOR_PIN = "84229bb7497547d2db91d02a4633ccb3867f740b"


def identity():
    if Path(xorchestrate.__file__).resolve().parent != ROOT / "xorchestrate":
        raise AssertionError("the imported application is not the selected calibration source")
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((ROOT / "xorchestrate").rglob("*.py"))}
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip(),
            "production_files": files,
            "project_files": {"pyproject.toml": hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()},
            "measurement_files": {"docs/telemetry-evidence/measure_overhead.py":
                                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}


def dependency_identity():
    root = Path(agent_telemetry.__file__).resolve().parent
    return {"pin": COLLECTOR_PIN, "version": agent_telemetry.__version__,
            "files": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(root.rglob("*.py"))}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def tree_bytes(directory):
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


class Reject(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).requests += 1
        self.send_response(503)
        self.end_headers()

    def log_message(self, *_):
        pass


SCENARIOS = ("disabled", "local", "large_payload_disabled", "large_payload",
             "concurrent_disabled", "concurrent", "export_failure")


def measure_capture_trials():
    trials = []
    for scenario in SCENARIOS:
        for trial in range(3):
            with tempfile.TemporaryDirectory(prefix="xor-telemetry-calibration-") as temporary:
                directory = Path(temporary)
                config = {"store": {"adapter": "memory"}}
                server = None
                rejected_before = Reject.requests
                enabled = scenario not in ("disabled", "large_payload_disabled", "concurrent_disabled")
                large = scenario in ("large_payload", "large_payload_disabled")
                concurrent = scenario in ("concurrent", "concurrent_disabled")
                if enabled:
                    options = {"runtime_root": str(directory / "corpus"), "project_id": "calibration",
                               "dataset_id": "calibration", "collector_id": "local",
                               "artifact_threshold_bytes": 1024}
                    if scenario == "export_failure":
                        server = ThreadingHTTPServer(("127.0.0.1", 0), Reject)
                        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
                        thread.start()
                        options["operational"] = {"adapter": "otlp",
                            "endpoint": "http://127.0.0.1:" + str(server.server_port),
                            "timeout": 0.05, "max_pending": 64, "max_bytes": 1048576,
                            "drain_timeout": 0.2}
                    config["telemetry"] = {"adapter": "local", "options": options}
                started = time.perf_counter()
                runtime = build(config, directory)
                construction_seconds = time.perf_counter() - started
                app = runtime.application
                if enabled and (app.telemetry is None or app.telemetry.status().get("status") != "ready"):
                    raise AssertionError("enabled calibration source did not start its real collector")
                count = 12 if large else 20
                width = 65536 if large else 32
                completed = []

                def operation(index):
                    goal = str(index) + ":" + "x" * width
                    began = time.perf_counter()
                    result = app.call("create_work", {"goal": goal})["work"]
                    duration = time.perf_counter() - began
                    if result["goal"] != goal:
                        raise AssertionError("invalid workload result")
                    return {"index": index, "duration_seconds": duration}

                tracemalloc.start()
                cpu_start = time.process_time()
                wall_start = time.perf_counter()
                if concurrent:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        completed = list(pool.map(operation, range(count)))
                else:
                    completed = [operation(index) for index in range(count)]
                wall_seconds = time.perf_counter() - wall_start
                cpu_seconds = time.process_time() - cpu_start
                _, peak_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                if len(app.call("list_work", {})["work"]) != count:
                    raise AssertionError("workload did not complete the declared work")
                status = app.call("telemetry_status", {})
                collector_status = app.telemetry.status() if app.telemetry is not None else None
                close_start = time.perf_counter()
                runtime.close()
                close_seconds = time.perf_counter() - close_start
                if server is not None:
                    server.shutdown()
                    server.server_close()
                    thread.join(1)
                    if Reject.requests == rejected_before:
                        raise AssertionError("export-failure calibration did not reach its failing endpoint")
                corpus_bytes = sum(path.stat().st_size for path in (directory / "corpus").rglob("*") if path.is_file())
                trials.append({"scenario": scenario, "trial": trial, "operation": "create_work",
                               "completed_operations": len(completed), "goal_payload_bytes": width,
                               "workers": 4 if concurrent else 1,
                               "construction_seconds": construction_seconds, "wall_seconds": wall_seconds,
                               "cpu_seconds": cpu_seconds, "python_tracemalloc_peak_bytes": peak_bytes,
                               "close_seconds": close_seconds, "corpus_bytes_after_close": corpus_bytes,
                               "operation_samples": completed, "public_status": status,
                               "collector_status": collector_status,
                               "observed_export_rejections": Reject.requests - rejected_before})
    return trials


def measure_read_trial(directory, *, trial=0, count=8, payload_bytes=65536, contention=False):
    """Measure fixed public workflows on a fresh, caller-owned fixture corpus.

    Default manual runs use eight work/results and 64 KiB ASCII result text.
    Tests use two work/results and 2 KiB text. Limits keep this procedure finite;
    they are fixture admission bounds, not product performance requirements.
    """
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("read calibration needs between 1 and 32 work items")
    if type(payload_bytes) is not int or not 1 <= payload_bytes <= 1048576:
        raise ValueError("read calibration payload must be between 1 byte and 1 MiB")
    directory = Path(directory).resolve()
    corpus = directory / "corpus"
    if corpus.exists():
        raise ValueError("read calibration needs a fresh fixture corpus")
    directory.mkdir(parents=True, exist_ok=True)
    config = {"store": {"adapter": "memory"}, "telemetry": {"adapter": "local", "options": {
        "runtime_root": str(corpus), "project_id": "read-calibration", "dataset_id": "read-calibration",
        "collector_id": "local", "artifact_threshold_bytes": 1024}}}
    construction_start = time.perf_counter()
    runtime = build(config, directory)
    construction_seconds = time.perf_counter() - construction_start
    app = runtime.application
    measurements = []
    expected_events = 2 * count
    expected_corpus = 4 * count
    query_limit, page_budget, payload_limit = 3, 16384, 4096

    def measure(operation, phase, perform):
        completed, returned = 0, 0

        def call(arguments):
            nonlocal completed, returned
            result = app.call(operation, arguments)
            completed += 1
            returned += len(canonical(result))
            return result

        tracemalloc.start()
        cpu_start, wall_start = time.process_time(), time.perf_counter()
        try:
            result = perform(call)
            wall = time.perf_counter() - wall_start
            cpu = time.process_time() - cpu_start
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if completed == 0:
            raise AssertionError("a public-operation measurement completed no public calls")
        sample = {"operation": operation, "pass": phase, "completed_operations": completed,
                  "wall_seconds": wall, "cpu_seconds": cpu, "python_tracemalloc_peak_bytes": peak,
                  "returned_bytes": returned, "byte_basis": "canonical-json-public-results",
                  "operations_per_second": completed / wall,
                  "returned_bytes_per_second": returned / wall, "result": result}
        if operation in {"telemetry_snapshot", "telemetry_verify", "telemetry_restore"}:
            sample["bundle_storage_bytes"] = tree_bytes(Path(result["manifest_reference"]["path"]).parent)
        measurements.append(sample)
        return result

    def query(call):
        cursor, cutoff, identities, rows = 0, None, [], []
        for _ in range(128):
            arguments = {"event_type": "application.completed", "after": cursor,
                         "limit": query_limit, "max_bytes": page_budget}
            if cutoff is not None:
                arguments["cutoff"] = cutoff
            page = call(arguments)
            if cutoff is None:
                cutoff = page["cutoff"]
            if page["cutoff"] != cutoff:
                raise AssertionError("query calibration changed its fixed cutoff")
            rows.extend(page["events"])
            identities.extend(row["event_id"] for row in page["events"])
            if page.get("complete"):
                break
            if page["next_after"] <= cursor:
                raise AssertionError("query calibration stopped making progress")
            cursor = page["next_after"]
        else:
            raise AssertionError("query calibration exceeded its finite page bound")
        if len(identities) != expected_events or len(set(identities)) != expected_events or cutoff != expected_corpus:
            raise AssertionError("query calibration did not complete its declared event population")
        # The final seeded action is the final record_result completion. The
        # response body is independently reconstructed and checked below.
        target = rows[-1]["event_id"]
        return {"event_ids": identities, "event_count": len(identities), "cutoff": cutoff, "_target": target}

    try:
        sink = app.telemetry
        if sink is None or sink.status().get("status") != "ready":
            raise AssertionError("read calibration did not start its actual local collector")
        seed_start = time.perf_counter()
        expected_payload = None
        for index in range(count):
            goal = f"calibration-{trial}-{index}"
            work = app.call("create_work", {"goal": goal})["work"]
            if work["goal"] != goal:
                raise AssertionError("read calibration created unexpected fixture work")
            arguments = {"work_id": work["id"], "result": {"trial": trial, "index": index, "text": "x" * payload_bytes}}
            result = app.call("record_result", arguments)
            expected_payload = {"action": "record_result", "arguments": arguments, "result": result}
        seed_seconds = time.perf_counter() - seed_start
        expected_bytes = canonical(expected_payload)
        expected_digest = hashlib.sha256(expected_bytes).hexdigest()
        if sink.status().get("event_count") != expected_corpus or sink.status().get("capture_failures") != 0:
            raise AssertionError("read calibration did not retain every seeded observation")
        validation = {"expected_payload": expected_payload, "expected_payload_sha256": expected_digest,
                      "expected_payload_size_bytes": len(expected_bytes)}

        def bundle(call, arguments, expected_id=None):
            receipt = call(arguments)
            if receipt.get("status") != "complete" or receipt.get("completeness") != "complete":
                raise AssertionError("preservation calibration did not produce a complete bundle")
            if receipt.get("event_count") != expected_corpus or receipt.get("source_cutoff") != expected_corpus:
                raise AssertionError("preservation calibration changed its source population")
            if expected_id is not None and receipt.get("bundle_id") != expected_id:
                raise AssertionError("preservation calibration changed the bundle identity")
            return receipt

        for phase in ("first-pass", "repeat-pass"):
            queried = measure("telemetry_events", phase, query)
            target = queried.pop("_target")
            if validation.get("completed_event_ids", queried["event_ids"]) != queried["event_ids"]:
                raise AssertionError("repeated query calibration changed its source population")
            validation.update(completed_event_ids=queried["event_ids"], payload_event_id=target)

            def payload(call):
                parts, offset = [], 0
                # The fixed payload byte admission bound also bounds range calls.
                for _ in range(2048):
                    part = call({"event_id": target, "offset": offset, "limit": payload_limit})
                    data = base64.b64decode(part["data_base64"], validate=True)
                    if part["offset"] != offset or len(data) > payload_limit:
                        raise AssertionError("payload calibration returned an invalid bounded range")
                    if part["digest"] != expected_digest or part["size_bytes"] != len(expected_bytes):
                        raise AssertionError("payload calibration changed the expected fixture identity")
                    parts.append(data)
                    if part["next_offset"] is None:
                        break
                    if part["next_offset"] != offset + len(data) or not data:
                        raise AssertionError("payload calibration stopped making exact progress")
                    offset = part["next_offset"]
                else:
                    raise AssertionError("payload calibration exceeded its finite range bound")
                reconstructed = b"".join(parts)
                if reconstructed != expected_bytes:
                    raise AssertionError("payload calibration did not retain the exact known fixture")
                return {"event_id": target, "sha256": hashlib.sha256(reconstructed).hexdigest(),
                        "size_bytes": len(reconstructed)}

            measure("telemetry_payload", phase, payload)
            name = phase + "-bundle"
            snapshot = measure("telemetry_snapshot", phase, lambda call: bundle(call, {"destination": name}))
            measure("telemetry_verify", phase, lambda call: bundle(call, {"destination": name}, snapshot["bundle_id"]))
            measure("telemetry_restore", phase, lambda call: bundle(call,
                {"bundle": name, "destination": phase + "-restored"}, snapshot["bundle_id"]))

        if contention:
            # A WAL reader can continue while another connection reserves the
            # writer. Measure that real condition without claiming a wait or
            # manufacturing uncommitted observation rows.
            connection = sqlite3.connect(sink.path, isolation_level=None)
            try:
                connection.execute("BEGIN IMMEDIATE")
                queried = measure("telemetry_events", "writer-reservation", query)
                queried.pop("_target")
                measure("telemetry_snapshot", "writer-reservation",
                        lambda call: bundle(call, {"destination": "writer-reservation-bundle"}))
            finally:
                connection.rollback()
                connection.close()

        collector_status = sink.status()
        report = {"valid": True, "trial": trial, "workload": {"work_count": count, "payload_bytes": payload_bytes,
            "expected_completed_events": expected_events, "expected_corpus_events": expected_corpus,
            "query_limit": query_limit, "query_max_bytes": page_budget, "payload_range_limit": payload_limit},
            "configuration": config, "configuration_identity": collector_status["configuration_identity"],
            "source_database_path": str(sink.path), "artifact_root": str(sink.artifact_root),
            "construction_seconds": construction_seconds, "seed_seconds": seed_seconds,
            "cache_control": "OS page cache is not flushed; first-pass and repeat-pass are order labels, not cold-cache claims",
            "writer_reservation": "BEGIN IMMEDIATE held, no uncommitted rows" if contention else "not measured",
            "measurements": measurements, "validation": validation, "collector_status": collector_status}
    finally:
        close_start = time.perf_counter()
        runtime.close()
        close_seconds = time.perf_counter() - close_start
    report["close_seconds"] = close_seconds
    report["live_corpus_bytes_after_close"] = sum(path.stat().st_size for path in corpus.rglob("*")
        if path.is_file() and not path.is_relative_to(corpus / "exports"))
    report["exports_bytes_after_close"] = tree_bytes(corpus / "exports")
    return report


def main():
    """Run the original capture cases and three bounded public-read trials."""
    destination = Path(os.environ["TELEMETRY_CALIBRATION_OUTPUT"]).resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError("calibration output must be outside the candidate checkout")
    started = datetime.now(timezone.utc).isoformat()
    before, dependency_before = identity(), dependency_identity()
    trials = measure_capture_trials()
    read_trials = []
    for trial in range(3):
        with tempfile.TemporaryDirectory(prefix="xor-telemetry-read-calibration-") as temporary:
            read_trials.append(measure_read_trial(temporary, trial=trial, contention=True))
    after, dependency_after = identity(), dependency_identity()
    capture_summary = {scenario: {"median_seconds_per_operation": statistics.median(
        row["wall_seconds"] / row["completed_operations"] for row in trials if row["scenario"] == scenario),
        "trials": sum(row["scenario"] == scenario for row in trials)} for scenario in SCENARIOS}
    measurements = [sample for trial in read_trials for sample in trial["measurements"]]
    read_summary = {}
    for operation, phase in sorted({(row["operation"], row["pass"]) for row in measurements}):
        samples = [row for row in measurements if row["operation"] == operation and row["pass"] == phase]
        read_summary[operation + "/" + phase] = {
            "trials": len(samples), "median_workflow_wall_seconds": statistics.median(row["wall_seconds"] for row in samples),
            "median_seconds_per_public_call": statistics.median(row["wall_seconds"] / row["completed_operations"] for row in samples),
            "median_workflow_cpu_seconds": statistics.median(row["cpu_seconds"] for row in samples),
            "median_python_tracemalloc_peak_bytes": statistics.median(row["python_tracemalloc_peak_bytes"] for row in samples),
            "median_returned_bytes_per_second": statistics.median(row["returned_bytes_per_second"] for row in samples)}
    unchanged = all(before[key] == after[key] for key in ("production_files", "project_files", "measurement_files"))
    commit_unchanged = before["commit"] == after["commit"] and before["tree"] == after["tree"]
    dependency_unchanged = dependency_before == dependency_after
    report = {"schema_version": 2, "kind": "bounded-local-calibration", "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_before": before, "source_after": after, "source_unchanged": unchanged,
        "commit_tree_unchanged": commit_unchanged, "collector_dependency_before": dependency_before,
        "collector_dependency_after": dependency_after, "collector_dependency_unchanged": dependency_unchanged,
        "valid": unchanged and commit_unchanged and dependency_unchanged,
        "python": sys.version, "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
        "trials": trials, "summary": capture_summary, "read_trials": read_trials, "read_summary": read_summary,
        "limitations": ["Finite fixture calibration, not a performance SLO, regression verdict or production recommendation.",
            "Python tracemalloc measures traced allocations, not total process RSS.",
            "Wall/CPU include public-call response serialization, page/range assembly and workload correctness checks.",
            "Process CPU includes all threads; concurrent Work host processes can influence elapsed time.",
            "First-pass and repeat-pass are fixed ordering labels; OS page cache is uncontrolled and neither is a cold-cache claim.",
            "Each public call opens its configured physical donor connection; repeating a workflow does not retain a warm connection.",
            "The writer-reservation probe holds BEGIN IMMEDIATE without uncommitted rows; it does not assert that WAL readers block.",
            "Returned-byte throughput counts canonical JSON public responses, not decoded payload or copied bundle bytes.",
            "The failed exporter is a local HTTP503 fixture; no authenticated model execution occurs.",
            "The temporary fixture corpora and bundles are removed after each manual trial; raw trial measurements and receipt identities are retained."]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"valid": report["valid"], "source_unchanged": unchanged,
                      "collector_dependency_unchanged": dependency_unchanged,
                      "summary": capture_summary, "read_summary": read_summary}, indent=2))
    if not report["valid"]:
        raise AssertionError("calibration source, dependency or revision identity changed during measurement")
    return report


if __name__ == "__main__":
    main()
