"""Finite local calibration; no acceptance thresholds and no product changes."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc

from xorchestrate.composition import build
import agent_telemetry


ROOT = Path(os.environ["TELEMETRY_CALIBRATION_ROOT"]).resolve()
DESTINATION = Path(os.environ["TELEMETRY_CALIBRATION_OUTPUT"]).resolve()


def identity():
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((ROOT / "xorchestrate").rglob("*.py"))}
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip(),
            "production_files": files}


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
before = identity()
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
after = identity()
donor_root = Path(agent_telemetry.__file__).parent
report = {"kind": "bounded-local-calibration", "source_before": before, "source_after": after,
          "source_unchanged": before["production_files"] == after["production_files"],
          "commit_tree_unchanged": before["commit"] == after["commit"] and before["tree"] == after["tree"],
          "collector_dependency_files": {str(path.relative_to(donor_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in sorted(donor_root.rglob("*.py"))},
          "python": sys.version, "sqlite": sqlite3.sqlite_version,
          "platform": platform.platform(), "pid": os.getpid(), "trials": trials,
          "summary": {scenario: {"median_seconds_per_operation": statistics.median(
              row["wall_seconds"] / row["completed_operations"] for row in trials if row["scenario"] == scenario),
              "trials": sum(row["scenario"] == scenario for row in trials)}
              for scenario in SCENARIOS},
          "limitations": ["Finite local calibration, not a performance SLO or regression verdict.",
                          "Python tracemalloc measures traced allocations, not total process RSS.",
                          "Concurrent processes on the Work host can influence elapsed time.",
                          "The failed exporter is a local HTTP503 fixture, not a remote service outage.",
                          "Measurements include tracing overhead equally in each operation workload."]}
DESTINATION.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"source_unchanged": report["source_unchanged"], "summary": report["summary"]}, indent=2))
